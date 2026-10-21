from __future__ import annotations

import pytest

from mikan_health.core.policy import Policy, load_profile, profile_names


def test_profiles_present() -> None:
    names = profile_names()
    assert "personal-normal" in names
    assert "personal-fast" in names
    assert "objective" in names


def test_personal_normal_weights() -> None:
    policy = load_profile("personal-normal")
    assert policy.mode == "normal"
    assert policy.weights.lang == pytest.approx(0.45)
    assert policy.weights.total() == pytest.approx(1.0)
    assert policy.lang_score("简日双语") > policy.lang_score("简繁日")
    assert policy.lang_score("简繁日") > policy.lang_score("简体") > policy.lang_score("繁体")
    assert policy.lang_score(None) == 0.0
    assert policy.lang_score("不存在的标签") == 0.0


def test_personal_fast_has_no_lang_weight() -> None:
    policy = load_profile("personal-fast")
    assert policy.mode == "fast"
    assert policy.weights.lang == 0.0


def test_risk_tier_boundaries() -> None:
    policy = load_profile("personal-normal")
    assert policy.risk_tier_of(0.0) == "low"
    assert policy.risk_tier_of(0.1499) == "low"
    assert policy.risk_tier_of(0.15) == "mid"
    assert policy.risk_tier_of(0.35) == "mid"
    assert policy.risk_tier_of(0.3501) == "high"


def test_risk_tier_veto_enabled_by_default() -> None:
    assert load_profile("personal-normal").high_risk_veto is True
    assert load_profile("objective").high_risk_veto is False


def test_unknown_profile_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_profile("does-not-exist")


def test_invalid_thresholds_rejected() -> None:
    with pytest.raises(ValueError):
        Policy.model_validate(
            {
                "profile": {"name": "bad", "mode": "normal"},
                "weights": {"speed": 0.5, "lang": 0.0, "stability": 0.5},
                "risk_tier": {"low": 0.5, "high": 0.2},
            }
        )


def test_negative_weight_rejected() -> None:
    with pytest.raises(ValueError):
        Policy.model_validate(
            {
                "profile": {"name": "bad", "mode": "normal"},
                "weights": {"speed": -0.1, "lang": 0.0, "stability": 0.5},
            }
        )


def test_13_3_q6_catchup_penalty_pinned() -> None:
    """Q6：攒集连发按阈值惩罚速度分（默认惩罚）。"""
    for name in ("personal-fast", "personal-normal", "objective"):
        policy = load_profile(name)
        assert policy.catchup_penalty_weeks == 2
        assert policy.catchup_speed_penalty < 1.0


def test_13_3_q7_fast_tie_prefers_stability_pinned() -> None:
    """Q7：fast 模式速度持平时优先历史更稳定的组。"""
    policy = load_profile("personal-fast")
    assert policy.fast_tie_prefers_stability is True
    assert policy.weights.stability > 0.0


def test_13_3_q8_unknown_tier_policy_pinned() -> None:
    """Q8：U（数据不足）在个人 profile 按中风险保守对待；objective 从宽。"""
    assert load_profile("personal-normal").unknown_tier_policy == "conservative"
    assert load_profile("personal-fast").unknown_tier_policy == "conservative"
    assert load_profile("objective").unknown_tier_policy == "lenient"


def test_13_3_q9_tolerance_window_pinned() -> None:
    """Q9：容忍窗内自动选 tier A；超出则推给用户确认（个人 0.15，objective 不触发）。"""
    assert load_profile("personal-normal").speed_gain_threshold == pytest.approx(0.15)
    assert load_profile("objective").speed_gain_threshold >= 1.0
