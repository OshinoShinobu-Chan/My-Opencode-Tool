"""plan 子命令：构建、查看与对比订阅计划。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from mikan_health.data import MikanError
from mikan_qb import EXIT_NEEDS_DECISION
from mikan_qb.analysis import load_schedule
from mikan_qb.commands._plan import _print_plan, _resolve_plan
from mikan_qb.commands._shared import (
    JsonFlag,
    RefreshFlag,
    _bangumi,
    _ctx,
    _emit,
    _qb,
    console,
    err_console,
)
from mikan_qb.mikan import build_source
from mikan_qb.rules import Plan, PlanError, build_plan, diff_plan, save_plan

plan_app = typer.Typer(help="订阅计划（dry-run）", no_args_is_help=True)


@plan_app.command("add")
def plan_add(
    ctx: typer.Context,
    bangumi_id: Annotated[int, typer.Argument(help="Mikan bangumi id")],
    subgroup: Annotated[int, typer.Option("--subgroup", help="字幕组 id")],
    mode: Annotated[str, typer.Option("--mode", help="fast | normal")] = "normal",
    season: Annotated[str | None, typer.Option("--season", help="季度标签，如 2026夏")] = None,
    save_path: Annotated[str | None, typer.Option("--save-path", help="覆盖保存路径")] = None,
    force: Annotated[bool, typer.Option("--force", help="即使模拟未通过也生成计划")] = False,
    use_bangumi: Annotated[
        bool, typer.Option("--bangumi/--no-bangumi", help="用 Bangumi 已播集数校验覆盖率")
    ] = True,
    refresh: RefreshFlag = False,
    json_output: JsonFlag = False,
) -> None:
    """为某字幕组构建并保存订阅计划。

    Args:
        bangumi_id: Mikan bangumi id。
        subgroup: 字幕组 id。
        mode: 分析模式，fast | normal。
        season: 季度标签，如 2026夏；None 表示自动推断。
        save_path: 覆盖保存路径；None 表示用默认模板。
        force: 是否即使模拟未通过也生成计划。
        use_bangumi: 是否用 Bangumi 已播集数校验覆盖率。
        refresh: 是否忽略缓存强制重新抓取。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    source = build_source(app_ctx.config, app_ctx.env)
    try:
        detail = source.bangumi(bangumi_id, force=refresh)
        releases = source.subgroup_releases(bangumi_id, subgroup, force=refresh)
        schedule = load_schedule(_bangumi(app_ctx, use_bangumi), detail)
        plan = build_plan(
            app_ctx.config,
            app_ctx.env,
            detail,
            releases,
            subgroup,
            mode,
            season=season,
            save_path_override=save_path,
            expected_episodes=schedule.aired_count(datetime.now()) if schedule else None,
        )
    except (MikanError, PlanError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=EXIT_NEEDS_DECISION) from exc

    path = save_plan(plan)
    payload = {"plan_file": str(path), **plan.model_dump(mode="json")}

    def render(data: dict) -> None:
        """打印计划文件路径与计划摘要。

        Args:
            data: 待渲染的字典。
        """
        console.print(f"[bold]计划已生成[/bold] {data['plan_file']}\n")
        _print_plan(Plan.model_validate(data))

    _emit(app_ctx, payload, render)
    if not plan.valid and not force:
        err_console.print(
            "[red]计划未生效：模拟未通过（漏集 / 多命中 / 覆盖率不足）。"
            "请用 `mikan rule preview` 查看细节，或加 --force 强制生成[/red]"
        )
        raise typer.Exit(code=EXIT_NEEDS_DECISION)


@plan_app.command("show")
def plan_show(
    ctx: typer.Context,
    path: Annotated[Path | None, typer.Argument(help="计划文件")] = None,
    bangumi_id: Annotated[int | None, typer.Option("--bangumi", help="按番剧 id 查找计划")] = None,
    subgroup: Annotated[int | None, typer.Option("--subgroup", help="字幕组 id")] = None,
    json_output: JsonFlag = False,
) -> None:
    """显示已保存的订阅计划。

    Args:
        path: 计划文件路径；None 表示按番剧 / 字幕组查找。
        bangumi_id: 按番剧 id 查找计划。
        subgroup: 字幕组 id。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    plan = _resolve_plan(path, bangumi_id, subgroup)
    payload = plan.model_dump(mode="json")

    def render(data: dict) -> None:
        """打印订阅计划摘要。

        Args:
            data: 待渲染的字典。
        """
        _print_plan(Plan.model_validate(data))

    _emit(app_ctx, payload, render)


@plan_app.command("diff")
def plan_diff(
    ctx: typer.Context,
    path: Annotated[Path | None, typer.Argument(help="计划文件")] = None,
    bangumi_id: Annotated[int | None, typer.Option("--bangumi", help="按番剧 id 查找计划")] = None,
    subgroup: Annotated[int | None, typer.Option("--subgroup", help="字幕组 id")] = None,
    json_output: JsonFlag = False,
) -> None:
    """对比订阅计划与 qB 现状。

    Args:
        path: 计划文件路径；None 表示按番剧 / 字幕组查找。
        bangumi_id: 按番剧 id 查找计划。
        subgroup: 字幕组 id。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    plan = _resolve_plan(path, bangumi_id, subgroup)
    client = _qb(app_ctx)
    payload = diff_plan(plan, client)

    def render(data: dict) -> None:
        """渲染与 qB 现状的差异表。

        Args:
            data: 待渲染的差异数据。
        """
        console.print(f"feed: {data['feed_path']}")
        console.print(f"rule: {data['rule_name']}\n")
        table = Table(title="与 qB 现状对比")
        table.add_column("对象", style="cyan")
        table.add_column("动作")
        table.add_column("详情")
        for row in data["differences"]:
            table.add_row(row["item"], row["action"], row["detail"])
        console.print(table)

    _emit(app_ctx, payload, render)
