"""健康度分析编排：汇总发布与历史记录并产出排名。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from mikan_health.core.airtime import Schedule, build_anchor
from mikan_health.core.episodes import (
    EpisodeEvent,
    episode_events,
    start_episode,
    subtitle_baseline,
)
from mikan_health.core.metrics import compute_metrics
from mikan_health.core.policy import Policy
from mikan_health.core.risk import (
    RiskAssessment,
    WorkRecord,
    assess_risk,
    season_is_recent,
)
from mikan_health.core.score import Ranking, SubgroupHealth, rank
from mikan_health.core.taxonomy import Taxonomy
from mikan_health.data.models import BangumiDetail, Release


@dataclass
class HealthInputs:
    """analyze 所需的全部输入（番剧详情、发布、历史作品等）。

    Attributes:
        detail: 番剧详情。
        releases: 用于分析的发布记录（页面可见或完整历史）。
        works: 字幕组 id -> 历史作品记录列表（深查结果）。
        subgroup_last_release: 字幕组 id -> 最近一次发布时间。
        subgroup_seasons: 字幕组 id -> 参与过的季度列表。
        schedule: Bangumi 精确放送日程；未启用为 None。
        now: 计算基准时间。
    """

    detail: BangumiDetail
    releases: list[Release]
    works: dict[int, list[WorkRecord]] = field(default_factory=dict)
    subgroup_last_release: dict[int, datetime | None] = field(default_factory=dict)
    subgroup_seasons: dict[int, list[str]] = field(default_factory=dict)
    schedule: Schedule | None = None
    now: datetime = field(default_factory=datetime.now)


def analyze(
    inputs: HealthInputs,
    policy: Policy,
    taxonomy: Taxonomy | None = None,
    deep_checked: set[int] | None = None,
) -> Ranking:
    """分析一部番剧各字幕组的健康度并给出排名。

    Args:
        inputs: 番剧详情、发布与历史等全部输入。
        policy: 评估参数（阈值、权重、惩罚系数）。
        taxonomy: 语言/字幕类型词典；None 用默认。
        deep_checked: 已做历史深查的字幕组 id 集合；None 表示不做限制。
    """
    now = inputs.now
    start_date: date | None = inputs.detail.start_date

    by_group: dict[int, list[Release]] = {}
    for release in inputs.releases:
        by_group.setdefault(release.subtitle_group_id, []).append(release)

    events_by_group = {
        subgroup_id: episode_events(items) for subgroup_id, items in by_group.items()
    }
    start_ep = start_episode(inputs.releases)
    baseline = subtitle_baseline(inputs.releases)

    if inputs.schedule is not None and inputs.schedule.airdates:
        normalized: dict[int, dict[float, EpisodeEvent]] = {}
        for subgroup_id, events in events_by_group.items():
            anchor = build_anchor(subgroup_id, list(events), inputs.schedule)
            normalized[subgroup_id] = {
                float(ordinal): events[number]
                for number, ordinal in anchor.ordinal_of.items()
                if number in events
            }
        events_by_group = normalized
        start_ep = 1.0

    healths: list[SubgroupHealth] = []
    for subgroup in inputs.detail.subgroups:
        items = by_group.get(subgroup.subtitle_group_id, [])
        metrics = compute_metrics(
            subgroup_id=subgroup.subtitle_group_id,
            name=subgroup.name,
            releases=items,
            events_by_group=events_by_group,
            start_date=start_date,
            start_ep=start_ep,
            now=now,
            catchup_hours=policy.catchup_interval_hours,
            min_interval_hours=policy.min_interval_hours,
            baseline=baseline,
            schedule=inputs.schedule,
        )
        metrics.publish_group_id = subgroup.publish_group_id
        healths.append(
            SubgroupHealth(
                subgroup_id=subgroup.subtitle_group_id,
                name=subgroup.name,
                metrics=metrics,
            )
        )

    for health in healths:
        _attach_risk(
            health,
            inputs,
            policy,
            now,
            deep_checked=deep_checked,
        )

    return rank(
        healths,
        policy,
        mode=policy.mode,
        require_simplified=policy.mode == "normal",
    )


def _attach_risk(
    health: SubgroupHealth,
    inputs: HealthInputs,
    policy: Policy,
    now: datetime,
    deep_checked: set[int] | None,
) -> None:
    """为单个字幕组评估停更风险并写入 evidence。

    Args:
        health: 待评估的字幕组健康度。
        inputs: 番剧详情、发布与历史等全部输入。
        policy: 评估参数（阈值、权重、惩罚系数）。
        now: 计算基准时间。
        deep_checked: 已做历史深查的字幕组 id 集合；None 表示全部已深查。
    """
    subgroup_id = health.subgroup_id
    if deep_checked is not None and subgroup_id not in deep_checked:
        health.risk = RiskAssessment(
            level="unknown",
            tier="U",
            confidence="low",
            evidence=["未做历史深查（评分未进入前列）"],
        )
        health.evidence.extend(_runtime_evidence(health))
        return

    last_release = _latest(inputs.subgroup_last_release.get(subgroup_id), _own_last_release(health))
    last_release_days = (
        (now - last_release).total_seconds() / 86400 if last_release is not None else None
    )
    seasons = inputs.subgroup_seasons.get(subgroup_id, [])
    accepts_new = any(season_is_recent(season, now.date()) for season in seasons)

    health.risk = assess_risk(
        records=inputs.works.get(subgroup_id, []),
        policy=policy,
        now=now,
        last_release_days=last_release_days,
        accepts_new_season=accepts_new,
        peer_gap_h=health.metrics.coverage.peer_gap_h,
        stall_lag_days=policy.stall_lag_days,
    )
    if last_release_days is not None:
        health.risk.evidence.append(f"该组最近一次发布在 {last_release_days:.0f} 天前")
    health.evidence.extend(_runtime_evidence(health))


def _own_last_release(health: SubgroupHealth) -> datetime | None:
    """该组在本番中最近一次发布时间；无则 None。"""
    stamps = [
        event.released_at
        for event in health.metrics.events.values()
        if event.released_at != datetime.min
    ]
    return max(stamps) if stamps else None


def _latest(*moments: datetime | None) -> datetime | None:
    values = [moment for moment in moments if moment is not None]
    return max(values) if values else None


def _runtime_evidence(health: SubgroupHealth) -> list[str]:
    """根据速度与覆盖指标生成人类可读的证据句。"""
    evidence: list[str] = []
    speed = health.metrics.speed
    coverage = health.metrics.coverage
    if speed.rel_p50_h is not None:
        evidence.append(f"相对同番最快组中位延迟 {speed.rel_p50_h:.2f} 小时")
    if speed.delay_p50_h is not None:
        evidence.append(f"相对名义播出日中位延迟 {speed.delay_p50_h / 24:.2f} 天")
    if coverage.peer_gap_h:
        evidence.append(f"当前落后同番最快组 {coverage.peer_gap_h / 24:.1f} 天")
    return evidence
