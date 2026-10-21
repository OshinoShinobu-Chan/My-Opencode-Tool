"""字幕组打分与排名：速度/语言/稳定性加权并分档推荐。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from mikan_health.core.metrics import SubgroupMetrics
from mikan_health.core.policy import Policy
from mikan_health.core.releases import LANG_DUAL
from mikan_health.core.risk import TIER_A, TIER_B, TIER_C, TIER_U, RiskAssessment


@dataclass
class SubgroupHealth:
    """单个字幕组的健康度（指标 + 风险 + 评分）。

    Attributes:
        subgroup_id: 字幕组 id。
        name: 字幕组名称。
        metrics: 该组汇总指标。
        risk: 停更风险评估。
        speed_score: 速度分（含覆盖率 / 攒集 / 停滞折算）。
        stability: 稳定性分（由抖动换算）。
        lang_match: 语言匹配分。
        score: 三项加权总分。
        evidence: 人类可读的评分依据。
    """

    subgroup_id: int
    name: str
    metrics: SubgroupMetrics
    risk: RiskAssessment = field(default_factory=RiskAssessment)
    speed_score: float = 0.0
    stability: float = 0.0
    lang_match: float = 0.0
    score: float = 0.0
    evidence: list[str] = field(default_factory=list)

    @property
    def tier(self) -> str:
        return self.risk.tier


@dataclass
class Ranking:
    """一次排名的结果（候选、合格、剔除与推荐）。

    Attributes:
        mode: 运行模式 fast / normal。
        candidates: 全部候选（含被剔除者）。
        eligible: 通过过滤的候选。
        rejected: 被剔除的候选。
        recommendation: 推荐字幕组；无可用为 None。
        decision_required: 是否需要人工确认。
        decision_options: 人工确认的候选（推荐与挑战者）。
        warnings: 过程提示。
    """

    mode: str
    candidates: list[SubgroupHealth]
    eligible: list[SubgroupHealth]
    rejected: list[SubgroupHealth]
    recommendation: SubgroupHealth | None = None
    decision_required: bool = False
    decision_options: list[SubgroupHealth] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def absolute_speed(delay_p50_h: float | None, policy: Policy) -> float:
    """绝对速度分：延迟越大衰减越低，范围 (0,1]。

    Args:
        delay_p50_h: 相对名义/精确放送日的中位延迟（小时）；None 返回 0.5。
        policy: 评估参数（含速度衰减时间常数）。
    """
    if delay_p50_h is None:
        return 0.5
    return math.exp(-max(delay_p50_h, 0.0) / policy.speed_T_hours)


def rank_of(value: float, values: list[float]) -> float:
    """相对速度分：延迟越小分越高（最小者 1.0，最大者 0.0）。

    Args:
        value: 当前组的中位相对延迟（小时）。
        values: 所有合格组的中位相对延迟列表。
    """
    if len(values) <= 1:
        return 1.0
    lower = sum(1 for v in values if v < value)
    return 1.0 - lower / (len(values) - 1)


def relative_speed(rel_p50_h: float | None, values: list[float], policy: Policy) -> float:
    """相对速度分 = 名次分与量级衰减分各半。

    名次分保留排序，衰减分体现差距大小，避免把「快 0.1 小时」与「快 3 天」等同。

    Args:
        rel_p50_h: 该组的中位相对延迟（小时）；None 返回 0.5。
        values: 所有合格组的中位相对延迟列表。
        policy: 评估参数（含速度衰减时间常数）。
    """
    if rel_p50_h is None:
        return 0.5
    rank_part = rank_of(rel_p50_h, values)
    decay_part = math.exp(-max(rel_p50_h, 0.0) / policy.speed_T_hours)
    return 0.5 * rank_part + 0.5 * decay_part


def compute_scores(health: SubgroupHealth, policy: Policy, rel_values: list[float]) -> None:
    """计算并写回该字幕组的速度/语言/稳定性与总分。

    Args:
        health: 待打分的字幕组健康度（原地更新）。
        policy: 评估参数（权重、惩罚系数、语言分）。
        rel_values: 所有合格组的中位相对延迟列表，用于名次分。
    """
    metrics = health.metrics
    rel = metrics.speed.rel_p50_h
    abs_score = absolute_speed(metrics.speed.delay_p50_h, policy)
    rel_score = relative_speed(rel, rel_values, policy)
    speed = policy.speed_abs_weight * abs_score + policy.speed_rel_weight * rel_score

    jitter = metrics.speed.jitter_h or 0.0
    stability = max(0.0, 1.0 - jitter / policy.jitter_scale_h)

    ratio = metrics.coverage.coverage_ratio
    if ratio < 1.0:
        speed *= ratio
        health.evidence.append(
            f"只覆盖了 {metrics.coverage.released_eps}/{metrics.coverage.peer_episode_count} 集，"
            "速度分按覆盖率折算"
        )
    if metrics.speed.catchup_weeks >= policy.catchup_penalty_weeks:
        speed *= policy.catchup_speed_penalty
        health.evidence.append(f"有 {metrics.speed.catchup_weeks} 次攒集连发")
    if metrics.coverage.stale_ratio and metrics.coverage.stale_ratio > policy.stall_ratio_threshold:
        speed *= policy.stall_speed_penalty
        health.evidence.append(f"停滞已达自身节奏的 {metrics.coverage.stale_ratio:.1f} 倍")

    health.speed_score = round(speed, 4)
    health.stability = round(stability, 4)
    health.lang_match = round(_language_match(health, policy), 4)
    if policy.mode == "fast" and not policy.fast_tie_prefers_stability:
        # 13.3 Q7：关闭后 fast 模式完全按速度，不以稳定性破并列
        stability_weight = 0.0
        speed_weight = policy.weights.speed + policy.weights.stability
    else:
        speed_weight = policy.weights.speed
        stability_weight = policy.weights.stability
    health.score = round(
        speed_weight * health.speed_score
        + policy.weights.lang * health.lang_match
        + stability_weight * health.stability,
        4,
    )


def _language_match(health: SubgroupHealth, policy: Policy) -> float:
    """按语言标签与内封加分计算语言匹配分，上限 1.0。"""
    metrics = health.metrics
    best = 0.0
    for language in metrics.languages:
        score = policy.lang_score(language)
        if language == LANG_DUAL and metrics.has_dual_zh_jp:
            score = max(score, policy.lang_score(LANG_DUAL))
        best = max(best, score)
    if "内封" in metrics.sub_types:
        best += policy.softsub_bonus
    return min(best, 1.0)


def rank(
    candidates: list[SubgroupHealth],
    policy: Policy,
    mode: str,
    require_simplified: bool,
) -> Ranking:
    """过滤不合格组、打分并按风险档位给出推荐与需人工确认项。

    Args:
        candidates: 全部候选字幕组健康度。
        policy: 评估参数（阈值、权重、惩罚系数）。
        mode: 运行模式 fast / normal。
        require_simplified: 是否要求必须有简体字幕。
    """
    warnings: list[str] = []
    eligible: list[SubgroupHealth] = []
    rejected: list[SubgroupHealth] = []

    for health in candidates:
        reason = _reject_reason(health, policy, require_simplified)
        if reason:
            health.evidence.append(f"已剔除：{reason}")
            rejected.append(health)
        else:
            eligible.append(health)

    rel_values = [
        h.metrics.speed.rel_p50_h for h in eligible if h.metrics.speed.rel_p50_h is not None
    ]
    for health in eligible:
        compute_scores(health, policy, rel_values)

    tiers: dict[str, list[SubgroupHealth]] = {TIER_A: [], TIER_B: [], TIER_U: [], TIER_C: []}
    for health in eligible:
        tiers[health.tier].append(health)

    for group in tiers.values():
        group.sort(key=lambda h: h.score, reverse=True)

    if require_simplified and not any(h.metrics.has_simplified for h in candidates):
        warnings.append("没有任何字幕组提供简体字幕")

    if tiers[TIER_C]:
        warnings.append(
            f"{len(tiers[TIER_C])} 个字幕组风险过高被剔除："
            + "、".join(h.name for h in tiers[TIER_C])
        )

    if not tiers[TIER_A]:
        for tier in (TIER_U, TIER_B, TIER_C):
            if tiers[tier]:
                warnings.append(
                    f"没有低风险候选，回退到 {tier} 档（{tiers[tier][0].name}）；建议人工确认"
                )
                break
        chosen = tiers[TIER_U] + tiers[TIER_B] + tiers[TIER_C]
        recommendation = chosen[0] if chosen else None
        return Ranking(
            mode=mode,
            candidates=candidates,
            eligible=eligible,
            rejected=rejected,
            recommendation=recommendation,
            decision_required=True,
            decision_options=[],
            warnings=warnings,
        )

    best_a = tiers[TIER_A][0]
    alternatives = tiers[TIER_B] + tiers[TIER_U]
    challenger = alternatives[0] if alternatives else None
    if challenger is not None and challenger.score - best_a.score > policy.speed_gain_threshold:
        warnings.append(
            f"{challenger.name} 评分高出 {challenger.score - best_a.score:.3f}，"
            f"但停更风险等级为 {challenger.risk.level}；需要你确认"
        )
        return Ranking(
            mode=mode,
            candidates=candidates,
            eligible=eligible,
            rejected=rejected,
            recommendation=best_a,
            decision_required=True,
            decision_options=[best_a, challenger],
            warnings=warnings,
        )

    return Ranking(
        mode=mode,
        candidates=candidates,
        eligible=eligible,
        rejected=rejected,
        recommendation=best_a,
        decision_required=False,
        warnings=warnings,
    )


def _reject_reason(health: SubgroupHealth, policy: Policy, require_simplified: bool) -> str | None:
    """返回剔除原因；合格时返回 None。

    Args:
        health: 待判定的字幕组健康度。
        policy: 评估参数（含高风险剔除开关）。
        require_simplified: 是否要求必须有简体字幕。
    """
    metrics = health.metrics
    if metrics.episode_count == 0:
        return "没有可用的单集发布（只有合集或空）"
    if not (metrics.has_explicit_simplified or metrics.has_explicit_traditional):
        return "没有任何明确标注的中文字幕版本（生肉/未标注）"
    if require_simplified and not metrics.has_simplified:
        return "没有任何简体字幕版本"
    if policy.high_risk_veto and health.risk.tier == TIER_C:
        return f"停更风险 {health.risk.level}（{health.risk.risk:.2f}）"
    return None
