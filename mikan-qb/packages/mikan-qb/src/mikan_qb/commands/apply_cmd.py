"""apply 子命令：把计划写入 qB 并维护托管清单。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from mikan_qb.commands._plan import _do_backfill, _plan_path_of, _resolve_plan
from mikan_qb.commands._shared import JsonFlag, _ctx, _emit, _qb, console
from mikan_qb.maintenance.cleanup import auto_cleanup
from mikan_qb.rules import diff_plan
from mikan_qb.state import ManagedEntry, load_state, save_state

apply_app = typer.Typer(help="执行写入操作（默认 dry-run，需 --apply）", no_args_is_help=True)


@apply_app.command("add")
def apply_add(
    ctx: typer.Context,
    path: Annotated[Path | None, typer.Argument(help="计划文件")] = None,
    bangumi_id: Annotated[int | None, typer.Option("--bangumi", help="按番剧 id 查找计划")] = None,
    subgroup: Annotated[int | None, typer.Option("--subgroup", help="字幕组 id")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="真正写入 qB")] = False,
    backfill: Annotated[bool, typer.Option("--backfill", help="同时回填已播集数")] = False,
    paused: Annotated[bool, typer.Option("--paused", help="回填的种子以暂停状态添加")] = False,
    disabled: Annotated[
        bool, typer.Option("--disabled", help="规则以禁用状态写入（安全演练）")
    ] = False,
    json_output: JsonFlag = False,
) -> None:
    """把计划写入 qB：建分类、feed、规则并登记托管清单。

    Args:
        path: 计划文件路径；None 表示按番剧 / 字幕组查找。
        bangumi_id: 按番剧 id 查找计划。
        subgroup: 字幕组 id。
        apply: 是否真正写入 qB。
        backfill: 是否同时回填已播集数。
        paused: 回填的种子是否以暂停状态添加。
        disabled: 是否以禁用状态写入规则（安全演练）。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    plan = _resolve_plan(path, bangumi_id, subgroup)
    client = _qb(app_ctx)

    actions: list[dict] = []
    diff = diff_plan(plan, client)
    for row in diff["differences"]:
        if row["action"] == "unchanged":
            continue
        actions.append(row)
    if backfill:
        actions.append(
            {"item": "torrents", "action": "add", "detail": f"{len(plan.matches)} 个种子"}
        )

    payload = {"plan_file": str(_plan_path_of(plan)), "actions": actions, "applied": False}

    def render(data: dict) -> None:
        """渲染将执行的变更动作表。

        Args:
            data: 待渲染的字典。
        """
        if not data["actions"]:
            console.print("[green]qB 现状与计划一致，无需变更[/green]")
            return
        table = Table(title="将执行的动作")
        table.add_column("对象", style="cyan")
        table.add_column("动作")
        table.add_column("详情")
        for row in data["actions"]:
            table.add_row(row["item"], row["action"], row["detail"])
        console.print(table)

    _emit(app_ctx, payload, render)

    if not apply:
        console.print("[yellow]dry-run[/yellow] 加 --apply 才会写入 qB")
        return

    if client.repack_proper_enabled():
        console.print(
            "[yellow]注意[/yellow] rss_download_repack_proper_episodes 仍为开启，"
            "同集的重发版本会被重复下载。可用 "
            "`mikan qb rules repack-proper --set off --apply` 关闭。"
        )

    client.ensure_category(app_ctx.config.qb.category, app_ctx.config.qb.save_root)
    feeds = {row["path"]: row for row in client.feeds()}
    same_url_path = next((path for path, row in feeds.items() if row["url"] == plan.feed_url), None)
    feed_created = False
    if same_url_path is None:
        client.ensure_feed_parent(plan.feed_path)
        client.add_feed(plan.feed_url, plan.feed_path, plan.feed_refresh_interval)
        feed_created = True
        console.print(f"[green]已新建 feed[/green] {plan.feed_path}")
    else:
        console.print(
            f"[yellow]该 RSS URL 已存在（{same_url_path}），跳过新建 feed；"
            "规则按 URL 关联，不受影响[/yellow]"
        )

    rule_def = dict(plan.rule_def)
    if disabled:
        rule_def["enabled"] = False
    client.set_rule(plan.rule_name, rule_def)
    console.print(
        f"[green]已写入规则[/green] {plan.rule_name}" + ("（已禁用）" if disabled else "")
    )

    state = load_state()
    state.upsert(
        ManagedEntry(
            bangumi_id=plan.bangumi_id,
            subgroup_id=plan.subgroup_id,
            bangumi_title=plan.bangumi_title,
            subgroup_name=plan.subgroup_name,
            season=plan.season,
            mode=plan.mode,
            feed_path=plan.feed_path if feed_created else (same_url_path or plan.feed_path),
            feed_url=plan.feed_url,
            feed_created=feed_created,
            rule_name=plan.rule_name,
            tag=(plan.rule_def.get("torrentParams", {}).get("tags") or [None])[0],
            save_path=str(plan.rule_def.get("savePath") or ""),
        )
    )
    save_state(state)

    if backfill:
        _do_backfill(app_ctx, plan, client, paused=paused)
    removed = auto_cleanup(state)
    if removed:
        console.print(f"[dim]已顺带清理 {len(removed)} 个陈旧文件[/dim]")
    payload["applied"] = True


