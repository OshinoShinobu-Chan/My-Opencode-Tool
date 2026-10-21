"""anime 子命令：Mikan 番剧的元数据与发布记录查询。"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from mikan_health.data import MikanError, Release
from mikan_health.data.bangumi import BangumiError, subject_id_from_url
from mikan_qb import EXIT_BAD_PARAMS, EXIT_REMOTE_ERROR
from mikan_qb.commands._format import _filter_releases, _fmt_dt, _fmt_num
from mikan_qb.commands._shared import (
    JsonFlag,
    RefreshFlag,
    _abs_url,
    _ctx,
    _emit,
    _fail_mikan,
    _load_bangumi,
    console,
    err_console,
)
from mikan_qb.mikan import build_bangumi_client, build_source

anime_app = typer.Typer(help="番剧查询（Mikan 站点）", no_args_is_help=True)


@anime_app.command("bangumi")
def anime_bangumi(
    ctx: typer.Context,
    bangumi_id: Annotated[int, typer.Argument(help="Mikan bangumi id")],
    schedule: Annotated[bool, typer.Option("--schedule", help="同时列出每集放送日期")] = False,
    refresh: RefreshFlag = False,
    json_output: JsonFlag = False,
) -> None:
    """查询番剧的 Bangumi 元数据与放送日程。

    Args:
        bangumi_id: Mikan bangumi id。
        schedule: 是否同时列出每集放送日期。
        refresh: 是否忽略缓存强制重新抓取。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    detail = _load_bangumi(app_ctx, bangumi_id, refresh)
    if detail is None:
        return
    subject_id = subject_id_from_url(detail.bangumi_tv_url)
    if subject_id is None:
        err_console.print(
            f"[red]番剧页没有可解析的 bgm.tv 链接：{detail.bangumi_tv_url or '(空)'}[/red]"
        )
        raise typer.Exit(code=EXIT_BAD_PARAMS)

    client = build_bangumi_client(app_ctx.config, app_ctx.env)
    try:
        subject = client.subject(subject_id, force=refresh)
        episodes = client.episodes(subject_id, force=refresh) if schedule else None
    except BangumiError as exc:
        err_console.print(f"[red]{exc}[/red]")
        err_console.print(
            "[yellow]提示[/yellow] Bangumi 需要代理；可设置 BGM_PROXY=http://127.0.0.1:7890"
        )
        raise typer.Exit(code=EXIT_REMOTE_ERROR) from exc

    mains = episodes.main_episodes() if episodes else []
    payload = {
        "mikan_bangumi_id": bangumi_id,
        "bgm_subject_id": subject.id,
        "name": subject.name,
        "name_cn": subject.name_cn,
        "date": subject.air_date.isoformat() if subject.air_date else None,
        "eps": subject.eps,
        "total_episodes": subject.total_episodes,
        "platform": subject.platform,
        "weekday": subject.broadcast_weekday,
        "aliases": subject.infobox_value("别名"),
        "schedule": [
            {
                "sort": ep.sort,
                "ep": ep.ep,
                "airdate": ep.airdate.isoformat() if ep.airdate else None,
                "name": ep.name,
                "name_cn": ep.name_cn,
            }
            for ep in mains
        ],
    }

    def render(data: dict) -> None:
        """渲染番剧元数据与放送日程表。

        Args:
            data: 待渲染的字典。
        """
        table = Table(title=f"{data['name_cn'] or data['name']}", show_header=False)
        table.add_column("项", style="cyan")
        table.add_column("值")
        for key in (
            "bgm_subject_id",
            "name",
            "date",
            "eps",
            "total_episodes",
            "platform",
            "weekday",
            "aliases",
        ):
            table.add_row(key, str(data.get(key) or "-"))
        console.print(table)
        if data["schedule"]:
            schedule_table = Table(title=f"放送日程（{len(data['schedule'])} 集）")
            schedule_table.add_column("集", justify="right", no_wrap=True)
            schedule_table.add_column("日期", no_wrap=True)
            schedule_table.add_column("标题", overflow="ellipsis", max_width=40, no_wrap=True)
            for row in data["schedule"]:
                schedule_table.add_row(
                    _fmt_num(row["ep"]), row["airdate"] or "未定", row["name_cn"] or row["name"]
                )
            console.print(schedule_table)

    _emit(app_ctx, payload, render)


