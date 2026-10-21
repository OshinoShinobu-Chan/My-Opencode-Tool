"""qb 子命令：连接检查、feed 浏览与 RSS 规则管理。"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table
from rich.text import Text

from mikan_qb import EXIT_BAD_PARAMS, EXIT_REMOTE_ERROR
from mikan_qb.commands._format import _walk_feeds
from mikan_qb.commands._shared import JsonFlag, _ctx, _emit, _qb, console, err_console
from mikan_qb.qb import QBClient, QBError

qb_app = typer.Typer(help="qBittorrent 交互", no_args_is_help=True)
qb_rules_app = typer.Typer(help="qB 中的规则增删查", no_args_is_help=True)
qb_app.add_typer(qb_rules_app, name="rules")


@qb_app.command("check")
def qb_check(
    ctx: typer.Context,
    show_global: Annotated[bool, typer.Option("--show-global", help="打印 rss_* 全局设置")] = False,
    json_output: JsonFlag = False,
) -> None:
    """测试 qB 连接并返回版本、feed 与规则数量。

    Args:
        show_global: 是否打印 rss_* 全局设置。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    try:
        client = QBClient(app_ctx.env)
        status = client.connect()
    except QBError as exc:
        payload = {"ok": False, "host": app_ctx.env.qb_host, "error": str(exc), "hint": exc.hint}
        if app_ctx.json_output:
            console.print_json(data=payload)
        else:
            err_console.print(f"[red]{exc}[/red]")
            if exc.hint:
                err_console.print(f"[yellow]提示[/yellow] {exc.hint}")
        raise typer.Exit(code=EXIT_REMOTE_ERROR) from exc

    payload = {
        "ok": True,
        "host": status.host,
        "version": status.version,
        "webapi_version": status.webapi_version,
        "feeds": status.feed_count,
        "rules": status.rule_count,
        "rss_settings": status.rss_settings if show_global else {},
    }

    def render(data: dict) -> None:
        """渲染 qB 连接状态表。

        Args:
            data: 待渲染的字典。
        """
        table = Table(title="qBittorrent 连接")
        table.add_column("项", style="cyan")
        table.add_column("值")
        table.add_row("地址", data["host"])
        table.add_row("版本", data["version"])
        table.add_row("WebAPI 版本", data["webapi_version"])
        table.add_row("RSS feeds", str(data["feeds"]))
        table.add_row("RSS 规则", str(data["rules"]))
        console.print(table)
        if show_global and data["rss_settings"]:
            prefs = Table(title="rss_* 全局设置")
            prefs.add_column("项", style="cyan")
            prefs.add_column("值")
            for key, value in sorted(data["rss_settings"].items()):
                prefs.add_row(key, str(value))
            console.print(prefs)

    _emit(app_ctx, payload, render)


@qb_app.command("feeds")
def qb_feeds(
    ctx: typer.Context,
    with_data: Annotated[bool, typer.Option("--with-data", help="包含 feed 内容")] = False,
    json_output: JsonFlag = False,
) -> None:
    """列出 qB 中的 RSS 目录与订阅。

    Args:
        with_data: 是否包含 feed 内容。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    try:
        client = QBClient(app_ctx.env)
        client.connect()
        items = client.raw.rss_items(with_data=with_data)
    except QBError as exc:
        err_console.print(f"[red]{exc}[/red]")
        if exc.hint:
            err_console.print(f"[yellow]提示[/yellow] {exc.hint}")
        raise typer.Exit(code=EXIT_REMOTE_ERROR) from exc

    rows = list(_walk_feeds(items))

    def render(data: list[dict]) -> None:
        """渲染 RSS 目录 / feed 树表。

        Args:
            data: 待渲染的列表。
        """
        if not data:
            console.print("[yellow]RSS 中暂无 feed[/yellow]")
            return
        table = Table(title=f"RSS 树（{sum(1 for r in data if r['type'] == 'feed')} 个 feed）")
        table.add_column("类型", no_wrap=True)
        table.add_column("路径", style="cyan", overflow="ellipsis", max_width=42)
        table.add_column("URL", overflow="ellipsis", max_width=52)
        for row in data:
            table.add_row(
                "feed" if row["type"] == "feed" else "dir",
                row["path"],
                row["url"] or "",
            )
        console.print(table)

    _emit(app_ctx, rows, render)


@qb_rules_app.command("list")
def rules_list(ctx: typer.Context, json_output: JsonFlag = False) -> None:
    """列出 qB 中的 RSS 自动下载规则。

    Args:
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    client = _qb(app_ctx)
    rules = client.rules()
    payload = rules

    def render(data: dict) -> None:
        """渲染 RSS 自动下载规则表。

        Args:
            data: 待渲染的字典。
        """
        if not data:
            console.print("[yellow]暂无规则[/yellow]")
            return
        table = Table(title=f"RSS 自动下载规则（{len(data)} 条）")
        table.add_column("规则名", style="cyan")
        table.add_column("启用", justify="center")
        table.add_column("feeds", justify="right")
        table.add_column("mustContain")
        table.add_column("mustNotContain")
        table.add_column("savePath", overflow="ellipsis", max_width=26)
        for name, rule in sorted(data.items()):
            table.add_row(
                str(name),
                "是" if rule.get("enabled") else "否",
                str(len(rule.get("affectedFeeds") or [])),
                Text(str(rule.get("mustContain") or "")[:40] or "-"),
                Text(str(rule.get("mustNotContain") or "")[:40] or "-"),
                str(rule.get("savePath") or "-"),
            )
        console.print(table)

    _emit(app_ctx, payload, render)