@apply_app.command("backfill")
def apply_backfill(
    ctx: typer.Context,
    path: Annotated[Path | None, typer.Argument(help="计划文件")] = None,
    bangumi_id: Annotated[int | None, typer.Option("--bangumi", help="按番剧 id 查找计划")] = None,
    subgroup: Annotated[int | None, typer.Option("--subgroup", help="字幕组 id")] = None,
    from_ep: Annotated[float | None, typer.Option("--from-ep", help="起始集")] = None,
    to_ep: Annotated[float | None, typer.Option("--to-ep", help="结束集")] = None,
    paused: Annotated[bool, typer.Option("--paused", help="以暂停状态添加")] = False,
    apply: Annotated[bool, typer.Option("--apply", help="真正写入 qB")] = False,
    json_output: JsonFlag = False,
) -> None:
    """回填计划中已播集数的种子（默认 dry-run）。

    Args:
        path: 计划文件路径；None 表示按番剧 / 字幕组查找。
        bangumi_id: 按番剧 id 查找计划。
        subgroup: 字幕组 id。
        from_ep: 起始集；None 表示不限。
        to_ep: 结束集；None 表示不限。
        paused: 是否以暂停状态添加。
        apply: 是否真正写入 qB。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    plan = _resolve_plan(path, bangumi_id, subgroup)
    client = _qb(app_ctx)
    matches = [
        m
        for m in plan.matches
        if (from_ep is None or m.episode >= from_ep) and (to_ep is None or m.episode <= to_ep)
    ]
    payload = {
        "episodes": [m.episode for m in matches],
        "count": len(matches),
        "applied": False,
    }
    if not apply:
        episodes = [m.episode for m in matches]
        console.print(f"[yellow]dry-run[/yellow] 将回填 {len(matches)} 集：{episodes}")
        _emit(app_ctx, payload)
        return

    _do_backfill(app_ctx, plan, client, paused=paused, matches=matches)
    payload["applied"] = True
    _emit(app_ctx, payload)


@apply_app.command("remove")
def apply_remove(
    ctx: typer.Context,
    bangumi_id: Annotated[int, typer.Option("--bangumi", help="番剧 id")],
    subgroup: Annotated[int | None, typer.Option("--subgroup", help="字幕组 id")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="真正执行")] = False,
    delete_files: Annotated[
        bool, typer.Option("--delete-files", help="同时删除已下载文件")
    ] = False,
    keep_feed: Annotated[bool, typer.Option("--keep-feed", help="保留 RSS feed，只删规则")] = False,
    delete_feed: Annotated[
        bool,
        typer.Option("--delete-feed", help="即使 feed 非本工具创建也一并删除（按 URL 匹配）"),
    ] = False,
    json_output: JsonFlag = False,
) -> None:
    """移除番剧的规则、feed 与种子，并更新托管清单。

    Args:
        bangumi_id: 番剧 id。
        subgroup: 字幕组 id。
        apply: 是否真正执行。
        delete_files: 是否同时删除已下载文件。
        keep_feed: 是否保留 RSS feed 只删规则。
        delete_feed: 是否即使 feed 非本工具创建也一并删除。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    plan = _resolve_plan(None, bangumi_id, subgroup)
    client = _qb(app_ctx)
    state = load_state()
    entry = state.find(bangumi_id, subgroup)
    tag = (entry.tag if entry else None) or (
        plan.rule_def.get("torrentParams", {}).get("tags") or [None]
    )[0]
    torrents = client.torrents_by_tag(tag) if tag else []
    owns_feed = entry.feed_created if entry else False
    feed_path = entry.feed_path if entry else plan.feed_path
    if not owns_feed and delete_feed:
        match = next(
            (row["path"] for row in client.feeds() if row.get("url") == plan.feed_url), None
        )
        if match:
            feed_path = match
            owns_feed = True
    feed_note = (
        f"feed {feed_path}（本工具创建）"
        if owns_feed
        else "保留 RSS feed（非本工具创建；需要一并删除请加 --delete-feed）"
    )

    payload = {
        "feed_path": feed_path,
        "rule_name": plan.rule_name,
        "tag": tag,
        "torrents": [t.name for t in torrents],
        "delete_files": delete_files,
        "owns_feed": owns_feed,
        "applied": False,
    }
    if not apply:
        console.print(
            f"[yellow]dry-run[/yellow] 将删除规则 {plan.rule_name}、{feed_note}"
            f"，并{'删除' if delete_files else '保留'} {len(torrents)} 个种子的文件"
        )
        _emit(app_ctx, payload)
        return

    client.remove_rule(plan.rule_name)
    if owns_feed and not keep_feed:
        client.remove_item(feed_path)
    if torrents:
        client.delete_torrents([t.hash for t in torrents], delete_files=delete_files)
    state.drop(bangumi_id, subgroup)
    save_state(state)
    payload["applied"] = True
    console.print(
        f"[green]已移除规则[/green] {plan.rule_name}"
        + (f"，并删除 feed {feed_path}" if owns_feed and not keep_feed else "")
        + (f"，删除 {len(torrents)} 个种子（delete_files={delete_files}）" if torrents else "")
    )
    _emit(app_ctx, payload)
