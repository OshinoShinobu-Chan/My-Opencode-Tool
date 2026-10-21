"""字幕组指标计算：速度、覆盖与语言/字幕类型统计。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from mikan_health.core.airtime import Schedule
from mikan_health.core.episodes import (
    EpisodeEvent,
    baseline_groups,
    delays_hours,
    intervals_hours,
    mad,
    median_or_none,
    relative_delays_hours,
)
from mikan_health.core.releases import SIMPLIFIED_LABELS, TRADITIONAL_LABELS
from mikan_health.data.models import Release

CATCHUP_HOURS = 48.0
MIN_INTERVAL_HOURS = 24.0


def airdate_delays(events: dict[float, EpisodeEvent], schedule: Schedule) -> dict[float, float]:
    """以 Bangumi 的精确放送日为基准计算延迟（事件键是 ordinal）。

    Args:
        events: ordinal -> 每集发布时间点。
        schedule: Bangumi 精确放送日程。
    """
    result: dict[float, float] = {}
    for ordinal, event in events.items():
        if event.released_at == datetime.min:
            continue
        aired = schedule.date_of_ordinal(int(ordinal))
        if aired is None:
            continue
        nominal = datetime.combine(aired, datetime.min.time())
        result[ordinal] = (event.released_at - nominal).total_seconds() / 3600
    return result


@dataclass
class SpeedMetrics:
    """速度类指标（绝对/相对延迟、抖动、发布间隔、攒集次数）。

    Attributes:
        delay_p50_h: 相对名义/精确放送日的中位延迟（小时）；无数据为 None。
        rel_p50_h: 相对同番最快组的中位延迟（小时）；无数据为 None。
        jitter_h: 延迟的中位数绝对偏差（小时），越小越稳定。
        interval_med_h: 相邻两集发布时间间隔的中位数（小时）。
        catchup_weeks: 发布间隔小于攒集阈值（CATCHUP_HOURS）的次数。
    """

    delay_p50_h: float | None = None
    rel_p50_h: float | None = None
    jitter_h: float | None = None
    interval_med_h: float | None = None
    catchup_weeks: int = 0


@dataclass
class CoverageMetrics:
    """覆盖类指标（集数、停滞比、与最快组的差距）。

    Attributes:
        last_episode: 该组已发布的最高集号（ordinal）；无发布为 None。
        released_eps: 该组已发布的去重集数。
        stale_ratio: 距最近发布的时长相对自身发布节奏的倍数；无法计算为 None。
        peer_gap_h: 落后同番最快组的小时数；不落后为 0.0，无法比较为 None。
        peer_max_episode: 同番所有参与比较组中的最高集号；无数据为 None。
        peer_episode_count: 同番单个组的最多已发布集数。
        missing_vs_peer: 相对 peer_episode_count 的缺集数。
    """

    last_episode: float | None = None
    released_eps: int = 0
    stale_ratio: float | None = None
    peer_gap_h: float | None = None
    peer_max_episode: float | None = None
    peer_episode_count: int = 0
    missing_vs_peer: int = 0

    @property
    def coverage_ratio(self) -> float:
        """已发布集数 / 同番最多集数，上限 1.0。"""
        if self.peer_episode_count <= 0:
            return 1.0
        return min(self.released_eps / self.peer_episode_count, 1.0)


@dataclass
class SubgroupMetrics:
    """单个字幕组的汇总指标。

    Attributes:
        subgroup_id: 字幕组 id（RSS 的 subgroupid）。
        name: 字幕组名称。
        publish_group_id: Mikan 作品年表 id；未知为 None。
        languages: 该组出现过的语言标签（去重、升序）。
        sub_types: 出现过的字幕类型（内封/外挂/内嵌/内挂，去重）。
        has_simplified: 是否出现过简体字幕。
        has_explicit_simplified: 是否出现过明确标注的简体字幕。
        has_explicit_traditional: 是否出现过明确标注的繁体字幕。
        has_dual_zh_jp: 是否出现过简繁 / 中日双语。
        episode_count: 可识别的单集发布数。
        events: 集号 -> EpisodeEvent（每集最早发布时间与变体数）。
        speed: 速度类指标。
        coverage: 覆盖类指标。
    """

    subgroup_id: int
    name: str
    publish_group_id: int | None = None
    languages: tuple[str, ...] = ()
    sub_types: tuple[str, ...] = ()
    has_simplified: bool = False
    has_explicit_simplified: bool = False
    has_explicit_traditional: bool = False
    has_dual_zh_jp: bool = False
    episode_count: int = 0
    events: dict[float, EpisodeEvent] = field(default_factory=dict)
    speed: SpeedMetrics = field(default_factory=SpeedMetrics)
    coverage: CoverageMetrics = field(default_factory=CoverageMetrics)


def compute_metrics(
    subgroup_id: int,
    name: str,
    releases: list[Release],
    events_by_group: dict[int, dict[float, EpisodeEvent]],
    start_date: date | None,
    start_ep: float | None,
    now: datetime,
    catchup_hours: float = CATCHUP_HOURS,
    min_interval_hours: float = MIN_INTERVAL_HOURS,
    baseline: set[int] | None = None,
    schedule: Schedule | None = None,
) -> SubgroupMetrics:
    """计算某字幕组的全部指标（速度、覆盖与语言标记）。

    Args:
        subgroup_id: 字幕组 id。
        name: 字幕组名称。
        releases: 该组的发布记录。
        events_by_group: 字幕组 id -> 各集发布时间点（用于同番比较）。
        start_date: 起始集对应日期；None 表示无法算名义延迟。
        start_ep: 起始集号；None 表示无法算名义延迟。
        now: 计算基准时间。
        catchup_hours: 判为「攒集连发」的相邻集间隔阈值（小时）。
        min_interval_hours: 计算停滞比时发布间隔的下限（小时）。
        baseline: 参与比较的字幕组 id 集合；None 表示全部参与。
        schedule: Bangumi 精确放送日程；None 则用名义日期。
    """
    own = releases
    events = events_by_group.get(subgroup_id, {})
    if schedule is not None:
        delays = list(airdate_delays(events, schedule).values())
    else:
        delays = list(delays_hours(events, start_date, start_ep).values())
    rel_map = relative_delays_hours(events_by_group, baseline).get(subgroup_id, {})
    rels = list(rel_map.values())
    gaps = intervals_hours(events)

    metrics = SubgroupMetrics(
        subgroup_id=subgroup_id,
        name=name,
        languages=tuple(sorted({r.language for r in own})),
        sub_types=tuple(sorted({r.sub_type for r in own if r.sub_type})),
        has_simplified=any(r.language in SIMPLIFIED_LABELS for r in own),
        has_explicit_simplified=any(
            r.has_explicit_chinese and r.language in SIMPLIFIED_LABELS for r in own
        ),
        has_explicit_traditional=any(
            r.has_explicit_chinese and r.language in TRADITIONAL_LABELS for r in own
        ),
        has_dual_zh_jp=any(r.is_dual_zh_jp for r in own),
        episode_count=len(events),
        events=events,
    )

    metrics.speed = SpeedMetrics(
        delay_p50_h=median_or_none(delays),
        rel_p50_h=median_or_none(rels),
        jitter_h=mad(delays),
        interval_med_h=median_or_none(gaps),
        catchup_weeks=sum(1 for gap in gaps if gap < catchup_hours),
    )

    last_release = max((e.released_at for e in events.values()), default=None)
    stale_days = (
        (now - last_release).total_seconds() / 86400
        if last_release is not None and last_release != datetime.min
        else None
    )
    interval = metrics.speed.interval_med_h
    stale_ratio = (
        stale_days * 24 / max(interval, min_interval_hours)
        if stale_days is not None and interval and interval > 0
        else None
    )

    peer_events = baseline_groups(events_by_group, baseline)

    peer_max_episode = max(
        (max(events_.keys()) for events_ in peer_events.values() if events_), default=None
    )
    peer_episode_count = max((len(events_) for events_ in peer_events.values()), default=0)
    peer_gap = _peer_gap_h(peer_events, subgroup_id, peer_max_episode, now)
    missing_vs_peer = max(0, peer_episode_count - len(events)) if peer_episode_count else 0

    metrics.coverage = CoverageMetrics(
        last_episode=max(events) if events else None,
        released_eps=len(events),
        stale_ratio=stale_ratio,
        peer_gap_h=peer_gap,
        peer_max_episode=peer_max_episode,
        peer_episode_count=peer_episode_count,
        missing_vs_peer=missing_vs_peer,
    )
    return metrics


def _peer_gap_h(
    events_by_group: dict[int, dict[float, EpisodeEvent]],
    subgroup_id: int,
    peer_max_episode: float | None,
    now: datetime,
) -> float | None:
    """该组相对同番最快组、在对方最高集号上的落后小时数。

    Args:
        events_by_group: 字幕组 id -> 各集发布时间点。
        subgroup_id: 当前字幕组 id。
        peer_max_episode: 同番所有比较组中的最高集号；None 表示无数据。
        now: 计算基准时间。
    """
    if peer_max_episode is None:
        return None
    own = events_by_group.get(subgroup_id, {})
    own_max = max(own) if own else None
    if own_max is not None and own_max >= peer_max_episode:
        return 0.0
    best = None
    for events in events_by_group.values():
        event = events.get(peer_max_episode)
        if event is None or event.released_at == datetime.min:
            continue
        if best is None or event.released_at < best:
            best = event.released_at
    if best is None:
        return None
    return (now - best).total_seconds() / 3600
