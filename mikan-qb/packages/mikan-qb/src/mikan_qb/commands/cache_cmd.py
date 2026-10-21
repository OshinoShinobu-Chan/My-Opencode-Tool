"""cache 子命令：本地 HTTP 缓存的统计与清理。"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from mikan_health.data import HttpCache
from mikan_qb.commands._format import _age_hours, _fmt_age
from mikan_qb.commands._shared import JsonFlag, _ctx, _emit, console
from mikan_qb.mikan import cache_path

cache_app = typer.Typer(help="本地 HTTP 缓存", no_args_is_help=True)


@cache_app.command("stats")
def cache_stats(ctx: typer.Context, json_output: JsonFlag = False) -> None:
    """显示本地 HTTP 缓存的体积与新旧程度。

    Args:
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    stats = HttpCache(cache_path()).stats()
    payload = {
        "path": stats.path,
        "entries": stats.entries,
        "bytes": stats.bytes,
        "oldest_age_hours": _age_hours(stats.oldest),
        "newest_age_hours": _age_hours(stats.newest),
    }

    def render(data: dict) -> None:
        """渲染缓存统计表。

        Args:
            data: 待渲染的字典。
        """
        table = Table(title="HTTP 缓存", show_header=False)
        table.add_column("项", style="cyan")
        table.add_column("值")
        table.add_row("文件", data["path"])
        table.add_row("条目数", str(data["entries"]))
        table.add_row("体积", f"{data['bytes'] / 1024:.1f} KB")
        table.add_row("最旧", _fmt_age(data["oldest_age_hours"]))
        table.add_row("最新", _fmt_age(data["newest_age_hours"]))
        console.print(table)

    _emit(app_ctx, payload, render)


@cache_app.command("clear")
def cache_clear(
    ctx: typer.Context,
    older_than: Annotated[
        float | None, typer.Option("--older-than", help="仅清理 N 小时前的条目；省略则全清")
    ] = None,
    json_output: JsonFlag = False,
) -> None:
    """清理全部或指定时长之前的 HTTP 缓存。

    Args:
        older_than: 仅清理 N 小时前的条目；None 表示全清。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    removed = HttpCache(cache_path()).clear(older_than)
    payload = {"removed": removed, "older_than_hours": older_than}

    def render(data: dict) -> None:
        """打印清理条数。

        Args:
            data: 待渲染的字典。
        """
        console.print(f"[green]已清理 {data['removed']} 条缓存[/green]")

    _emit(app_ctx, payload, render)