@anime_app.command("search")
def anime_search(
    ctx: typer.Context,
    keyword: Annotated[str, typer.Argument(help="搜索关键词")],
    limit: Annotated[int, typer.Option("--limit", help="最多返回条数")] = 20,
    refresh: RefreshFlag = False,
    json_output: JsonFlag = False,
) -> None:
    """按关键词搜索 Mikan 番剧。

    Args:
        keyword: 搜索关键词。
        limit: 最多返回条数。
        refresh: 是否忽略缓存强制重新抓取。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    source = build_source(app_ctx.config, app_ctx.env)
    try:
        results = source.search(keyword, force=refresh)[:limit]
    except MikanError as exc:
        _fail_mikan(exc)
        return

    payload = [r.model_dump(mode="json") for r in results]

    def render(data: list[dict]) -> None:
        """渲染搜索结果表。

        Args:
            data: 待渲染的列表。
        """
        if not data:
            console.print("[yellow]没有匹配结果[/yellow]")
            return
        table = Table(title=f"搜索：{keyword}")
        table.add_column("bangumi_id", style="cyan")
        table.add_column("标题")
        for row in data:
            table.add_row(str(row["bangumi_id"]), row["title"])
        console.print(table)

    _emit(app_ctx, payload, render)


@anime_app.command("info")
def anime_info(
    ctx: typer.Context,
    bangumi_id: Annotated[int, typer.Argument(help="Mikan bangumi id")],
    refresh: RefreshFlag = False,
    json_output: JsonFlag = False,
) -> None:
    """显示番剧在 Mikan 上的基本信息。

    Args:
        bangumi_id: Mikan bangumi id。
        refresh: 是否忽略缓存强制重新抓取。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    detail = _load_bangumi(app_ctx, bangumi_id, refresh)
    if detail is None:
        return

    payload = {
        "bangumi_id": detail.bangumi_id,
        "title": detail.title,
        "weekday": detail.weekday,
        "start_date": detail.start_date.isoformat() if detail.start_date else None,
        "official_site": detail.official_site,
        "bangumi_tv_url": detail.bangumi_tv_url,
        "rss_url": _abs_url(app_ctx, detail.rss_url),
        "subgroup_count": len(detail.subgroups),
        "release_count": len(detail.releases),
    }

    def render(data: dict) -> None:
        """渲染番剧基本信息表。

        Args:
            data: 待渲染的字典。
        """
        table = Table(title=data["title"], show_header=False)
        table.add_column("项", style="cyan")
        table.add_column("值")
        for key in (
            "bangumi_id",
            "weekday",
            "start_date",
            "official_site",
            "bangumi_tv_url",
            "rss_url",
            "subgroup_count",
            "release_count",
        ):
            table.add_row(key, str(data[key]))
        console.print(table)

    _emit(app_ctx, payload, render)


