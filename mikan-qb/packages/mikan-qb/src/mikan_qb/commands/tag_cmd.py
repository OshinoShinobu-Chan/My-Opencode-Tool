"""tag 子命令：发布标题标签解析（调试）。"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from mikan_health.core.releases import parse_title
from mikan_qb.commands._shared import JsonFlag, _ctx, _emit, console

tag_app = typer.Typer(help="标题标签解析（调试）", no_args_is_help=True)


@tag_app.command("parse")
def tag_parse(
    ctx: typer.Context,
    title: Annotated[str, typer.Argument(help="要解析的发布标题")],
    json_output: JsonFlag = False,
) -> None:
    """解析发布标题中的字幕组、集数、语言等标签（调试用）。

    Args:
        title: 要解析的发布标题。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    parsed = parse_title(title, app_ctx.config.taxonomy)
    payload = parsed.__dict__ | {
        "languages": list(parsed.languages),
        "has_simplified": parsed.has_simplified,
        "has_traditional": parsed.has_traditional,
    }

    def render(data: dict) -> None:
        """渲染标题解析结果表。

        Args:
            data: 待渲染的字典。
        """
        table = Table(title="标题解析", show_header=False)
        table.add_column("字段", style="cyan")
        table.add_column("值")
        for key in (
            "subgroup_label",
            "episode_raw",
            "episode",
            "is_pack",
            "is_extra",
            "language",
            "language_source",
            "sub_type",
            "resolution",
            "codec",
            "bit_depth",
            "source_tag",
            "is_dual_zh_jp",
            "has_japanese",
        ):
            table.add_row(key, str(data.get(key)))
        table.add_row("raw", data["raw"])
        console.print(table)

    _emit(app_ctx, payload, render)
