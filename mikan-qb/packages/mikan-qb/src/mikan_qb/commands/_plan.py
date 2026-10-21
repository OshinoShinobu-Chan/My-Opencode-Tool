"""订阅计划的读取、展示与种子回填辅助。"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table
from rich.text import Text

from mikan_qb import EXIT_BAD_PARAMS
from mikan_qb.commands._shared import console, err_console
from mikan_qb.context import AppContext
from mikan_qb.qb import QBClient
from mikan_qb.rules import Plan, find_plan, load_plan


def _do_backfill(
    app_ctx: AppContext,
    plan: Plan,
    client: QBClient,
    paused: bool,
    matches: list | None = None,
) -> None:
    """按计划（或指定集）批量添加种子链接。

    Args:
        app_ctx: 应用上下文（配置与环境）。
        plan: 订阅计划。
        client: 已连接的 qB 客户端。
        paused: 是否以暂停状态添加种子。
        matches: 指定回填的命中项；None 表示用计划的全部命中。
    """
    selected = matches if matches is not None else plan.matches
    urls: list[str] = []
    for match in selected:
        url = match.torrent_url or match.magnet
        if url:
            urls.append(url)
    if not urls:
        console.print("[yellow]没有可回填的种子（计划里缺少 torrent/magnet 链接）[/yellow]")
        return
    tags = plan.rule_def.get("torrentParams", {}).get("tags") or []
    client.add_torrents(
        urls,
        save_path=plan.rule_def.get("savePath") or "",
        category=app_ctx.config.qb.category,
        tags=[t for t in tags if t],
        paused=paused,
    )
    console.print(f"[green]已回填 {len(urls)} 个种子[/green]（paused={paused}）")


def _print_plan(plan: Plan) -> None:
    """以表格打印订阅计划摘要。

    Args:
        plan: 待展示的订阅计划。
    """
    table = Table(title=f"{plan.bangumi_title} — 计划", show_header=False)
    table.add_column("项", style="cyan")
    table.add_column("值")
    table.add_row("番剧", f"{plan.bangumi_title}（{plan.bangumi_id}）")
    table.add_row("字幕组", f"{plan.subgroup_name}（{plan.subgroup_id}）")
    table.add_row("季度 / 模式", f"{plan.season} / {plan.mode}")
    table.add_row("选用版本", " + ".join(v.label for v in plan.variants))
    table.add_row("feed 路径", plan.feed_path)
    table.add_row("feed URL", plan.feed_url)
    table.add_row("规则名", plan.rule_name)
    table.add_row("savePath", str(plan.rule_def.get("savePath")))
    table.add_row("tags", ", ".join(plan.rule_def.get("torrentParams", {}).get("tags") or []))
    table.add_row("mustContain", Text(plan.rule_def.get("mustContain") or "(空)"))
    table.add_row("mustNotContain", Text(plan.rule_def.get("mustNotContain") or "(空)"))
    table.add_row("命中集数", str(len(plan.matches)))
    table.add_row("漏集", str(plan.gaps) if plan.gaps else "无")
    table.add_row("模拟结论", "[green]通过[/green]" if plan.valid else "[red]未通过[/red]")
    console.print(table)
    for warning in plan.warnings:
        console.print(f"[yellow]警告[/yellow] {warning}")


def _plan_path_of(plan: Plan) -> Path:
    """由计划推导其默认文件路径。

    Args:
        plan: 订阅计划。
    """
    from mikan_qb.rules import plan_file

    return plan_file(plan.bangumi_id, plan.subgroup_id)


def _resolve_plan(path: Path | None, bangumi_id: int | None, subgroup: int | None) -> Plan:
    """从文件路径或番剧 / 字幕组定位计划，失败时退出。

    Args:
        path: 计划文件路径；None 表示改用番剧 / 字幕组查找。
        bangumi_id: 番剧 id；path 为 None 时必填。
        subgroup: 字幕组 id；None 表示匹配该番剧的任意计划。
    """
    if path is not None:
        try:
            return load_plan(path)
        except (OSError, ValueError) as exc:
            err_console.print(f"[red]读取计划失败：{exc}[/red]")
            raise typer.Exit(code=EXIT_BAD_PARAMS) from exc
    if bangumi_id is None:
        err_console.print("[red]需要提供计划文件路径，或 --bangumi（可加 --subgroup）[/red]")
        raise typer.Exit(code=EXIT_BAD_PARAMS)
    plan = find_plan(bangumi_id, subgroup)
    if plan is None:
        err_console.print(
            f"[red]找不到计划：bangumi={bangumi_id} subgroup={subgroup}"
            "（先运行 `mikan plan add`）[/red]"
        )
        raise typer.Exit(code=EXIT_BAD_PARAMS)
    return plan
