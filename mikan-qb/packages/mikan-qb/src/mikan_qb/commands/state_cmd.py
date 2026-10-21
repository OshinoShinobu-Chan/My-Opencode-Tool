"""state 子命令：查看托管清单。"""

from __future__ import annotations

import typer
from rich.table import Table

from mikan_qb.commands._shared import JsonFlag, _ctx, _emit, console
from mikan_qb.state import load_state

state_app = typer.Typer(help="托管清单", no_args_is_help=True)


@state_app.command("list")
def state_list(ctx: typer.Context, json_output: JsonFlag = False) -> None:
    """列出托管清单中的番剧。

    Args:
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    state = load_state()
    payload = [e.model_dump(mode="json") for e in state.entries]

    def render(data: list[dict]) -> None:
        """渲染托管清单表。

        Args:
            data: 待渲染的列表。
        """
        if not data:
            console.print("[yellow]托管清单为空[/yellow]")
            return
        table = Table(title=f"托管清单（{len(data)} 项）")
        table.add_column("bangumi_id", style="cyan", no_wrap=True)
        table.add_column("番剧", overflow="ellipsis", max_width=26, no_wrap=True)
        table.add_column("字幕组", overflow="ellipsis", max_width=16, no_wrap=True)
        table.add_column("季度", no_wrap=True)
        table.add_column("模式", no_wrap=True)
        table.add_column("tag", no_wrap=True)
        for row in data:
            table.add_row(
                str(row["bangumi_id"]),
                row["bangumi_title"],
                row["subgroup_name"],
                row["season"],
                row["mode"],
                row["tag"] or "-",
            )
        console.print(table)

    _emit(app_ctx, payload, render)
