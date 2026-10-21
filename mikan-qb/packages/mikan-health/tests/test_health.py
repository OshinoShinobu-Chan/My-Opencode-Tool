from __future__ import annotations

from datetime import date, datetime, timedelta

from mikan_health.core.episodes import (
    episode_events,
    relative_delays_hours,
    subtitle_baseline,
)
from mikan_health.core.health import HealthInputs, analyze
from mikan_health.core.metrics import SpeedMetrics, compute_metrics
from mikan_health.core.policy import load_profile
from mikan_health.core.releases import parse_title
from mikan_health.core.risk import (
    TIER_A,
    TIER_B,
    TIER_C,
    WorkRecord,
    assess_risk,
    judge_work,
    numbering_mismatch,
    season_is_recent,
)
from mikan_health.core.score import SubgroupHealth, rank
from mikan_health.core.taxonomy import Taxonomy
from mikan_health.data.models import BangumiDetail, Release, Subgroup

TAX = Taxonomy()
START = date(2026, 1, 4)
NOW = datetime(2026, 3, 20, 12, 0)
BASE = datetime(2026, 1, 4, 23, 0)


def mk_release(
    subgroup_id: int,
    episode: int,
    days: float,
    tags: str,
    subgroup_label: str = "某组",
) -> Release:
    title = f"[{subgroup_label}] 番名 [{episode:02d}][1080p][{tags}]"
    parsed = parse_title(title, TAX)
    return Release(
        bangumi_id=1,
        subtitle_group_id=subgroup_id,
        title=title,
        episode=float(episode),
        episode_raw=f"{episode:02d}",
        is_pack=parsed.is_pack,
        is_extra=parsed.is_extra,
        language=parsed.language,
        language_source=parsed.language_source,
        sub_type=parsed.sub_type,
        is_dual_zh_jp=parsed.is_dual_zh_jp,
        subgroup_label=parsed.subgroup_label,
        published_at=BASE + timedelta(days=days),
    )


def build_releases(spec: dict[int, tuple[float, str]], episodes: int = 10) -> list[Release]:
    releases: list[Release] = []
    for subgroup_id, (lag_days, tags) in spec.items():
        for episode in range(1, episodes + 1):
            releases.append(mk_release(subgroup_id, episode, (episode - 1) * 7 + lag_days, tags))
    return releases


def build_detail(spec: dict[int, str]) -> BangumiDetail:
    return BangumiDetail(
        bangumi_id=1,
        title="测试番剧",
        start_date=START,
        subgroups=[
            Subgroup(subtitle_group_id=sid, name=name, publish_group_id=100 + sid)
            for sid, name in spec.items()
        ],
    )


def test_episode_events_dedupe_by_episode() -> None:
    releases = [
        mk_release(1, 1, 0, "简体内嵌"),
        mk_release(1, 1, 0.2, "简繁内封"),
        mk_release(1, 2, 7, "简体内嵌"),
    ]
    events = episode_events(releases)
    assert set(events) == {1.0, 2.0}
    assert events[1.0].released_at == BASE
    assert events[1.0].variant_count == 2


def test_relative_delay_excludes_raw_baseline() -> None:
    releases = [
        mk_release(1, 1, 0, "简体", "正片组"),
        mk_release(1, 2, 7, "简体", "正片组"),
        mk_release(9, 1, -2.0, "RAW", "生肉组"),
        mk_release(9, 2, 5.0, "RAW", "生肉组"),
    ]
    events = {
        sid: episode_events([r for r in releases if r.subtitle_group_id == sid]) for sid in (1, 9)
    }
    baseline = subtitle_baseline(releases)
    assert baseline == {1}

    rel = relative_delays_hours(events, baseline)
    assert rel[1][1.0] == 0.0
    assert rel[9][1.0] < 0


def test_compute_metrics_speed_and_coverage() -> None:
    releases = build_releases({1: (0.0, "简体"), 2: (2.0, "繁体")})
    events_by_group = {
        sid: episode_events([r for r in releases if r.subtitle_group_id == sid]) for sid in (1, 2)
    }
    metrics = compute_metrics(
        subgroup_id=1,
        name="快的组",
        releases=[r for r in releases if r.subtitle_group_id == 1],
        events_by_group=events_by_group,
        start_date=START,
        start_ep=1.0,
        now=NOW,
        baseline={1, 2},
    )
    speed = metrics.speed
    assert speed.delay_p50_h is not None
    assert speed.rel_p50_h == 0.0
    assert metrics.coverage.missing_vs_peer == 0
    assert metrics.coverage.coverage_ratio == 1.0
    assert metrics.coverage.peer_gap_h == 0.0

    slow = compute_metrics(
        subgroup_id=2,
        name="慢的组",
        releases=[r for r in releases if r.subtitle_group_id == 2],
        events_by_group=events_by_group,
        start_date=START,
        start_ep=1.0,
        now=NOW,
        baseline={1, 2},
    )
    assert slow.speed.rel_p50_h == 48.0
    assert slow.speed.rel_p50_h > speed.rel_p50_h


