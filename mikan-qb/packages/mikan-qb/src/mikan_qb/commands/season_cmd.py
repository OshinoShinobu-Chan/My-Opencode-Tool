"""season 子命令：按季度列出 Mikan 番组。"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from mikan_health.data import MikanError
from mikan_qb.commands._shared import (
    JsonFlag,
    RefreshFlag,
    _ctx,
    _emit,
    _fail_mikan,
    console,
)
from mikan_qb.mikan import build_source

season_app = typer.Typer(help="季度番剧列表", no_args_is_help=True)


@season_app.command("list")
def season_list(
    ctx: typer.Context,
    year: Annotated[int, typer.Option("--year", help="年份，如 2026")],
    season: Annotated[
        str, typer.Option("--season", help="季度：冬/春/夏/秋 或 winter/spring/summer/fall")
    ] = "夏",
    weekday: Annotated[
        int | None, typer.Option("--weekday", help="仅看星期几（0=周日 … 6=周六）")
    ] = None,
    refresh: RefreshFlag = False,
    json_output: JsonFlag = False,
) -> None:
    """列出某季度番组，可按星期过滤。

    Args:
        year: 年份，如 2026。
        season: 季度：冬/春/夏/秋 或 winter/spring/summer/fall。
        weekday: 仅看星期几（0=周日 … 6=周六）；None 表示不限。
        refresh: 是否忽略缓存强制重新抓取。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    source = build_source(app_ctx.config, app_ctx.env)
    try:
        entries = source.season(year, season, force=refresh)
    except (MikanError, ValueError) as exc:
        _fail_mikan(exc)
        return
    if weekday is not None:
        entries = [e for e in entries if e.weekday == weekday]

    payload = [e.model_dump(mode="json") for e in entries]

    def render(data: list[dict]) -> None:
        """渲染季度番组表。

        Args:
            data: 待渲染的列表。
        """
        table = Table(title=f"{year}{season} 季番组（{len(data)} 部）")
        table.add_column("bangumi_id", style="cyan")
        table.add_column("星期")
        table.add_column("标题")
        table.add_column("最近更新")
        for row in data:
            table.add_row(
                str(row["bangumi_id"]),
                row["weekday_cn"] or "-",
                row["title"],
                row["last_update"] or "-",
            )
        console.print(table)

    _emit(app_ctx, payload, render)
