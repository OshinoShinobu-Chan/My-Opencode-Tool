"""字幕组健康度报告：历史回看、放送日程与排名 / 风险报告构建。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from mikan_health.core.airtime import Schedule, build_schedule
from mikan_health.core.episodes import (
    episode_events,
    median_or_none,
    relative_delays_hours,
    subtitle_baseline,
)
from mikan_health.core.health import HealthInputs, analyze
from mikan_health.core.policy import Policy
from mikan_health.core.risk import WorkRecord, assess_risk, judge_work, season_is_recent
from mikan_health.core.score import Ranking
from mikan_health.data.bangumi import BangumiClient, BangumiError, subject_id_from_url
from mikan_health.data.models import BangumiDetail, Release
from mikan_health.data.source import MikanSource

DEEP_DEFAULT = 6
WORKS_DEFAULT = 16
SEASONS_DEFAULT = 4


@dataclass
class HistoryResult:
    """字幕组历史回看的聚合结果。

    Attributes:
        records: 回看到的作品发布记录。
        seasons: 该组参与过的季度（降序、去重）。
        last_release: 最近一次发布时间；无记录为 None。
    """

    records: list[WorkRecord] = field(default_factory=list)
    seasons: list[str] = field(default_factory=list)
    last_release: datetime | None = None


def collect_history(
    source: MikanSource,
    subgroup_id: int,
    publish_group_id: int | None,
    exclude_bangumi_id: int | None = None,
    limit: int = WORKS_DEFAULT,
    seasons: int = SEASONS_DEFAULT,
    force: bool = False,
) -> HistoryResult:
    """回看该字幕组最近若干季度（跳过当前季）的作品。

    当前季作品多在连载、无法判定弃番，且会占满回看名额。

    Args:
        source: Mikan 数据源。
        subgroup_id: 目标字幕组 id。
        publish_group_id: 该组作品所属发布组 id；None 时返回空结果。
        exclude_bangumi_id: 需排除的番剧 id（通常是当前番）；None 不排除。
        limit: 回看作品数量上限。
        seasons: 回看的季度数上限。
        force: 是否强制刷新缓存。
    """
    result = HistoryResult()
    if publish_group_id is None:
        return result

    group = source.publish_group(publish_group_id)
    result.seasons = sorted(
        {
            w.season_date
            for w in group.works
            if w.subtitle_group_id == subgroup_id and w.season_date
        },
        reverse=True,
    )

    by_season: dict[str, list] = {}
    for work in group.works:
        if work.subtitle_group_id != subgroup_id:
            continue
        if work.bangumi_id == exclude_bangumi_id:
            continue
        if work.is_current:
            continue
        by_season.setdefault(work.season_date, []).append(work)

    picked: list = []
    seen: set[int] = set()
    for season_date in list(by_season)[:seasons]:
        for work in by_season[season_date]:
            if work.bangumi_id in seen:
                continue
            seen.add(work.bangumi_id)
            picked.append(work)
            if len(picked) >= limit:
                break
        if len(picked) >= limit:
            break

    titles = {w.bangumi_id: w.title for w in group.works}
    seasons_map = {w.bangumi_id: w.season_date for w in group.works}

    for work in picked:
        detail = source.bangumi(work.bangumi_id, force=force)
        record = _work_record(
            detail,
            subgroup_id,
            work.bangumi_id,
            titles.get(work.bangumi_id, detail.title),
            seasons_map.get(work.bangumi_id, ""),
        )
        result.records.append(record)
        if record.group_last_release is not None and (
            result.last_release is None or record.group_last_release > result.last_release
        ):
            result.last_release = record.group_last_release

    return result


def _work_record(
    detail: BangumiDetail,
    subgroup_id: int,
    bangumi_id: int,
    title: str,
    season_date: str,
) -> WorkRecord:
    """把单部番剧的发布记录汇总成一个 WorkRecord。

    Args:
        detail: 番剧详情，含各组发布记录。
        subgroup_id: 目标字幕组 id。
        bangumi_id: 番剧 id。
        title: 优先使用的标题；为空时回退 detail.title。
        season_date: 所属季度标签。
    """
    by_group: dict[int, list[Release]] = {}
    for release in detail.releases:
        by_group.setdefault(release.subtitle_group_id, []).append(release)

    events_by_group = {
        sid: episode_events(items)
        for sid, items in by_group.items()
        if any(r.is_episode for r in items)
    }
    own = events_by_group.get(subgroup_id, {})
    all_episodes = [
        event.episode for events in events_by_group.values() for event in events.values()
    ]
    fact_final = max(all_episodes) if all_episodes else None
    own_episodes = list(own) or None

    own_stamps = [e.released_at for e in own.values() if e.released_at != datetime.min]
    fact_stamps = [
        e.released_at
        for events in events_by_group.values()
        for e in events.values()
        if e.released_at != datetime.min
    ]

    rel_map = relative_delays_hours(events_by_group, subtitle_baseline(detail.releases)).get(
        subgroup_id, {}
    )
    rel_p50 = median_or_none(list(rel_map.values()))

    return WorkRecord(
        bangumi_id=bangumi_id,
        title=title or detail.title,
        season_date=season_date,
        group_final_episode=max(own_episodes) if own_episodes else None,
        fact_final_episode=fact_final,
        group_last_release=max(own_stamps) if own_stamps else None,
        fact_last_release=max(fact_stamps) if fact_stamps else None,
        rel_p50_h=rel_p50,
    )


def load_schedule(client: BangumiClient | None, detail: BangumiDetail) -> Schedule | None:
    """用番剧页上的 bgm.tv 链接拉精确放送日程；失败时返回 None（退回估算口径）。

    Args:
        client: Bangumi 客户端；None 表示不可用。
        detail: 番剧详情，用于提取 bgm.tv 链接。
    """
    if client is None:
        return None
    subject_id = subject_id_from_url(detail.bangumi_tv_url)
    if subject_id is None:
        return None
    try:
        subject = client.subject(subject_id)
        episodes = client.episodes(subject_id)
    except BangumiError:
        return None
    if not episodes.airdates():
        return None
    return build_schedule(subject, episodes)


def resolve_works(works: int | None, policy: Policy) -> int:
    """历史回看深度：CLI 未指定时用 profile 的 history_works。

    Args:
        works: CLI 指定的回看深度；None 或非正数时回退 profile 配置。
        policy: 生效的分析策略，提供 history_works 默认值。
    """
    if works is not None and works > 0:
        return works
    return policy.history_works if policy.history_works > 0 else WORKS_DEFAULT


def build_report(
    source: MikanSource,
    bangumi_id: int,
    policy: Policy,
    taxonomy,
    deep: int = DEEP_DEFAULT,
    works: int | None = None,
    page_only: bool = False,
    now: datetime | None = None,
    force: bool = False,
    force_deep: set[int] | None = None,
    bgm: BangumiClient | None = None,
) -> tuple[Ranking, BangumiDetail]:
    """构建番剧的字幕组排名报告，返回 (Ranking, BangumiDetail)。

    deep 控制历史深查范围：0 不查、-1 全部、正数取前 N 名。

    Args:
        source: Mikan 数据源。
        bangumi_id: 番剧 id。
        policy: 分析策略。
        taxonomy: 标题标签词典。
        deep: 历史深查范围；0 不查、-1 全部、正数取前 N 名。
        works: 每个字幕组的回看深度；None 用 profile 默认。
        page_only: 仅用番剧页数据，不抓各字幕组完整发布列表。
        now: 参考时间；None 用当前时间。
        force: 是否强制刷新缓存。
        force_deep: 额外强制深查的字幕组 id 集合。
        bgm: Bangumi 客户端；None 表示不用精确放送日程。
    """
    moment = now or datetime.now()
    depth = resolve_works(works, policy)
    detail = source.bangumi(bangumi_id, force=force)
    schedule = load_schedule(bgm, detail)

    releases: list[Release] = list(detail.releases)
    if not page_only:
        full: list[Release] = []
        for subgroup in detail.subgroups:
            full.extend(
                source.subgroup_releases(bangumi_id, subgroup.subtitle_group_id, force=force)
            )
        releases = full

    probe = HealthInputs(detail=detail, releases=releases, schedule=schedule, now=moment)
    pre = analyze(probe, policy, taxonomy, deep_checked=set())

    if deep < 0:
        deep_ids = {h.subgroup_id for h in pre.candidates}
    elif deep == 0:
        deep_ids = set()
    else:
        deep_ids = {h.subgroup_id for h in pre.eligible[:deep]}
    if force_deep:
        deep_ids |= {sid for sid in force_deep if detail.subgroup(sid) is not None}

    works_map: dict[int, list[WorkRecord]] = {}
    seasons_map: dict[int, list[str]] = {}
    last_release_map: dict[int, datetime | None] = {}
    for health in pre.candidates:
        if health.subgroup_id not in deep_ids:
            continue
        history = collect_history(
            source,
            subgroup_id=health.subgroup_id,
            publish_group_id=health.metrics.publish_group_id,
            exclude_bangumi_id=bangumi_id,
            limit=depth,
            force=force,
        )
        works_map[health.subgroup_id] = history.records
        seasons_map[health.subgroup_id] = history.seasons
        last_release_map[health.subgroup_id] = history.last_release

    final_inputs = HealthInputs(
        detail=detail,
        releases=releases,
        works=works_map,
        subgroup_seasons=seasons_map,
        subgroup_last_release=last_release_map,
        schedule=schedule,
        now=moment,
    )
    ranking = analyze(final_inputs, policy, taxonomy, deep_checked=deep_ids)
    return ranking, detail


def build_subgroup_report(
    source: MikanSource,
    subgroup_id: int,
    policy: Policy,
    publish_group_id: int | None = None,
    works: int | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> dict:
    """汇总单个字幕组的历史作品与风险，返回供 CLI 渲染的字典。

    Args:
        source: Mikan 数据源。
        subgroup_id: 字幕组 id。
        policy: 分析策略。
        publish_group_id: 该组作品所属发布组 id；None 时回看为空。
        works: 回看深度；None 用 profile 默认。
        now: 参考时间；None 用当前时间。
        force: 是否强制刷新缓存。
    """
    moment = now or datetime.now()
    history = collect_history(
        source,
        subgroup_id=subgroup_id,
        publish_group_id=publish_group_id,
        limit=resolve_works(works, policy),
        force=force,
    )
    rel_values = [r.rel_p50_h for r in history.records if r.rel_p50_h is not None]
    last_release_days = (
        (moment - history.last_release).total_seconds() / 86400 if history.last_release else None
    )
    accepts_new = any(season_is_recent(s, moment.date()) for s in history.seasons)

    assessment = assess_risk(
        records=history.records,
        policy=policy,
        now=moment,
        last_release_days=last_release_days,
        accepts_new_season=accepts_new,
        peer_gap_h=None,
        stall_lag_days=policy.stall_lag_days,
    )
    group_active = last_release_days is None or last_release_days < policy.silent_days
    verdicts = [judge_work(r, moment, policy.drop_gap_days, group_active) for r in history.records]
    return {
        "subgroup_id": subgroup_id,
        "publish_group_id": publish_group_id,
        "works_total": len(history.records),
        "works_judged": sum(1 for v in verdicts if v is not None),
        "seasons": history.seasons,
        "last_release_days": last_release_days,
        "accepts_new_season": accepts_new,
        "median_rel_p50_h": median_or_none(rel_values),
        "risk": assessment,
        "records": history.records,
        "verdicts": verdicts,
    }
