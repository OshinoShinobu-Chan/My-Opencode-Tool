"""健康度分析模块（collect_history / build_report / 放送日程）的单元测试。"""

from __future__ import annotations

from datetime import datetime

import pytest

import support
from mikan_health.core.policy import load_profile
from mikan_health.data.bangumi import BangumiEpisode, BangumiEpisodes, BangumiError
from mikan_health.data.models import PublishGroupDetail, WorkRef
from mikan_qb.analysis.health import (
    WORKS_DEFAULT,
    build_report,
    build_subgroup_report,
    collect_history,
    load_schedule,
    resolve_works,
)
from mikan_qb.config import AppConfig

NOW = datetime(2026, 1, 1, 12, 0)


class _ErrorBangumiClient(support.FakeBangumiClient):
    """subject 抛 BangumiError，用于覆盖 load_schedule 的异常分支。"""

    def subject(self, subject_id: int, force: bool = False):
        raise BangumiError("boom", url="https://api.bgm.tv/v0/subjects/1")


@pytest.fixture
def policy():
    """内置的 normal 模式策略。"""
    return load_profile("personal-normal")


@pytest.fixture
def publish_group() -> PublishGroupDetail:
    """含一个当季作品（应被回看跳过）的年表。"""
    works = support.make_work_refs()
    works.append(
        WorkRef(
            bangumi_id=102,
            subtitle_group_id=support.SAKURATO_SID,
            title="当季番",
            season_date="2026.7",
            season_label="2026夏",
            is_current=True,
        )
    )
    return PublishGroupDetail(publish_group_id=181, name="桜都字幕组", works=works)


def test_resolve_works_explicit_and_profile(policy) -> None:
    assert resolve_works(3, policy) == 3
    assert resolve_works(None, policy) == policy.history_works
    assert resolve_works(0, policy) == policy.history_works


def test_resolve_works_falls_back_to_default(policy) -> None:
    fallback = policy.model_copy(update={"history_works": 0})
    assert resolve_works(None, fallback) == WORKS_DEFAULT
    assert resolve_works(-1, fallback) == WORKS_DEFAULT


def test_load_schedule_without_client(detail) -> None:
    assert load_schedule(None, detail) is None


def test_load_schedule_without_bgm_url(detail) -> None:
    no_url = detail.model_copy(update={"bangumi_tv_url": None})
    assert load_schedule(support.FakeBangumiClient(), no_url) is None


def test_load_schedule_builds_schedule(detail) -> None:
    schedule = load_schedule(support.FakeBangumiClient(), detail)
    assert schedule is not None
    assert len(schedule) == 3
    assert schedule.name_cn == "无职转生"


def test_load_schedule_without_airdates(detail) -> None:
    episodes = BangumiEpisodes(
        subject_id=12345,
        episodes=[BangumiEpisode(id=1, type=0, sort=1, ep=1.0, airdate=None)],
        total=1,
    )
    client = support.FakeBangumiClient(episodes=episodes)
    assert load_schedule(client, detail) is None


def test_load_schedule_swallows_bangumi_error(detail) -> None:
    assert load_schedule(_ErrorBangumiClient(), detail) is None


def test_collect_history_without_publish_group() -> None:
    result = collect_history(support.FakeSource(), support.SAKURATO_SID, None)
    assert result.records == []
    assert result.seasons == []
    assert result.last_release is None


def test_collect_history_skips_current_work(publish_group) -> None:
    source = support.FakeSource(publish_group=publish_group)
    result = collect_history(source, support.SAKURATO_SID, 181, limit=10)
    assert sorted(r.bangumi_id for r in result.records) == [100, 101]
    assert result.seasons == ["2026.7", "2025.7", "2025.10"]
    assert result.last_release is not None


def test_collect_history_respects_limit(publish_group) -> None:
    source = support.FakeSource(publish_group=publish_group)
    result = collect_history(source, support.SAKURATO_SID, 181, limit=1)
    assert len(result.records) == 1


def test_collect_history_excludes_bangumi(publish_group) -> None:
    source = support.FakeSource(publish_group=publish_group)
    result = collect_history(source, support.SAKURATO_SID, 181, exclude_bangumi_id=100, limit=10)
    assert [r.bangumi_id for r in result.records] == [101]


def test_build_report_deep_zero(detail, policy, publish_group) -> None:
    source = support.FakeSource(publish_group=publish_group)
    ranking, returned = build_report(
        source, detail.bangumi_id, policy, AppConfig().taxonomy, deep=0, now=NOW
    )
    assert returned.bangumi_id == detail.bangumi_id
    assert ranking.mode == "normal"
    assert ranking.candidates
    assert ranking.recommendation is not None


def test_build_report_deep_positive(detail, policy, publish_group) -> None:
    source = support.FakeSource(publish_group=publish_group)
    ranking, _ = build_report(
        source, detail.bangumi_id, policy, AppConfig().taxonomy, deep=5, now=NOW
    )
    assert ranking.recommendation is not None
    assert ranking.recommendation.subgroup_id == support.SAKURATO_SID


def test_build_report_deep_all(detail, policy, publish_group) -> None:
    source = support.FakeSource(publish_group=publish_group)
    ranking, _ = build_report(
        source, detail.bangumi_id, policy, AppConfig().taxonomy, deep=-1, now=NOW
    )
    assert ranking.candidates


def test_build_report_page_only(detail, policy) -> None:
    ranking, returned = build_report(
        support.FakeSource(),
        detail.bangumi_id,
        policy,
        AppConfig().taxonomy,
        deep=0,
        page_only=True,
        now=NOW,
    )
    assert returned.bangumi_id == detail.bangumi_id
    assert ranking.candidates


def test_build_subgroup_report_with_history(policy, publish_group) -> None:
    source = support.FakeSource(publish_group=publish_group)
    report = build_subgroup_report(
        source, support.SAKURATO_SID, policy, publish_group_id=181, now=NOW
    )
    assert report["subgroup_id"] == support.SAKURATO_SID
    assert report["publish_group_id"] == 181
    assert report["works_total"] == 2
    assert report["seasons"] == ["2026.7", "2025.7", "2025.10"]
    assert len(report["records"]) == 2
    assert "risk" in report
    assert "verdicts" in report


def test_build_subgroup_report_without_group(policy) -> None:
    report = build_subgroup_report(
        support.FakeSource(), support.SAKURATO_SID, policy, publish_group_id=None, now=NOW
    )
    assert report["works_total"] == 0
    assert report["seasons"] == []
    assert report["last_release_days"] is None
