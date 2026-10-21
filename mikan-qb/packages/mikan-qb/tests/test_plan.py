from __future__ import annotations

from pathlib import Path

import pytest

from mikan_health.data.parse import parse_bangumi_page, parse_episode_table
from mikan_qb.config import AppConfig, EnvSettings
from mikan_qb.rules.plan import (
    PlanError,
    build_plan,
    sanitize_dir,
    season_of,
)

FIXTURES = Path(__file__).resolve().parents[2] / "mikan-health" / "tests" / "fixtures"
BANGUMI_ID = 3995
SAKURATO_SID = 203


def _detail():
    html = (FIXTURES / "bangumi_3995.html").read_text(encoding="utf-8")
    return parse_bangumi_page(html, BANGUMI_ID)


def _sakurato_releases():
    html = (FIXTURES / "expanded_203_full.html").read_text(encoding="utf-8")
    return parse_episode_table(html, BANGUMI_ID, SAKURATO_SID, source="expand")


def _plan(mode: str = "normal"):
    return build_plan(
        AppConfig(),
        EnvSettings(_env_file=None),
        _detail(),
        _sakurato_releases(),
        SAKURATO_SID,
        mode,
    )


def test_season_of_from_start_date() -> None:
    assert season_of(_detail()) == "2026夏"


def test_sanitize_dir_strips_illegal_chars() -> None:
    assert sanitize_dir("某番/第二季: 篇") == "某番第二季 篇"
    assert sanitize_dir("  ") == "unknown"


def test_plan_is_valid_for_known_group() -> None:
    plan = _plan()
    assert plan.valid is True
    assert plan.gaps == []
    assert len(plan.matches) == 12
    assert [m.episode for m in plan.matches] == [float(n) for n in range(1, 13)]


def test_plan_chooses_softsub_both_languages() -> None:
    plan = _plan("normal")
    assert [v.token for v in plan.variants] == ["简繁内封"]
    assert plan.language == "简繁"
    assert plan.rule_lang == "简繁"


def test_plan_rule_def_shape() -> None:
    plan = _plan()
    rule = plan.rule_def
    assert rule["enabled"] is True
    assert rule["useRegex"] is True
    assert rule["smartFilter"] is False
    assert rule["mustContain"] == "简繁内封"
    assert "简体内嵌" in rule["mustNotContain"]
    assert "繁体内嵌" in rule["mustNotContain"]
    assert rule["affectedFeeds"] == [plan.feed_url]
    assert rule["assignedCategory"] == "番剧"
    assert rule["savePath"] == "/downloads/anime/无职转生 第三季 ～到了异世界就拿出真本事～"


def test_plan_paths_and_tags() -> None:
    plan = _plan()
    anime = "无职转生 第三季 ～到了异世界就拿出真本事～"
    assert plan.season == "2026夏"
    assert plan.feed_folder == "2026夏"
    assert plan.feed_path == f"2026夏/{anime}@桜都字幕组"
    assert plan.rule_name == f"简繁/{anime}@桜都字幕组"
    assert plan.rule_def["torrentParams"]["tags"] == ["mikan-3995", "简繁"]
    assert plan.rule_def["torrentParams"]["save_path"] == plan.rule_def["savePath"]


def test_feed_path_keeps_subgroups_apart() -> None:
    """换组替换时新旧两个 feed 与规则必须能共存，路径/规则名里必须带字幕组。"""
    detail = _detail()
    releases = _sakurato_releases()
    config = AppConfig()
    env = EnvSettings(_env_file=None)

    first = build_plan(config, env, detail, releases, SAKURATO_SID, "normal")
    other = build_plan(config, env, detail, releases, 615, "normal", expected_episodes=None)
    assert first.feed_path != other.feed_path
    assert first.feed_url != other.feed_url
    assert first.rule_name != other.rule_name
    assert "桜都字幕组" in first.feed_path
    assert "桜都字幕组" in first.rule_name


def test_plan_feed_url_points_to_subgroup() -> None:
    plan = _plan()
    assert plan.feed_url.endswith(f"bangumiId={BANGUMI_ID}&subgroupid={SAKURATO_SID}")


def test_fast_mode_uses_chase_label() -> None:
    plan = _plan("fast")
    assert plan.mode == "fast"
    assert plan.rule_lang == "追更"
    assert plan.variants[0].sub_type == "内封"
    assert plan.valid is True


def test_matches_carry_download_links() -> None:
    plan = _plan()
    assert all(m.torrent_url or m.magnet for m in plan.matches)
    assert all(m.size_bytes for m in plan.matches)


def test_unknown_subgroup_raises() -> None:
    with pytest.raises(PlanError):
        build_plan(
            AppConfig(),
            EnvSettings(_env_file=None),
            _detail(),
            _sakurato_releases(),
            999999,
            "normal",
        )


def test_unsolvable_group_raises() -> None:
    releases = []
    for episode in (1, 2, 3):
        releases.append(_sakurato_releases()[0].model_copy(update={"episode": float(episode)}))
    token_a = _sakurato_releases()[0].model_copy(
        update={"episode": 3.0, "title": "[某组] 番名 [03][1080p][简繁内封]"}
    )
    token_b = _sakurato_releases()[1].model_copy(
        update={"episode": 3.0, "title": "[某组] 番名 [03][1080p][繁体内嵌]"}
    )
    only_a = _sakurato_releases()[0].model_copy(
        update={"episode": 1.0, "title": "[某组] 番名 [01][1080p][简繁内封]"}
    )
    only_b = _sakurato_releases()[1].model_copy(
        update={"episode": 2.0, "title": "[某组] 番名 [02][1080p][繁体内嵌]"}
    )
    releases = [only_a, only_b, token_a, token_b]
    with pytest.raises(PlanError):
        build_plan(
            AppConfig(),
            EnvSettings(_env_file=None),
            _detail(),
            releases,
            SAKURATO_SID,
            "normal",
        )
