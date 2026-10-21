"""发布记录压成每集时间点，以及延迟/间隔等时间序列统计。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median

from mikan_health.data.models import Release


@dataclass(frozen=True)
class EpisodeEvent:
    """某一集的最早发布时间与该集的变体数。

    Attributes:
        episode: Mikan 集号。
        released_at: 该集最早发布时间；未知为 datetime.min。
        variant_count: 该集包含的发布条目数。
    """

    episode: float
    released_at: datetime
    variant_count: int


def episode_events(releases: list[Release]) -> dict[float, EpisodeEvent]:
    """把某个 (番剧, 字幕组) 的发布记录压成「每集一个时间点」。

    Args:
        releases: 同一 (番剧, 字幕组) 的发布记录。
    """
    buckets: dict[float, list[Release]] = {}
    for release in releases:
        if not release.is_episode or release.episode is None:
            continue
        buckets.setdefault(release.episode, []).append(release)

    events: dict[float, EpisodeEvent] = {}
    for episode, items in buckets.items():
        stamps = [r.published_at for r in items if r.published_at is not None]
        events[episode] = EpisodeEvent(
            episode=episode,
            released_at=min(stamps) if stamps else datetime.min,
            variant_count=len(items),
        )
    return events


def start_episode(releases: list[Release]) -> float | None:
    """所有单集发布中最小的集号；无单集则返回 None。

    Args:
        releases: 待扫描的发布记录。
    """
    episodes = [r.episode for r in releases if r.is_episode and r.episode is not None]
    return min(episodes) if episodes else None


def nominal_datetime(start_date: date, start_ep: float, episode: float) -> datetime:
    """按「起始集日期 + 每集 7 天」推算某集的名义放送时间。

    Args:
        start_date: 起始集对应的日期。
        start_ep: 起始集号。
        episode: 待推算的集号。
    """
    offset_weeks = (episode - start_ep) * 7
    return datetime.combine(start_date, datetime.min.time()) + timedelta(days=offset_weeks)


def delays_hours(
    events: dict[float, EpisodeEvent], start_date: date | None, start_ep: float | None
) -> dict[float, float]:
    """各集相对名义放送时间的延迟（小时）。

    Args:
        events: 集号 -> 每集发布时间点。
        start_date: 起始集对应的日期；None 表示无法计算。
        start_ep: 起始集号；None 表示无法计算。
    """
    if start_date is None or start_ep is None:
        return {}
    return {
        episode: (
            event.released_at - nominal_datetime(start_date, start_ep, episode)
        ).total_seconds()
        / 3600
        for episode, event in events.items()
        if event.released_at != datetime.min
    }


def baseline_groups(
    events_by_group: dict[int, dict[float, EpisodeEvent]],
    baseline: set[int] | None,
) -> dict[int, dict[float, EpisodeEvent]]:
    """按 baseline 过滤出参与比较的字幕组；过滤后为空则退回全部。

    baseline 用于排除先出但用户不会选的生肉/无中文字幕组。

    Args:
        events_by_group: 字幕组 id -> 各集发布点。
        baseline: 参与比较的字幕组 id 集合；None 表示全部参与。
    """
    selected = {
        group_id: events
        for group_id, events in events_by_group.items()
        if baseline is None or group_id in baseline
    }
    return selected or events_by_group


def relative_delays_hours(
    events_by_group: dict[int, dict[float, EpisodeEvent]],
    baseline: set[int] | None = None,
) -> dict[int, dict[float, float]]:
    """相对同番最快组的延迟。

    Args:
        events_by_group: 字幕组 id -> 各集发布点。
        baseline: 参与比较的字幕组 id 集合；None 表示全部参与。
    """
    source = baseline_groups(events_by_group, baseline)

    best: dict[float, datetime] = {}
    for events in source.values():
        for episode, event in events.items():
            if event.released_at == datetime.min:
                continue
            if episode not in best or event.released_at < best[episode]:
                best[episode] = event.released_at
    return {
        group_id: {
            episode: (event.released_at - best[episode]).total_seconds() / 3600
            for episode, event in events.items()
            if episode in best and event.released_at != datetime.min
        }
        for group_id, events in events_by_group.items()
    }


def subtitle_baseline(releases: list[Release]) -> set[int] | None:
    """有**明确**中文字幕标记的字幕组集合；没有则返回 None。

    必须排除 inferred 条目：默认判为繁体的生肉若进基线，会把正片组算成「慢好几天」。

    Args:
        releases: 待扫描的发布记录。
    """
    groups = {release.subtitle_group_id for release in releases if release.has_explicit_chinese}
    return groups or None


def intervals_hours(events: dict[float, EpisodeEvent]) -> list[float]:
    """按发布时间排序后相邻两集的时间间隔（小时）。

    Args:
        events: 集号 -> 每集发布时间点。
    """
    stamps = sorted(e.released_at for e in events.values() if e.released_at != datetime.min)
    return [
        (later - earlier).total_seconds() / 3600
        for earlier, later in zip(stamps, stamps[1:], strict=False)
    ]


def median_or_none(values: list[float]) -> float | None:
    """对非空列表取中位数，空列表返回 None。"""
    return median(values) if values else None


def mad(values: list[float]) -> float | None:
    """中位数绝对偏差（MAD）；空列表返回 None。"""
    if not values:
        return None
    center = median(values)
    return median([abs(v - center) for v in values])
