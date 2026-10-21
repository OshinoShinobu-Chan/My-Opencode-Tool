"""cleanup 命令：清理陈旧订阅计划、终态换组任务与缓存数据。"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from mikan_qb.commands._shared import JsonFlag, _ctx, _emit, console, err_console
from mikan_qb.maintenance.cleanup import (
    DEFAULT_CACHE_DAYS,
    DEFAULT_JOB_DAYS,
    DEFAULT_PLAN_DAYS,
    apply_cleanup,
    scan_cleanup,
)
from mikan_qb.state import load_state

cleanup_app = typer.Typer(help="定期清理（默认 dry-run，需 --apply）", no_args_is_help=True)


@cleanup_app.command("run")
def cleanup_run(
    ctx: typer.Context,
    plan_days: Annotated[
        float, typer.Option("--plan-days", help="计划文件保留天数；0=不清计划")
    ] = DEFAULT_PLAN_DAYS,
    job_days: Annotated[
        float, typer.Option("--job-days", help="终态 job 保留天数；0=不清 job")
    ] = DEFAULT_JOB_DAYS,
    cache_days: Annotated[
        float, typer.Option("--cache-days", help="data 缓存保留天数；0=不清缓存")
    ] = DEFAULT_CACHE_DAYS,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="不清理 data/ 缓存")] = False,
    keep_managed: Annotated[
        bool, typer.Option("--keep-managed/--no-keep-managed", help="保护仍在用的计划")
    ] = True,
    apply: Annotated[bool, typer.Option("--apply", help="真正删除文件")] = False,
    json_output: JsonFlag = False,
) -> None:
    """清理陈旧计划 / 终态任务 / 缓存（默认 dry-run）。

    Args:
        plan_days: 计划文件保留天数；0 表示不清计划。
        job_days: 终态 job 保留天数；0 表示不清 job。
        cache_days: data 缓存保留天数；0 表示不清缓存。
        no_cache: 是否跳过 data/ 缓存清理。
        keep_managed: 是否保护仍被托管清单引用的计划。
        apply: 是否真正删除文件。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    plan = scan_cleanup(
        load_state(),
        plan_days=plan_days,
        job_days=job_days,
        cache_days=cache_days,
        include_cache=not no_cache,
        keep_managed=keep_managed,
    )

    if apply:
        counts = apply_cleanup(plan)
    else:
        counts = {
            "plans": len(plan.plans),
            "jobs": len(plan.jobs),
            "cache": len(plan.cache),
        }

    payload = {
        "applied": apply,
        "plan_days": plan_days,
        "job_days": job_days,
        "cache_days": cache_days,
        "kept_plans": plan.kept_plans,
        "counts": counts,
        "plans": [str(p) for p in plan.plans],
        "jobs": [str(p) for p in plan.jobs],
        "cache": [str(p) for p in plan.cache],
    }

    def render(data: dict) -> None:
        """渲染待清理 / 已清理清单。

        Args:
            data: 待渲染的字典。
        """
        head = "已清理" if data["applied"] else "dry-run"
        console.print(
            f"[bold]{head}[/bold] 计划 {data['counts']['plans']}，"
            f"job {data['counts']['jobs']}，缓存 {data['counts']['cache']}"
            f"（保留在用计划 {data['kept_plans']}）"
        )
        table = Table(title="待清理文件")
        table.add_column("类别", style="cyan", no_wrap=True)
        table.add_column("文件", overflow="ellipsis")
        for path in data["plans"]:
            table.add_row("计划", path)
        for path in data["jobs"]:
            table.add_row("job", path)
        for path in data["cache"]:
            table.add_row("缓存", path)
        if data["plans"] or data["jobs"] or data["cache"]:
            console.print(table)
        if not data["applied"]:
            err_console.print("[yellow]dry-run[/yellow] 加 --apply 才会真正删除")

    _emit(app_ctx, payload, render)