def _record(
    bangumi_id: int, group_final: float, fact_final: float, quiet_days: float
) -> WorkRecord:
    last = NOW - timedelta(days=quiet_days)
    return WorkRecord(
        bangumi_id=bangumi_id,
        title=f"作品{bangumi_id}",
        group_final_episode=group_final,
        fact_final_episode=fact_final,
        group_last_release=last,
        fact_last_release=last,
    )


def test_judge_work_categories() -> None:
    policy = load_profile("personal-normal")
    assert judge_work(_record(1, 12, 12, 60), NOW, 14, True) is False
    assert judge_work(_record(2, 8, 12, 60), NOW, 14, True) is True
    assert judge_work(_record(3, 12, 12, 3), NOW, 14, True) is None
    assert judge_work(_record(4, 7, 48, 60), NOW, 14, True) is None
    assert judge_work(_record(5, 11, 12, 60), NOW, 14, False) is False
    assert policy.drop_gap_days == 14


def test_numbering_mismatch() -> None:
    assert numbering_mismatch(7, 48) is True
    assert numbering_mismatch(21, 22) is False
    assert numbering_mismatch(12, 12) is False


def test_assess_risk_clean_history_is_low() -> None:
    policy = load_profile("personal-normal")
    records = [_record(i, 12, 12, 60) for i in range(1, 9)]
    assessment = assess_risk(
        records,
        policy,
        NOW,
        last_release_days=1.0,
        accepts_new_season=True,
        peer_gap_h=0.0,
        stall_lag_days=10,
    )
    assert assessment.level == "low"
    assert assessment.tier == TIER_A
    assert assessment.confidence == "high"
    assert assessment.risk is not None and assessment.risk < 0.15


def test_assess_risk_dropper_is_high() -> None:
    policy = load_profile("personal-normal")
    records = [_record(i, 8, 12, 60) for i in range(1, 9)]
    assessment = assess_risk(
        records,
        policy,
        NOW,
        last_release_days=1.0,
        accepts_new_season=True,
        peer_gap_h=0.0,
        stall_lag_days=10,
    )
    assert assessment.level == "high"
    assert assessment.tier == TIER_C
    assert assessment.risk is not None and assessment.risk > 0.35


def test_assess_risk_suspect_stall_multiplier() -> None:
    policy = load_profile("personal-normal")
    records = [_record(i, 12, 12, 60) for i in range(1, 9)]
    calm = assess_risk(records, policy, NOW, 1.0, True, peer_gap_h=0.0, stall_lag_days=10)
    stalling = assess_risk(records, policy, NOW, 1.0, True, peer_gap_h=20 * 24, stall_lag_days=10)
    assert stalling.risk is not None and calm.risk is not None
    assert stalling.risk > calm.risk
    assert any("落后同番最快组" in item for item in stalling.evidence)


def test_season_is_recent() -> None:
    assert season_is_recent("2026.07", date(2026, 9, 18)) is True
    assert season_is_recent("2025.01", date(2026, 9, 18)) is False


def _health(subgroup_id: int, name: str, score_rel: float, risk_level: str) -> SubgroupHealth:
    from mikan_health.core.risk import RiskAssessment

    metrics = SubgroupMetricsStub(subgroup_id, name, score_rel)
    level = {"low": "low", "mid": "mid", "high": "high", "unknown": "unknown"}[risk_level]
    tier = {"low": TIER_A, "mid": TIER_B, "high": TIER_C, "unknown": "U"}[risk_level]
    risk = RiskAssessment(
        risk={"low": 0.05, "mid": 0.2, "high": 0.6, "unknown": None}[risk_level],
        level=level,
        tier=tier,
        confidence="high",
    )
    health = SubgroupHealth(subgroup_id=subgroup_id, name=name, metrics=metrics, risk=risk)
    health._fixed_rel = score_rel * 100
    return health


