"""status 命令：托管清单的订阅健康状况总览（feed/规则/进度/停滞）。"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from mikan_qb.commands._analysis import _audit_one, _policy_for
from mikan_qb.commands._format import _fmt_dt, _fmt_num
from mikan_qb.commands._shared import (
    JsonFlag,
    RefreshFlag,
    _bangumi,
    _ctx,
    _emit,
    _qb,
    console,
)
from mikan_qb.mikan import build_source
from mikan_qb.state import ManagedEntry, load_state

_STATUS_RANK = {
    "抓取失败": 0,
    "订阅缺失": 1,
    "feed 缺失": 2,
    "规则缺失": 3,
    "规则禁用": 4,
    "需关注": 5,
    "无可用字幕组": 6,
    "留意": 7,
    "正常": 8,
}


def _derive_status(audit: dict, feed_ok: bool, rule_ok: bool, rule_enabled: bool | None) -> str:
    """由 Mikan 侧巡检结论与 qB 侧订阅状态推导单行状态。

    Args:
        audit: 单番巡检结论（含 status）。
        feed_ok: qB 中是否存在对应 feed。
        rule_ok: qB 中是否存在对应规则。
        rule_enabled: 规则是否启用；None 表示规则不存在。
    """
    if audit.get("status") == "抓取失败":
        return "抓取失败"
    if not rule_ok:
        return "规则缺失" if feed_ok else "订阅缺失"
    if not feed_ok:
        return "feed 缺失"
    if rule_enabled is False:
        return "规则禁用"
    return str(audit.get("status") or "正常")


def _status_one(
    app_ctx,
    source,
    policy,
    feeds_by_url: dict[str, dict],
    rules: dict[str, dict],
    entry: ManagedEntry,
    stale_days: float,
    deep: int,
    full: bool,
    refresh: bool,
    bgm,
) -> dict:
    """汇总一条托管记录的 Mikan 进度与 qB feed/规则状态。

    Args:
        app_ctx: 应用上下文（配置与环境）。
        source: Mikan 数据源。
        policy: 分析策略 profile。
        feeds_by_url: qB feed 的 URL -> 行 映射。
        rules: qB 规则名 -> 规则定义 映射。
        entry: 托管记录。
        stale_days: 落后多少天算异常。
        deep: 历史深查前 N 名；0 表示不深查。
        full: 是否抓取各组完整发布列表（慢但更准）。
        refresh: 是否忽略缓存强制重新抓取。
        bgm: Bangumi 客户端；None 表示不启用。
    """
    audit = _audit_one(
        app_ctx,
        source,
        policy,
        entry.bangumi_id,
        entry.subgroup_id,
        entry.bangumi_title,
        stale_days,
        deep,
        None,
        page_only=not full,
        refresh=refresh,
        tracked=True,
        bgm=bgm,
    )
    feed = feeds_by_url.get(entry.feed_url)
    rule = rules.get(entry.rule_name)
    rule_ok = rule is not None
    rule_enabled = bool(rule.get("enabled", True)) if rule is not None else None
    feed_ok = feed is not None
    return {
        "bangumi_id": entry.bangumi_id,
        "title": entry.bangumi_title or audit.get("title") or str(entry.bangumi_id),
        "subgroup_id": entry.subgroup_id,
        "subgroup_name": entry.subgroup_name or audit.get("subgroup_name") or "-",
        "season": entry.season,
        "mode": entry.mode,
        "feed_path": feed["path"] if feed else (entry.feed_path or "-"),
        "feed_ok": feed_ok,
        "rule_name": entry.rule_name,
        "rule_ok": rule_ok,
        "rule_enabled": rule_enabled,
        "last_match": (rule or {}).get("lastMatch") or "",
        "last_episode": audit.get("last_episode"),
        "peer_max_episode": audit.get("peer_max_episode"),
        "progress": audit.get("progress"),
        "missing_vs_peer": audit.get("missing_vs_peer"),
        "peer_gap_days": audit.get("peer_gap_days"),
        "stale_days": audit.get("stale_days"),
        "tier": audit.get("tier"),
        "risk": audit.get("risk"),
        "status": _derive_status(audit, feed_ok, rule_ok, rule_enabled),
        "flags": list(audit.get("flags") or []),
    }


def _sort_key(row: dict) -> tuple:
    """按问题严重度、落后程度与标题排序，使异常项置顶。"""
    rank = _STATUS_RANK.get(row["status"], 9)
    return (rank, -(row["missing_vs_peer"] or 0), row["title"])


def _lag_cell(row: dict) -> str:
    """把落后集数与落后天数拼成单元格文本，无落后返回 '-'。"""
    parts: list[str] = []
    if row["missing_vs_peer"]:
        parts.append(f"{_fmt_num(row['missing_vs_peer'])} 集")
    if row["peer_gap_days"]:
        parts.append(f"{row['peer_gap_days']} 天")
    return " / ".join(parts) or "-"


def status_cmd(
    ctx: typer.Context,
    season: Annotated[
        str | None, typer.Option("--season", help="只看该季度的托管番剧，如 2026夏")
    ] = None,
    stale_days: Annotated[float, typer.Option("--stale-days", help="停滞多少天算异常")] = 10,
    mode: Annotated[str, typer.Option("--mode", help="fast | normal")] = "normal",
    deep: Annotated[int, typer.Option("--deep", help="历史深查前 N 名；0=不深查")] = 0,
    full: Annotated[
        bool, typer.Option("--full", help="抓取各组完整发布列表（慢，进度更准）")
    ] = False,
    use_bangumi: Annotated[
        bool, typer.Option("--bangumi/--no-bangumi", help="用 Bangumi 精确放送日程")
    ] = True,
    refresh: RefreshFlag = False,
    json_output: JsonFlag = False,
) -> None:
    """一屏查看托管番剧的 feed / 规则 / 进度与停滞情况。

    Args:
        season: 只看该季度的托管番剧；None 表示全部。
        stale_days: 停滞 / 落后多少天算异常。
        mode: 分析模式，fast | normal。
        deep: 历史深查前 N 名；0 表示不深查。
        full: 是否抓取各组完整发布列表。
        use_bangumi: 是否用 Bangumi 精确放送日程。
        refresh: 是否忽略缓存强制重新抓取。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    entries = [e for e in load_state().entries if not season or season in (e.season or "")]
    if not entries:
        console.print(
            "[yellow]没有可显示的托管项（清单为空或 --season 不匹配）。"
            "用 `mikan apply add` 纳管后再试[/yellow]"
        )
        return

    client = _qb(app_ctx)
    source = build_source(app_ctx.config, app_ctx.env)
    policy = _policy_for(app_ctx, mode)
    bgm = _bangumi(app_ctx, use_bangumi)
    feeds_by_url = {row["url"]: row for row in client.feeds() if row.get("url")}
    rules = client.rules()

    rows = [
        _status_one(
            app_ctx,
            source,
            policy,
            feeds_by_url,
            rules,
            entry,
            stale_days,
            deep,
            full,
            refresh,
            bgm,
        )
        for entry in entries
    ]
    rows.sort(key=_sort_key)
    payload = {"season": season, "stale_days": stale_days, "mode": mode, "entries": rows}

    def render(data: dict) -> None:
        """渲染托管订阅状态表。

        Args:
            data: 待渲染的字典。
        """
        counts: dict[str, int] = {}
        for row in data["entries"]:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        console.print(
            f"托管 {len(data['entries'])} 项："
            + "，".join(f"{name} {count}" for name, count in counts.items())
        )
        table = Table(title="订阅状态")
        table.add_column("状态", no_wrap=True)
        table.add_column("番剧", overflow="ellipsis", max_width=20, no_wrap=True)
        table.add_column("组", overflow="ellipsis", max_width=12, no_wrap=True)
        table.add_column("feed", justify="center", no_wrap=True)
        table.add_column("规则", justify="center", no_wrap=True)
        table.add_column("最后命中", no_wrap=True)
        table.add_column("落后", justify="right", no_wrap=True)
        table.add_column("停滞", justify="right", no_wrap=True)
        tones = {
            "正常": "green",
            "留意": "yellow",
            "规则禁用": "yellow",
            "需关注": "red",
            "规则缺失": "red",
            "feed 缺失": "red",
            "订阅缺失": "red",
            "抓取失败": "red",
            "无可用字幕组": "yellow",
        }
        for row in data["entries"]:
            tone = tones.get(row["status"], "yellow")
            table.add_row(
                f"[{tone}]{row['status']}[/]",
                row["title"],
                row["subgroup_name"],
                "[green]有[/green]" if row["feed_ok"] else "[red]无[/red]",
                "[green]启用[/green]"
                if row["rule_ok"] and row["rule_enabled"]
                else ("[yellow]停用[/yellow]" if row["rule_ok"] else "[red]无[/red]"),
                _fmt_dt(row["last_match"]),
                _lag_cell(row),
                f"{row['stale_days']} 天" if row["stale_days"] is not None else "-",
            )
        console.print(table)

    _emit(app_ctx, payload, render)
