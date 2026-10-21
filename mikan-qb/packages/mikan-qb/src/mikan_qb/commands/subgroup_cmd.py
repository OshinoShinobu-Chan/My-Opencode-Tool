"""subgroup 子命令：字幕组评分与停更巡检。"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from mikan_health.data import MikanError
from mikan_qb import EXIT_BAD_PARAMS, EXIT_NEEDS_DECISION
from mikan_qb.analysis import DEEP_DEFAULT, build_report, build_subgroup_report
from mikan_qb.commands._analysis import _audit_one, _policy_for, _ranking_payload
from mikan_qb.commands._format import _fmt_days, _fmt_hours, _fmt_lag, _fmt_num
from mikan_qb.commands._shared import (
    JsonFlag,
    RefreshFlag,
    _bangumi,
    _ctx,
    _emit,
    _fail_mikan,
    _load_bangumi,
    console,
    err_console,
)
from mikan_qb.mikan import build_source
from mikan_qb.state import load_state

subgroup_app = typer.Typer(help="字幕组健康度（更新速度 / 停更风险）", no_args_is_help=True)


@subgroup_app.command("stats")
def subgroup_stats_cmd(
    ctx: typer.Context,
    subgroup_id: Annotated[
        int | None, typer.Argument(help="字幕组 id（RSS 的 subgroupid）")
    ] = None,
    bangumi: Annotated[
        int | None, typer.Option("--bangumi", help="从该番剧页解析出版本组信息")
    ] = None,
    publish_group: Annotated[
        int | None, typer.Option("--publish-group", help="直接指定作品年表 id")
    ] = None,
    name: Annotated[str | None, typer.Option("--name", help="按名称模糊检索字幕组")] = None,
    works: Annotated[
        int | None, typer.Option("--works", help="回看的作品数（默认取 profile.history_works）")
    ] = None,
    refresh: RefreshFlag = False,
    json_output: JsonFlag = False,
) -> None:
    """汇总单个字幕组的历史作品与停更风险。

    Args:
        subgroup_id: 字幕组 id（RSS 的 subgroupid）；None 表示待检索。
        bangumi: 从该番剧页解析出版本组信息；None 表示不用。
        publish_group: 直接指定作品年表 id；None 表示自动查找。
        name: 按名称模糊检索字幕组；None 表示不检索。
        works: 回看的作品数；None 取 profile.history_works。
        refresh: 是否忽略缓存强制重新抓取。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    source = build_source(app_ctx.config, app_ctx.env)

    if name and subgroup_id is None:
        matches = source.index.search(name) if source.index else []
        if not matches:
            err_console.print(f"[red]索引里找不到名称含 {name!r} 的字幕组[/red]")
            raise typer.Exit(code=EXIT_BAD_PARAMS)
        if len(matches) > 1:
            console.print("[yellow]匹配到多个字幕组，请用 sid 指定：[/yellow]")
            for sid, matched_name, _ in matches[:20]:
                console.print(f"  {sid}  {matched_name}")
            raise typer.Exit(code=EXIT_NEEDS_DECISION)
        subgroup_id, _, publish_group = matches[0][0], None, matches[0][2]

    if subgroup_id is None:
        err_console.print("[red]需要提供字幕组 id，或用 --name 检索[/red]")
        raise typer.Exit(code=EXIT_BAD_PARAMS)

    if publish_group is None and bangumi is not None:
        detail = _load_bangumi(app_ctx, bangumi, refresh)
        if detail is None:
            return
        subgroup = detail.subgroup(subgroup_id)
        if subgroup is None:
            err_console.print(f"[red]番剧 {bangumi} 下没有字幕组 {subgroup_id}[/red]")
            raise typer.Exit(code=EXIT_BAD_PARAMS)
        publish_group = subgroup.publish_group_id
    if publish_group is None and source.index is not None:
        found = source.index.lookup(subgroup_id)
        publish_group = found[1] if found else None

    policy = _policy_for(app_ctx, "normal")
    try:
        report = build_subgroup_report(
            source,
            subgroup_id,
            policy,
            publish_group_id=publish_group,
            works=works,
            force=refresh,
        )
    except MikanError as exc:
        _fail_mikan(exc)
        return

    risk = report["risk"]
    payload = {
        "subgroup_id": subgroup_id,
        "publish_group_id": publish_group,
        "works_total": report["works_total"],
        "works_judged": report["works_judged"],
        "seasons": report["seasons"],
        "last_release_days": report["last_release_days"],
        "accepts_new_season": report["accepts_new_season"],
        "median_rel_p50_h": report["median_rel_p50_h"],
        "risk": {
            "risk": risk.risk,
            "level": risk.level,
            "tier": risk.tier,
            "confidence": risk.confidence,
            "drop_rate": risk.drop_rate,
            "evidence": risk.evidence,
        },
        "records": [
            {
                "bangumi_id": r.bangumi_id,
                "title": r.title,
                "season": r.season_date,
                "group_final": r.group_final_episode,
                "fact_final": r.fact_final_episode,
                "rel_p50_h": r.rel_p50_h,
                "group_last_release": r.group_last_release.isoformat()
                if r.group_last_release
                else None,
                "dropped": verdict,
            }
            for r, verdict in zip(report["records"], report["verdicts"], strict=True)
        ],
    }

    def render(data: dict) -> None:
        """渲染字幕组健康度与回看作品表。

        Args:
            data: 待渲染的字典。
        """
        head = Table(title=f"字幕组 {subgroup_id} 健康度", show_header=False)
        head.add_column("项", style="cyan")
        head.add_column("值")
        head.add_row("publish_group_id", str(data["publish_group_id"]))
        head.add_row("回看作品数", f"{data['works_total']}（可判定 {data['works_judged']}）")
        head.add_row("最近发布", _fmt_days(data["last_release_days"]))
        head.add_row("仍在接新番", "是" if data["accepts_new_season"] else "否")
        head.add_row(
            "跨作品中位相对延迟",
            _fmt_hours(data["median_rel_p50_h"]),
        )
        risk = data["risk"]
        tone = {"low": "green", "mid": "yellow"}.get(risk["level"], "red")
        head.add_row(
            "停更风险",
            f"[{tone}]{risk['level']} / tier {risk['tier']} / 置信度 {risk['confidence']}[/]"
            + (f"  risk={risk['risk']:.3f}" if risk["risk"] is not None else ""),
        )
        console.print(head)
        for item in risk["evidence"]:
            console.print(f"  · {item}")

        table = Table(title="回看作品")
        table.add_column("bangumi_id", style="cyan", no_wrap=True)
        table.add_column("作品", overflow="ellipsis", max_width=30, no_wrap=True)
        table.add_column("季度", no_wrap=True)
        table.add_column("该组", justify="right", no_wrap=True)
        table.add_column("他组最高", justify="right", no_wrap=True)
        table.add_column("相对延迟", justify="right", no_wrap=True)
        table.add_column("弃番", justify="center", no_wrap=True)
        for row in data["records"]:
            table.add_row(
                str(row["bangumi_id"]),
                row["title"],
                row["season"] or "-",
                _fmt_num(row["group_final"]),
                _fmt_num(row["fact_final"]),
                _fmt_hours(row["rel_p50_h"], day=True),
                (
                    "[red]是[/red]"
                    if row["dropped"] is True
                    else ("?" if row["dropped"] is None else "否")
                ),
            )
        console.print(table)

    _emit(app_ctx, payload, render)


