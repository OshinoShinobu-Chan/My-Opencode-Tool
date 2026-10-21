"""计划构建的持久化、差异对比与内部 helper 的补充单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

import support
from mikan_health.core.policy import load_profile
from mikan_health.core.variants import solve
from mikan_qb.config import AppConfig, EnvSettings
from mikan_qb.rules.plan import (
    PlanError,
    _coverage_check,
    _feed_url,
    _load_policy,
    _normalized,
    _plan_variant,
    _require_subgroup,
    _rule_lang,
    build_plan,
    diff_plan,
    find_plan,
    load_plan,
    plan_file,
    save_plan,
)


@pytest.fixture
def plan(detail, releases):
    """正常模式下为桜都字幕组构建的真实计划。"""
    return build_plan(
        AppConfig(), EnvSettings(_env_file=None), detail, releases, support.SAKURATO_SID, "normal"
    )


@pytest.fixture
def isolated_plans_dir(tmp_path: Path, monkeypatch) -> Path:
    """把计划的默认目录隔离到 tmp。"""
    monkeypatch.setattr("mikan_qb.rules.plan.plans_dir", lambda: tmp_path)
    return tmp_path


def test_plan_file_under_plans_dir(isolated_plans_dir: Path) -> None:
    assert plan_file(3995, 203) == isolated_plans_dir / "3995-203.json"


def test_save_and_load_plan_roundtrip(tmp_path: Path, plan) -> None:
    target = tmp_path / "custom.json"
    written = save_plan(plan, target)
    assert written == target

    loaded = load_plan(target)
    assert loaded.bangumi_id == plan.bangumi_id
    assert loaded.subgroup_id == plan.subgroup_id
    assert loaded.rule_name == plan.rule_name
    assert loaded.rule_def == plan.rule_def
    assert [m.episode for m in loaded.matches] == [m.episode for m in plan.matches]


def test_save_plan_uses_default_path(isolated_plans_dir: Path, plan) -> None:
    written = save_plan(plan)
    assert written == isolated_plans_dir / f"{plan.bangumi_id}-{plan.subgroup_id}.json"
    assert written.is_file()


def test_find_plan_by_subgroup(isolated_plans_dir: Path, plan) -> None:
    save_plan(plan, plan_file(plan.bangumi_id, plan.subgroup_id))
    found = find_plan(plan.bangumi_id, plan.subgroup_id)
    assert found is not None
    assert found.subgroup_id == plan.subgroup_id
    assert find_plan(plan.bangumi_id, 999999) is None


def test_find_plan_without_subgroup_picks_latest(isolated_plans_dir: Path, plan) -> None:
    save_plan(plan, plan_file(plan.bangumi_id, plan.subgroup_id))
    save_plan(plan.model_copy(update={"subgroup_id": 999}), plan_file(plan.bangumi_id, 999))

    found = find_plan(plan.bangumi_id)
    assert found is not None
    assert found.subgroup_id == 999
    assert find_plan(123456) is None


def test_diff_plan_creates_when_qb_empty(plan) -> None:
    result = diff_plan(plan, support.FakeQBClient())
    actions = {(d["item"], d["action"]) for d in result["differences"]}
    assert ("feed", "create") in actions
    assert ("rule", "create") in actions
    assert result["has_changes"] is True


def test_diff_plan_unchanged(plan) -> None:
    feeds = [{"path": plan.feed_path, "url": plan.feed_url}]
    rules = {plan.rule_name: plan.rule_def}
    result = diff_plan(plan, support.FakeQBClient(feeds=feeds, rules=rules))
    assert {d["action"] for d in result["differences"]} == {"unchanged"}
    assert result["has_changes"] is False


def test_diff_plan_updates_url_and_rule(plan) -> None:
    feeds = [{"path": plan.feed_path, "url": "https://old.example/rss"}]
    stale = dict(plan.rule_def)
    stale["mustContain"] = "旧标记"
    rules = {plan.rule_name: stale}
    result = diff_plan(plan, support.FakeQBClient(feeds=feeds, rules=rules))
    by_item = {d["item"]: d["action"] for d in result["differences"]}
    assert by_item["feed"] == "update_url"
    assert by_item["rule"] == "update"
    assert result["has_changes"] is True


def test_diff_plan_reuses_existing_feed(plan) -> None:
    feeds = [{"path": "别的路径", "url": plan.feed_url}]
    result = diff_plan(plan, support.FakeQBClient(feeds=feeds))
    feed_diff = next(d for d in result["differences"] if d["item"] == "feed")
    assert feed_diff["action"] == "reuse_existing"


def test_diff_plan_detects_tag_change(plan) -> None:
    feeds = [{"path": plan.feed_path, "url": plan.feed_url}]
    stale = dict(plan.rule_def)
    stale["torrentParams"] = {"tags": ["别的标签"]}
    rules = {plan.rule_name: stale}
    result = diff_plan(plan, support.FakeQBClient(feeds=feeds, rules=rules))
    rule_diff = next(d for d in result["differences"] if d["item"] == "rule")
    assert rule_diff["action"] == "update"
    assert "tags" in rule_diff["detail"]


def test_coverage_check_explicit_reference(detail) -> None:
    assert _coverage_check(detail, list(range(12)), 12)["ok"] is True
    bad = _coverage_check(detail, list(range(2)), 12)
    assert bad["ok"] is False
    assert bad["covered"] == 2
    assert bad["reference"] == 12
    assert "只覆盖" in bad["message"]


def test_coverage_check_infers_peer_reference(detail) -> None:
    result = _coverage_check(detail, list(range(12)), None)
    assert result["reference"] is not None
    assert isinstance(result["ok"], bool)


def test_coverage_check_no_reference_ok(detail) -> None:
    empty = detail.model_copy(update={"releases": [], "subgroups": []})
    result = _coverage_check(empty, [], None)
    assert result["ok"] is True
    assert result["reference"] is None


def test_rule_lang_fast_and_normal(releases) -> None:
    solution = solve(releases, load_profile("personal-normal"), AppConfig().taxonomy)
    assert solution is not None
    assert _rule_lang("fast", solution) == "追更"
    assert _rule_lang("normal", solution) == "简繁"


def test_load_policy_modes() -> None:
    assert _load_policy(AppConfig(), "normal").mode == "normal"
    assert _load_policy(AppConfig(), "fast").mode == "fast"


def test_load_policy_falls_back_when_custom_dir_missing(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {"analysis": {"profile": "自定义-normal", "profiles_dir": str(tmp_path / "nope")}}
    )
    assert _load_policy(config, "normal").mode == "normal"
    assert _load_policy(config, "fast").mode == "fast"


def test_require_subgroup_returns_match(detail) -> None:
    assert _require_subgroup(detail, support.SAKURATO_SID).subtitle_group_id == (
        support.SAKURATO_SID
    )


def test_require_subgroup_raises_for_unknown(detail) -> None:
    with pytest.raises(PlanError):
        _require_subgroup(detail, 999999)


def test_feed_url(detail) -> None:
    url = _feed_url(
        AppConfig(), EnvSettings(_env_file=None), detail.bangumi_id, support.SAKURATO_SID
    )
    assert url == "https://mikanani.kas.pub/RSS/Bangumi?bangumiId=3995&subgroupid=203"


def test_feed_url_respects_env_override(detail) -> None:
    env = EnvSettings(_env_file=None, mikan_base="https://env.example/")
    url = _feed_url(AppConfig(), env, detail.bangumi_id, support.SAKURATO_SID)
    assert url.startswith("https://env.example/RSS/Bangumi")


def test_normalized_converts_lists() -> None:
    assert _normalized([1, 2]) == ["1", "2"]
    assert _normalized("x") == "x"
    assert _normalized(None) is None


def test_plan_variant_from_solution(releases) -> None:
    solution = solve(releases, load_profile("personal-normal"), AppConfig().taxonomy)
    assert solution is not None
    variant = _plan_variant(solution.variants[0])
    assert variant.key == solution.variants[0].key
    assert variant.language == solution.variants[0].language
    assert variant.episode_count == len(solution.variants[0].episodes)