class SubgroupMetricsStub:
    def __init__(self, subgroup_id: int, name: str, rel: float) -> None:
        from mikan_health.core.metrics import CoverageMetrics, SubgroupMetrics

        self._impl = SubgroupMetrics(
            subgroup_id=subgroup_id,
            name=name,
            languages=("简日双语",),
            sub_types=("内封",),
            has_simplified=True,
            has_explicit_simplified=True,
            has_dual_zh_jp=True,
            episode_count=10,
        )
        self._impl.speed = SpeedMetrics(rel_p50_h=rel, delay_p50_h=rel, jitter_h=1.0)
        self._impl.coverage = CoverageMetrics(released_eps=10, peer_episode_count=10)

    def __getattr__(self, item):
        return getattr(self._impl, item)


def test_rank_prefers_low_risk_over_faster_mid_risk() -> None:
    policy = load_profile("personal-normal")
    fast_risky = _health(1, "快但有风险", 0.0, "mid")
    slow_safe = _health(2, "稳但稍慢", 0.5, "low")

    ranking = rank([fast_risky, slow_safe], policy, mode="normal", require_simplified=True)
    assert ranking.recommendation is not None
    assert ranking.recommendation.subgroup_id == 2
    assert ranking.rejected == []
    assert ranking.decision_required is False


def test_rank_flags_decision_when_b_much_better() -> None:
    policy = load_profile("personal-normal")
    fast_risky = _health(1, "快但有风险", 0.0, "mid")
    slow_safe = _health(2, "稳但很慢", 120.0, "low")

    ranking = rank([fast_risky, slow_safe], policy, mode="normal", require_simplified=True)
    assert ranking.recommendation is not None
    assert ranking.recommendation.subgroup_id == 2
    assert ranking.decision_required is True
    assert {h.subgroup_id for h in ranking.decision_options} == {1, 2}


def test_rank_rejects_high_risk() -> None:
    policy = load_profile("personal-normal")
    risky = _health(1, "弃番组", 0.0, "high")
    normal = _health(2, "正常组", 100.0, "low")

    ranking = rank([risky, normal], policy, mode="normal", require_simplified=True)
    assert [h.subgroup_id for h in ranking.rejected] == [1]
    assert ranking.recommendation is not None
    assert ranking.recommendation.subgroup_id == 2


def test_high_risk_veto_can_be_disabled() -> None:
    """profile 的 high_risk_veto=false 时，tier C 也被保留为候选（只是排在最后）。"""
    policy = load_profile("personal-normal")
    lenient = policy.model_copy(update={"high_risk_veto": False})
    risky = _health(1, "弃番组", 0.0, "high")
    normal = _health(2, "正常组", 100.0, "low")

    assert rank([risky], policy, mode="normal", require_simplified=True).rejected

    ranking = rank([risky, normal], lenient, mode="normal", require_simplified=True)
    assert [h.subgroup_id for h in ranking.rejected] == []
    assert ranking.recommendation is not None
    assert ranking.recommendation.subgroup_id == 2


def test_rank_rejects_group_without_simplified() -> None:
    policy = load_profile("personal-normal")
    trad_only = _health(1, "只有繁体", 0.0, "low")
    trad_only.metrics.languages = ("繁体",)
    trad_only.metrics.has_simplified = False
    trad_only.metrics.has_explicit_simplified = False
    trad_only.metrics.has_explicit_traditional = True
    trad_only.metrics.has_dual_zh_jp = False

    ranking = rank([trad_only], policy, mode="normal", require_simplified=True)
    assert ranking.rejected
    assert ranking.warnings


def test_analyze_end_to_end_wires_metrics_and_ranking() -> None:
    policy = load_profile("personal-normal")
    spec = {1: "快组", 2: "慢组"}
    detail = build_detail(spec)
    releases = build_releases({1: (0.0, "简日双语"), 2: (3.0, "简体内嵌")})

    inputs = HealthInputs(detail=detail, releases=releases, now=NOW)
    ranking = analyze(inputs, policy, TAX, deep_checked=set())
    assert len(ranking.candidates) == 2
    fast = next(h for h in ranking.candidates if h.subgroup_id == 1)
    slow = next(h for h in ranking.candidates if h.subgroup_id == 2)
    assert fast.metrics.speed.rel_p50_h == 0.0
    assert slow.metrics.speed.rel_p50_h == 72.0
    assert fast.lang_match > slow.lang_match
    assert ranking.recommendation is not None
    assert ranking.recommendation.subgroup_id == 1