@subgroup_app.command("rank")
def subgroup_rank(
    ctx: typer.Context,
    bangumi: Annotated[int, typer.Argument(help="Mikan bangumi id")],
    mode: Annotated[str, typer.Option("--mode", help="fast | normal")] = "normal",
    deep: Annotated[
        int, typer.Option("--deep", help="对评分前 N 名做历史深查；0=不深查，-1=全部")
    ] = DEEP_DEFAULT,
    works: Annotated[
        int | None,
        typer.Option("--works", help="历史深查回看的作品数（默认取 profile.history_works）"),
    ] = None,
    page_only: Annotated[
        bool, typer.Option("--page-only", help="只用番剧页窗口，不抓完整历史（快）")
    ] = False,
    explain: Annotated[bool, typer.Option("--explain", help="打印每个候选的判定依据")] = False,
    use_bangumi: Annotated[
        bool, typer.Option("--bangumi/--no-bangumi", help="用 Bangumi 精确放送日程")
    ] = True,
    refresh: RefreshFlag = False,
    json_output: JsonFlag = False,
) -> None:
    """对番剧的候选字幕组打分排名。

    Args:
        bangumi: Mikan bangumi id。
        mode: 分析模式，fast | normal。
        deep: 对评分前 N 名做历史深查；0=不深查，-1=全部。
        works: 历史深查回看的作品数；None 取 profile.history_works。
        page_only: 是否只用番剧页窗口不抓完整历史。
        explain: 是否打印每个候选的判定依据。
        use_bangumi: 是否用 Bangumi 精确放送日程。
        refresh: 是否忽略缓存强制重新抓取。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    source = build_source(app_ctx.config, app_ctx.env)
    policy = _policy_for(app_ctx, mode)
    bgm = _bangumi(app_ctx, use_bangumi)
    try:
        ranking, detail = build_report(
            source,
            bangumi,
            policy,
            app_ctx.config.taxonomy,
            deep=deep,
            works=works,
            page_only=page_only,
            force=refresh,
            bgm=bgm,
        )
    except MikanError as exc:
        _fail_mikan(exc)
        return

    payload = _ranking_payload(ranking, detail)

    def render(data: dict) -> None:
        """渲染候选字幕组排名表。

        Args:
            data: 待渲染的字典。
        """
        console.print(
            f"[bold]{data['title']}[/bold]  mode={data['mode']}  "
            f"候选 {len(data['candidates'])} / 剔除 {len(data['rejected'])}"
        )
        if data["recommendation"]:
            console.print(f"[green]推荐[/green] {data['recommendation']['line']}")
        table = Table(title="候选字幕组")
        table.add_column("tier", justify="center", no_wrap=True)
        table.add_column("sid", style="cyan", no_wrap=True)
        table.add_column("字幕组", overflow="ellipsis", max_width=18, no_wrap=True)
        table.add_column("风险", no_wrap=True)
        table.add_column("评分", justify="right", no_wrap=True)
        table.add_column("速度", justify="right", no_wrap=True)
        table.add_column("语言分", justify="right", no_wrap=True)
        table.add_column("落后", justify="right", no_wrap=True)
        table.add_column("集数", justify="right", no_wrap=True)
        table.add_column("语言", overflow="ellipsis", max_width=12, no_wrap=True)
        for row in data["candidates"]:
            table.add_row(
                row["tier"],
                str(row["subgroup_id"]),
                row["name"],
                row["risk_label"],
                f"{row['score']:.3f}",
                f"{row['speed_score']:.3f}",
                f"{row['lang_match']:.2f}",
                _fmt_lag(row["missing_vs_peer"], row["peer_gap_h"]),
                str(row["episode_count"]),
                ",".join(row["languages"]) or "-",
            )
        console.print(table)
        for message in data["warnings"]:
            console.print(f"[yellow]注意[/yellow] {message}")
        if explain:
            for row in data["candidates"]:
                console.print(f"\n[bold]{row['name']}[/bold]（sid {row['subgroup_id']}）")
                for item in row["evidence"]:
                    console.print(f"  · {item}")

    _emit(app_ctx, payload, render)


@subgroup_app.command("audit")
def subgroup_audit(
    ctx: typer.Context,
    season: Annotated[
        str | None, typer.Option("--season", help="只看该季度的已纳管番剧，如 2026夏")
    ] = None,
    scan: Annotated[
        str | None, typer.Option("--scan", help="扫描该季度全部番剧（含未纳管，较慢）")
    ] = None,
    mode: Annotated[str, typer.Option("--mode", help="fast | normal")] = "normal",
    stale_days: Annotated[float, typer.Option("--stale-days", help="落后多少天算异常")] = 10,
    deep: Annotated[int, typer.Option("--deep", help="历史深查前 N 名；0=不深查")] = 4,
    works: Annotated[
        int | None,
        typer.Option("--works", help="历史深查回看的作品数（默认取 profile.history_works）"),
    ] = None,
    use_bangumi: Annotated[
        bool, typer.Option("--bangumi/--no-bangumi", help="用 Bangumi 精确放送日程")
    ] = True,
    refresh: RefreshFlag = False,
    json_output: JsonFlag = False,
) -> None:
    """巡检托管清单或整季番剧的停更情况。

    Args:
        season: 只看该季度的已纳管番剧，如 2026夏；None 表示全部。
        scan: 扫描该季度全部番剧（含未纳管）；None 表示不扫描。
        mode: 分析模式，fast | normal。
        stale_days: 落后多少天算异常。
        deep: 历史深查前 N 名；0=不深查。
        works: 历史深查回看的作品数；None 取 profile.history_works。
        use_bangumi: 是否用 Bangumi 精确放送日程。
        refresh: 是否忽略缓存强制重新抓取。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    source = build_source(app_ctx.config, app_ctx.env)
    policy = _policy_for(app_ctx, mode)
    state = load_state()
    bgm = _bangumi(app_ctx, use_bangumi)

    tracked = [(e.bangumi_id, e.subgroup_id, e.bangumi_title, e.season) for e in state.entries]
    if season:
        tracked = [t for t in tracked if season in (t[3] or "")]

    scan_targets: list[int] = []
    if scan:
        year = int("".join(ch for ch in scan if ch.isdigit()) or 0)
        label = "".join(ch for ch in scan if not ch.isdigit()) or "夏"
        if year == 0:
            err_console.print("[red]--scan 需要形如 2026夏[/red]")
            raise typer.Exit(code=EXIT_BAD_PARAMS)
        try:
            entries = source.season(year, label, force=refresh)
        except (MikanError, ValueError) as exc:
            _fail_mikan(exc if isinstance(exc, MikanError) else MikanError(str(exc)))
            return
        known = {t[0] for t in tracked}
        scan_targets = [e.bangumi_id for e in entries if e.bangumi_id not in known]

    if not tracked and not scan_targets:
        console.print(
            "[yellow]没有可巡检的对象：托管清单为空。"
            "用 `mikan apply add` 纳管，或用 `mikan subgroup audit --scan 2026夏` 扫描全季[/yellow]"
        )
        return

    findings: list[dict] = []
    for bangumi_id, subgroup_id, title, _season in tracked:
        findings.append(
            _audit_one(
                app_ctx,
                source,
                policy,
                bangumi_id,
                subgroup_id,
                title,
                stale_days,
                deep,
                works,
                page_only=False,
                refresh=refresh,
                tracked=True,
                bgm=bgm,
            )
        )
    for bangumi_id in scan_targets:
        findings.append(
            _audit_one(
                app_ctx,
                source,
                policy,
                bangumi_id,
                None,
                "",
                stale_days,
                deep,
                works,
                page_only=True,
                refresh=refresh,
                tracked=False,
                bgm=bgm,
            )
        )

    payload = {"mode": mode, "stale_days": stale_days, "findings": findings}

    def render(data: dict) -> None:
        """渲染停更巡检结果表。

        Args:
            data: 待渲染的字典。
        """
        counts: dict[str, int] = {}
        for row in data["findings"]:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        console.print(
            "巡检 "
            + str(len(data["findings"]))
            + " 部："
            + "，".join(f"{name} {count}" for name, count in counts.items())
        )
        table = Table(title="停更巡检")
        table.add_column("状态", no_wrap=True)
        table.add_column("番剧", overflow="ellipsis", max_width=26, no_wrap=True)
        table.add_column("字幕组", overflow="ellipsis", max_width=16, no_wrap=True)
        table.add_column("tier", justify="center", no_wrap=True)
        table.add_column("进度", no_wrap=True)
        table.add_column("说明", overflow="ellipsis", max_width=44, no_wrap=True)
        tones = {"正常": "green", "留意": "yellow", "需关注": "red", "未纳管": "cyan"}
        for row in data["findings"]:
            status = row["status"]
            tone = tones.get(status, "yellow")
            table.add_row(
                f"[{tone}]{status}[/]",
                row["title"],
                row.get("subgroup_name") or "-",
                row.get("tier") or "-",
                row.get("progress") or "-",
                "；".join(row["flags"]) or "-",
            )
        console.print(table)

    _emit(app_ctx, payload, render)
