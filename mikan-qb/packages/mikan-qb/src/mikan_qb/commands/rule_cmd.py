"""rule 子命令：离线模拟字幕组过滤规则。"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from mikan_health.data import MikanError
from mikan_qb.commands._analysis import _preview_one
from mikan_qb.commands._shared import (
    JsonFlag,
    RefreshFlag,
    _ctx,
    _emit,
    _fail_mikan,
    console,
)
from mikan_qb.mikan import build_source

rule_app = typer.Typer(help="规则离线模拟", no_args_is_help=True)


@rule_app.command("preview")
def rule_preview(
    ctx: typer.Context,
    bangumi_id: Annotated[int, typer.Argument(help="Mikan bangumi id")],
    subgroup: Annotated[
        int | None, typer.Option("--subgroup", help="仅预览某个字幕组；省略则预览全部")
    ] = None,
    mode: Annotated[str, typer.Option("--mode", help="fast | normal")] = "normal",
    page_only: Annotated[
        bool, typer.Option("--page-only", help="只用番剧页可见的最近条目（快，但不是全量）")
    ] = False,
    refresh: RefreshFlag = False,
    json_output: JsonFlag = False,
) -> None:
    """离线模拟各字幕组的过滤规则与漏集 / 多命中情况。

    Args:
        bangumi_id: Mikan bangumi id。
        subgroup: 仅预览该字幕组；None 表示全部。
        mode: 分析模式，fast | normal。
        page_only: 是否只用番剧页可见的最近条目。
        refresh: 是否忽略缓存强制重新抓取。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    source = build_source(app_ctx.config, app_ctx.env)
    try:
        detail = source.bangumi(bangumi_id, force=refresh)
        targets = (
            [subgroup] if subgroup is not None else [s.subtitle_group_id for s in detail.subgroups]
        )
        reports = []
        for sid in targets:
            if page_only:
                releases = [r for r in detail.releases if r.subtitle_group_id == sid]
            else:
                releases = source.subgroup_releases(bangumi_id, sid, force=refresh)
            section = detail.subgroup(sid)
            name = section.name if section else str(sid)
            reports.append(_preview_one(app_ctx, sid, name, releases, mode))
    except MikanError as exc:
        _fail_mikan(exc)
        return

    payload = {"bangumi_id": bangumi_id, "title": detail.title, "mode": mode, "reports": reports}

    def render(data: dict) -> None:
        """渲染规则离线模拟结果表。

        Args:
            data: 待渲染的字典。
        """
        ok_count = sum(1 for r in data["reports"] if r["valid"])
        table = Table(
            title=f"{data['title']} — 规则离线模拟（{ok_count}/{len(data['reports'])} 可用）"
        )
        table.add_column("sid", style="cyan", no_wrap=True)
        table.add_column("字幕组", overflow="ellipsis", max_width=16, no_wrap=True)
        table.add_column("选用版本", overflow="ellipsis", max_width=22, no_wrap=True)
        table.add_column("集数", justify="right", no_wrap=True)
        table.add_column("漏集", justify="right", no_wrap=True)
        table.add_column("多命中", justify="right", no_wrap=True)
        table.add_column("可用", justify="center", no_wrap=True)
        for row in data["reports"]:
            table.add_row(
                str(row["subgroup_id"]),
                row["subgroup_name"],
                row["variant_label"] or "-",
                str(row["episode_count"]),
                str(len(row["gaps"])),
                str(len(row["multiple"])),
                "[green]是[/green]" if row["valid"] else "[red]否[/red]",
            )
        console.print(table)

    _emit(app_ctx, payload, render)