@anime_app.command("subgroups")
def anime_subgroups(
    ctx: typer.Context,
    bangumi_id: Annotated[int, typer.Argument(help="Mikan bangumi id")],
    sort: Annotated[str, typer.Option("--sort", help="排序：latest | name | releases")] = "latest",
    refresh: RefreshFlag = False,
    json_output: JsonFlag = False,
) -> None:
    """列出番剧的字幕组及其语言和版本。

    Args:
        bangumi_id: Mikan bangumi id。
        sort: 排序方式，latest | name | releases。
        refresh: 是否忽略缓存强制重新抓取。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    detail = _load_bangumi(app_ctx, bangumi_id, refresh)
    if detail is None:
        return

    items = list(detail.subgroups)
    if sort == "name":
        items.sort(key=lambda s: s.name)
    elif sort == "releases":
        items.sort(key=lambda s: s.release_count, reverse=True)
    else:
        items.sort(
            key=lambda s: (s.last_update is not None, s.last_update),
            reverse=True,
        )

    payload = {
        "bangumi_id": detail.bangumi_id,
        "title": detail.title,
        "subgroups": [
            {
                **s.model_dump(mode="json"),
                "languages": sorted(
                    {
                        r.language
                        for r in detail.releases
                        if r.subtitle_group_id == s.subtitle_group_id
                    }
                ),
                "sub_types": sorted(
                    {
                        r.sub_type
                        for r in detail.releases
                        if r.subtitle_group_id == s.subtitle_group_id and r.sub_type
                    }
                ),
            }
            for s in items
        ],
    }

    def render(data: dict) -> None:
        """渲染字幕组及其语言 / 版本表。

        Args:
            data: 待渲染的字典。
        """
        table = Table(title=f"{data['title']} — 字幕组（{len(data['subgroups'])} 个）")
        table.add_column("sid", style="cyan", no_wrap=True, min_width=5)
        table.add_column("字幕组", no_wrap=True, overflow="ellipsis", max_width=18, min_width=10)
        table.add_column("本页", no_wrap=True, min_width=4)
        table.add_column("最近更新", no_wrap=True, min_width=16)
        table.add_column("语言", no_wrap=True, overflow="ellipsis", max_width=16, min_width=6)
        table.add_column("版本", no_wrap=True, overflow="ellipsis", max_width=10, min_width=5)
        for row in data["subgroups"]:
            table.add_row(
                str(row["subtitle_group_id"]),
                row["name"],
                str(row["release_count"]),
                _fmt_dt(row["last_update"]),
                ",".join(row["languages"]) or "-",
                ",".join(row["sub_types"]) or "-",
            )
        console.print(table)

    _emit(app_ctx, payload, render)


@anime_app.command("releases")
def anime_releases(
    ctx: typer.Context,
    bangumi_id: Annotated[int, typer.Argument(help="Mikan bangumi id")],
    subgroup: Annotated[int | None, typer.Option("--subgroup", help="仅看某个字幕组 id")] = None,
    full: Annotated[
        bool, typer.Option("--full", help="抓取完整历史（每个字幕组一次请求）")
    ] = False,
    since: Annotated[str | None, typer.Option("--since", help="起始日期 YYYY-MM-DD")] = None,
    limit: Annotated[int | None, typer.Option("--limit", help="最多返回条数")] = None,
    ep: Annotated[float | None, typer.Option("--ep", help="仅看某一集")] = None,
    lang: Annotated[
        str | None, typer.Option("--lang", help="按语言过滤，如 简体/繁体/简日双语/简繁")
    ] = None,
    include_pack: Annotated[
        bool, typer.Option("--include-pack", help="包含合集与特典条目")
    ] = False,
    refresh: RefreshFlag = False,
    json_output: JsonFlag = False,
) -> None:
    """列出番剧的发布记录，可按字幕组、日期、集与语言过滤。

    Args:
        bangumi_id: Mikan bangumi id。
        subgroup: 仅看该字幕组 id；None 表示全部。
        full: 是否抓取完整历史。
        since: 起始日期 YYYY-MM-DD；None 表示不限。
        limit: 最多返回条数；None 表示不限。
        ep: 仅看某一集；None 表示不限。
        lang: 按语言过滤；None 表示不限。
        include_pack: 是否包含合集与特典条目。
        refresh: 是否忽略缓存强制重新抓取。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    try:
        detail = _load_bangumi(app_ctx, bangumi_id, refresh)
        if detail is None:
            return
        source = build_source(app_ctx.config, app_ctx.env)
        if full:
            targets = (
                [subgroup]
                if subgroup is not None
                else [s.subtitle_group_id for s in detail.subgroups]
            )
            releases: list[Release] = []
            for sid in targets:
                releases.extend(source.subgroup_releases(bangumi_id, sid, force=refresh))
        else:
            releases = [
                r for r in detail.releases if subgroup is None or r.subtitle_group_id == subgroup
            ]
    except MikanError as exc:
        _fail_mikan(exc)
        return

    releases = _filter_releases(releases, since, ep, lang, include_pack)
    if limit is not None:
        releases = releases[:limit]

    payload = [r.model_dump(mode="json") for r in releases]

    def render(data: list[dict]) -> None:
        """渲染发布记录表。

        Args:
            data: 待渲染的列表。
        """
        table = Table(title=f"{detail.title} — 发布记录（{len(data)} 条）")
        table.add_column("sid", style="cyan", no_wrap=True, min_width=5)
        table.add_column("集", no_wrap=True, min_width=3)
        table.add_column("语言", no_wrap=True, min_width=6)
        table.add_column("版本", no_wrap=True, min_width=5)
        table.add_column("时间", no_wrap=True, min_width=16)
        table.add_column("标题", no_wrap=True, overflow="ellipsis", max_width=52, min_width=20)
        for row in data:
            table.add_row(
                str(row["subtitle_group_id"]),
                row["episode_raw"] or ("合集" if row["is_pack"] else "-"),
                row["language"],
                row["sub_type"] or "-",
                _fmt_dt(row["published_at"]),
                row["title"] or "",
            )
        console.print(table)

    _emit(app_ctx, payload, render)
