"""CLI 命令共享的上下文构建、客户端获取与错误处理。"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from mikan_health.data import BangumiDetail, MikanError
from mikan_qb import EXIT_BAD_PARAMS, EXIT_REMOTE_ERROR
from mikan_qb.config import ConfigError, effective_mikan_base
from mikan_qb.context import AppContext, GlobalOptions, build_context
from mikan_qb.mikan import build_bangumi_client, build_source
from mikan_qb.qb import QBClient, QBError

console = Console()
err_console = Console(stderr=True)

JsonFlag = Annotated[bool, typer.Option("--json", help="以 JSON 输出（供 agent 消费）")]
RefreshFlag = Annotated[bool, typer.Option("--refresh", help="忽略本地缓存，强制重新抓取")]


def _ctx(ctx: typer.Context, json_flag: bool = False) -> AppContext:
    """由 typer 上下文构建 AppContext；配置错误时以退出码 1 结束。

    Args:
        json_flag: 是否强制以 JSON 输出。
    """
    options: GlobalOptions = ctx.obj or GlobalOptions()
    options.json_output = options.json_output or json_flag
    try:
        return build_context(options)
    except ConfigError as exc:
        err_console.print(f"[red]配置错误：{exc}[/red]")
        raise typer.Exit(code=EXIT_BAD_PARAMS) from exc


def _emit(ctx: AppContext, payload: object, render=None) -> None:
    """按 json_output 决定输出 JSON 还是调用 render 渲染；render 缺省为不渲染。

    Args:
        payload: 待输出的数据。
        render: 非 JSON 模式下的渲染回调；None 表示不渲染。
    """
    if ctx.json_output:
        console.print_json(data=payload)
    elif render is not None:
        render(payload)


def _qb(app_ctx: AppContext) -> QBClient:
    """连接 qB 并返回客户端，失败时以退出码 2 结束。

    Args:
        app_ctx: 应用上下文（环境变量）。
    """
    try:
        client = QBClient(app_ctx.env)
        client.connect()
        return client
    except QBError as exc:
        err_console.print(f"[red]{exc}[/red]")
        if exc.hint:
            err_console.print(f"[yellow]提示[/yellow] {exc.hint}")
        raise typer.Exit(code=EXIT_REMOTE_ERROR) from exc


def _bangumi(app_ctx: AppContext, enabled: bool = True):
    """按开关与配置构建 BangumiClient；未启用时返回 None。

    Args:
        app_ctx: 应用上下文（配置与环境）。
        enabled: 是否允许启用 Bangumi 客户端。
    """
    if not enabled or not app_ctx.config.bangumi.enabled:
        return None
    return build_bangumi_client(app_ctx.config, app_ctx.env)


def _fail_mikan(exc: Exception) -> None:
    """打印 Mikan 错误及 URL 并以退出码 2 结束。

    Args:
        exc: 捕获到的 Mikan 异常。
    """
    err_console.print(f"[red]{exc}[/red]")
    hint = getattr(exc, "url", None)
    if hint:
        err_console.print(f"[yellow]URL[/yellow] {hint}")
    raise typer.Exit(code=EXIT_REMOTE_ERROR) from exc


def _load_bangumi(app_ctx: AppContext, bangumi_id: int, refresh: bool) -> BangumiDetail | None:
    """抓取番剧详情，失败时退出并返回 None。

    Args:
        app_ctx: 应用上下文（配置与环境）。
        bangumi_id: Mikan 番剧 id。
        refresh: 是否忽略缓存强制重新抓取。
    """
    source = build_source(app_ctx.config, app_ctx.env)
    try:
        return source.bangumi(bangumi_id, force=refresh)
    except MikanError as exc:
        _fail_mikan(exc)
        return None


def _abs_url(app_ctx: AppContext, path: str | None) -> str | None:
    """把相对路径补全为基于 Mikan 地址的绝对 URL。

    Args:
        app_ctx: 应用上下文（配置与环境）。
        path: 相对或绝对 URL；None 表示无。
    """
    if not path:
        return None
    if path.startswith("http"):
        return path
    return f"{effective_mikan_base(app_ctx.config, app_ctx.env)}{path}"
