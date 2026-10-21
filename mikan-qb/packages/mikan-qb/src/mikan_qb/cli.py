"""mikan 命令行入口：组装各子命令应用并运行 typer。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from mikan_health.console import force_pipe_utf8
from mikan_qb import __version__
from mikan_qb.commands._shared import console
from mikan_qb.commands.anime_cmd import anime_app
from mikan_qb.commands.apply_cmd import apply_app
from mikan_qb.commands.cache_cmd import cache_app
from mikan_qb.commands.cleanup_cmd import cleanup_app
from mikan_qb.commands.config_cmd import config_app
from mikan_qb.commands.plan_cmd import plan_app
from mikan_qb.commands.qb_cmd import qb_app
from mikan_qb.commands.replace_cmd import replace_app
from mikan_qb.commands.rule_cmd import rule_app
from mikan_qb.commands.season_cmd import season_app
from mikan_qb.commands.state_cmd import state_app
from mikan_qb.commands.status_cmd import status_cmd
from mikan_qb.commands.subgroup_cmd import subgroup_app
from mikan_qb.commands.tag_cmd import tag_app
from mikan_qb.context import GlobalOptions

app = typer.Typer(
    name="mikan",
    help="Mikan 新番订阅与 qBittorrent RSS 管理",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(config_app, name="config")
app.add_typer(qb_app, name="qb")
app.add_typer(cache_app, name="cache")
app.add_typer(cleanup_app, name="cleanup")
app.add_typer(anime_app, name="anime")
app.add_typer(season_app, name="season")
app.add_typer(tag_app, name="tag")
app.add_typer(rule_app, name="rule")
app.add_typer(plan_app, name="plan")
app.add_typer(apply_app, name="apply")
app.add_typer(subgroup_app, name="subgroup")
app.add_typer(state_app, name="state")
app.add_typer(replace_app, name="replace")
app.command("status")(status_cmd)


def _version_callback(value: bool) -> None:
    """--version 回调：打印版本并退出。

    Args:
        value: --version 是否为真。
    """
    if value:
        console.print(f"mikan {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    ctx: typer.Context,
    config: Annotated[Path | None, typer.Option("--config", help="config.toml 路径")] = None,
    env: Annotated[Path | None, typer.Option("--env", help=".env 路径")] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="以 JSON 输出（供 agent 消费）")
    ] = False,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="输出调试信息")] = False,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="显示版本号并退出"
        ),
    ] = None,
) -> None:
    """把全局选项记录到 typer 上下文。

    Args:
        config: config.toml 路径。
        env: .env 路径。
        json_output: 是否以 JSON 输出。
        verbose: 是否输出调试信息。
        version: 是否显示版本号并退出。
    """
    ctx.obj = GlobalOptions(config=config, env=env, json_output=json_output, verbose=verbose)


def main() -> None:
    """CLI 入口：修正管道编码后运行 typer 应用。"""
    force_pipe_utf8()
    app()


if __name__ == "__main__":
    main()