@qb_rules_app.command("show")
def rules_show(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="规则名")],
    json_output: JsonFlag = False,
) -> None:
    """打印单条规则的完整定义。

    Args:
        name: 规则名。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    client = _qb(app_ctx)
    rule = client.rule(name)
    if rule is None:
        err_console.print(f"[red]规则不存在：{name}[/red]")
        raise typer.Exit(code=EXIT_BAD_PARAMS)
    console.print_json(data=rule)


@qb_rules_app.command("remove")
def rules_remove(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="规则名")],
    apply: Annotated[bool, typer.Option("--apply", help="真正执行删除")] = False,
    json_output: JsonFlag = False,
) -> None:
    """删除规则（默认 dry-run，需 --apply）。

    Args:
        name: 规则名。
        apply: 是否真正执行删除。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    client = _qb(app_ctx)
    if client.rule(name) is None:
        err_console.print(f"[red]规则不存在：{name}[/red]")
        raise typer.Exit(code=EXIT_BAD_PARAMS)
    if not apply:
        console.print(f"[yellow]dry-run[/yellow] 将删除规则 {name}（加 --apply 执行）")
        return
    client.remove_rule(name)
    console.print(f"[green]已删除规则[/green] {name}")


@qb_rules_app.command("repack-proper")
def rules_repack_proper(
    ctx: typer.Context,
    state: Annotated[str, typer.Option("--set", help="off | on")] = "off",
    apply: Annotated[bool, typer.Option("--apply", help="真正修改 qB 全局设置")] = False,
    json_output: JsonFlag = False,
) -> None:
    """开关 qB 的 repack/proper 自动下载（默认 dry-run）。

    Args:
        state: 目标开关，off | on。
        apply: 是否真正修改 qB 全局设置。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    client = _qb(app_ctx)
    desired = state.strip().lower() not in ("off", "false", "0", "no")
    current = client.repack_proper_enabled()
    payload = {"current": current, "desired": desired, "applied": False}
    if current == desired:
        console.print(f"[green]rss_download_repack_proper_episodes 已是 {desired}[/green]")
        _emit(app_ctx, payload)
        return
    if not apply:
        console.print(
            f"[yellow]dry-run[/yellow] 将把 rss_download_repack_proper_episodes "
            f"由 {current} 改为 {desired}（加 --apply 执行）"
        )
        _emit(app_ctx, payload)
        return
    client.set_preferences({"rss_download_repack_proper_episodes": desired})
    payload["applied"] = True
    console.print(f"[green]已设置[/green] rss_download_repack_proper_episodes = {desired}")
    _emit(app_ctx, payload)


@qb_rules_app.command("match")
def rules_match(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="规则名")],
    json_output: JsonFlag = False,
) -> None:
    """列出规则当前命中的文章标题。

    Args:
        name: 规则名。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    client = _qb(app_ctx)
    if client.rule(name) is None:
        err_console.print(f"[red]规则不存在：{name}[/red]")
        raise typer.Exit(code=EXIT_BAD_PARAMS)
    matched = client.matching_articles(name)
    total = sum(len(v) for v in matched.values()) if isinstance(matched, dict) else 0
    payload = {"rule_name": name, "feeds": matched, "total": total}

    def render(data: dict) -> None:
        """按 feed 渲染规则命中的标题表。

        Args:
            data: 待渲染的字典。
        """
        console.print(f"规则 [cyan]{data['rule_name']}[/cyan] 当前命中 {data['total']} 条")
        for feed, titles in data["feeds"].items():
            table = Table(title=feed)
            table.add_column("标题", overflow="ellipsis", max_width=96)
            for title in titles:
                table.add_row(Text(title))
            console.print(table)

    _emit(app_ctx, payload, render)
