"""停更/弃番风险评估：依据历史作品与当前活跃度定级。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from mikan_health.core.policy import Policy

RISK_LOW = "low"
RISK_MID = "mid"
RISK_UNKNOWN = "unknown"
RISK_HIGH = "high"

TIER_A = "A"
TIER_B = "B"
TIER_U = "U"
TIER_C = "C"


@dataclass
class WorkRecord:
    """一部历史作品的发布记录，用于评估弃番。

    Attributes:
        bangumi_id: 作品番剧 id。
        title: 作品标题。
        season_date: 季度（如 "2024.4"）。
        group_final_episode: 该组在该作品的最终集号。
        fact_final_episode: 全组最高的最终集号（事实进度）。
        group_last_release: 该组在该作品的最近发布时间。
        fact_last_release: 所有组的最近发布时间。
        rel_p50_h: 相对最快组的中位延迟（小时）。
    """

    bangumi_id: int
    title: str = ""
    season_date: str = ""
    group_final_episode: float | None = None
    fact_final_episode: float | None = None
    group_last_release: datetime | None = None
    fact_last_release: datetime | None = None
    rel_p50_h: float | None = None


@dataclass
class RiskAssessment:
    """风险评分结果（risk/level/tier/confidence 与证据）。

    Attributes:
        risk: 0-1 停更风险值；无法评估为 None。
        level: 风险等级 low / mid / high / unknown。
        tier: 推荐档位 A / B / U / C。
        confidence: 置信度 high / medium / low（取决于可判定作品数）。
        drop_rate: 历史弃番率估计（含平滑）；无样本为 None。
        evidence: 人类可读的判定依据。
    """

    risk: float | None = None
    level: str = RISK_UNKNOWN
    tier: str = TIER_U
    confidence: str = "low"
    drop_rate: float | None = None
    evidence: list[str] = field(default_factory=list)


def numbering_mismatch(group_final: float, fact_final: float) -> bool:
    """不同字幕组对同一部番可能用不同编号体系（如 7 vs 48）。

    当该组集数明显低于他组最高集数时，更可能是编号体系不同而不是弃番。

    Args:
        group_final: 该组在该作品的最终集号。
        fact_final: 全组最高的最终集号（事实进度）。
    """
    if fact_final <= 0:
        return False
    if group_final >= fact_final:
        return False
    return group_final < 0.6 * fact_final


def judge_work(
    record: WorkRecord,
    now: datetime,
    drop_gap_days: float,
    subgroup_active: bool,
) -> bool | None:
    """返回 True=疑似弃番，False=正常，None=无法判定。

    只判定 fact 侧已安静的作品；仍在连载的落后由 peer_gap 负责；集数对不上则跳过。

    Args:
        record: 该组在某历史作品上的记录。
        now: 计算基准时间。
        drop_gap_days: 多少天无发布才纳入弃番判定。
        subgroup_active: 该组当前是否仍在活跃更新。
    """
    if record.group_final_episode is None or record.fact_final_episode is None:
        return None
    if record.group_last_release is None or record.fact_last_release is None:
        return None
    if (now - record.fact_last_release).days < drop_gap_days:
        return None
    if record.group_final_episode >= record.fact_final_episode:
        return False
    if numbering_mismatch(record.group_final_episode, record.fact_final_episode):
        return None
    quiet_days = (now - record.group_last_release).total_seconds() / 86400
    if quiet_days < drop_gap_days:
        return None
    return bool(subgroup_active)


def assess_risk(
    records: list[WorkRecord],
    policy: Policy,
    now: datetime,
    last_release_days: float | None,
    accepts_new_season: bool,
    peer_gap_h: float | None,
    stall_lag_days: float,
) -> RiskAssessment:
    """综合历史弃番率与当前活跃度，给出停更风险评级。

    Args:
        records: 该组的历史作品记录。
        policy: 评估参数（阈值、惩罚系数）。
        now: 计算基准时间。
        last_release_days: 该组最近一次发布距今天数；None 表示未知。
        accepts_new_season: 最近两季是否接过新番。
        peer_gap_h: 落后同番最快组的小时数；None 表示无法比较。
        stall_lag_days: 落后多少天算「停滞」。
    """
    judged = 0
    drops = 0
    unknown = 0
    quiet = last_release_days is not None and last_release_days >= policy.silent_days
    group_silent = quiet and not accepts_new_season
    group_active = not group_silent

    for record in records:
        verdict = judge_work(record, now, policy.drop_gap_days, group_active)
        if verdict is None:
            unknown += 1
            continue
        judged += 1
        if verdict:
            drops += 1

    drop_rate = (drops + 1) / (judged + 2) if judged else None
    confidence = "high" if judged >= 8 else ("medium" if judged >= 3 else "low")

    evidence: list[str] = []
    if judged:
        evidence.append(f"最近 {judged} 部已完结作品中有 {drops} 部疑似弃番")
    else:
        evidence.append("没有可判定的历史作品")

    if drop_rate is None:
        if accepts_new_season and last_release_days is not None and last_release_days < 7:
            return RiskAssessment(
                risk=None,
                level=RISK_LOW,
                tier=TIER_A,
                confidence="low",
                evidence=[*evidence, "历史样本不足，但当前活跃且仍在接新番"],
            )
        return RiskAssessment(
            risk=None,
            level=RISK_UNKNOWN,
            tier=TIER_U,
            confidence="low",
            evidence=evidence,
        )

    risk = drop_rate
    suspect_stall = (
        peer_gap_h is not None and peer_gap_h >= stall_lag_days * 24 and not group_silent
    )

    if suspect_stall and peer_gap_h is not None:
        risk *= policy.stall_penalty
        evidence.append(
            f"本番落后同番最快组 {peer_gap_h / 24:.1f} 天，但该组仍在别处更新（疑似弃坑）"
        )
    if group_silent:
        risk *= policy.silent_penalty
        evidence.append(
            f"全组已 {last_release_days:.0f} 天没有任何发布，且最近两季未接新番（疑似整组停更）"
        )
    elif quiet:
        evidence.append(f"历史作品最近一次发布在 {last_release_days:.0f} 天前，但该组仍在接新番")
    if not accepts_new_season:
        risk *= policy.inactive_penalty
        evidence.append("最近两季未接任何新番")
    if judged and drops == 0:
        risk *= policy.clean_history_bonus
        evidence.append("历史作品全部正常完结")

    risk = max(0.0, min(risk, 1.0))
    level = policy.risk_tier_of(risk)
    tier = {RISK_LOW: TIER_A, RISK_MID: TIER_B, RISK_HIGH: TIER_C}[level]
    if confidence == "low" and tier == TIER_A:
        tier = TIER_U if policy.unknown_tier_policy == "conservative" else TIER_A
    if unknown and not evidence:
        evidence.append(f"{unknown} 部作品因数据不足无法判定")

    return RiskAssessment(
        risk=risk,
        level=level,
        tier=tier,
        confidence=confidence,
        drop_rate=drop_rate,
        evidence=evidence,
    )


def season_is_recent(season_date: str, now: date, months: int = 5) -> bool:
    """判断 season_date（如 "2024.4"）是否落在最近 months 个月内。

    Args:
        season_date: 季度字符串，形如 "2024.4"。
        now: 计算基准日期。
        months: 回看月数上限。
    """
    if not season_date or "." not in season_date:
        return False
    year, month = season_date.split(".", 1)
    try:
        anchor = date(int(year), max(1, min(int(month), 12)), 1)
    except ValueError:
        return False
    months_ago = (now.year - anchor.year) * 12 + (now.month - anchor.month)
    return 0 <= months_ago <= months
