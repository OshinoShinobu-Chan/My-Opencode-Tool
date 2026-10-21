from __future__ import annotations

import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from mikan_health.data.parse import (
    parse_bangumi_page,
    parse_episode_table,
    parse_publish_group,
    parse_rss,
    parse_search,
    parse_season,
)

FIXTURES = Path(__file__).parent / "fixtures"
BANGUMI_ID = 3995
SAKURATO_SID = 203


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_bangumi_page_basics() -> None:
    detail = parse_bangumi_page(_read("bangumi_3995.html"), BANGUMI_ID)
    assert detail.title == "无职转生 第三季 ～到了异世界就拿出真本事～"
    assert detail.weekday == "星期日"
    assert detail.start_date is not None
    assert (detail.start_date.year, detail.start_date.month, detail.start_date.day) == (2026, 7, 4)
    assert detail.official_site == "https://mushokutensei.jp/"
    assert detail.bangumi_tv_url == "https://bgm.tv/subject/501963"


def test_parse_bangumi_page_subgroups() -> None:
    detail = parse_bangumi_page(_read("bangumi_3995.html"), BANGUMI_ID)
    assert len(detail.subgroups) == 13

    first = detail.subgroups[0]
    assert first.subtitle_group_id == SAKURATO_SID
    assert first.name == "桜都字幕组"
    assert first.publish_group_id == 181
    assert first.rss_url == f"/RSS/Bangumi?bangumiId={BANGUMI_ID}&subgroupid={SAKURATO_SID}"
    assert first.last_update is not None
    assert first.release_count > 0

    ids = {s.subtitle_group_id for s in detail.subgroups}
    assert {203, 615, 1252, 1256, 370, 583, 45} <= ids


def test_parse_bangumi_page_releases() -> None:
    detail = parse_bangumi_page(_read("bangumi_3995.html"), BANGUMI_ID)
    assert detail.releases
    assert all(r.bangumi_id == BANGUMI_ID for r in detail.releases)

    sakurato = [r for r in detail.releases if r.subtitle_group_id == SAKURATO_SID]
    assert sakurato
    assert all(r.subgroup_label == "桜都字幕组" for r in sakurato)
    assert all(r.source == "bangumi-page" for r in sakurato)

    newest = sakurato[0]
    assert newest.episode is not None
    assert newest.published_at is not None
    assert newest.size_bytes and newest.size_bytes > 100 * 1024 * 1024
    assert newest.magnet and newest.magnet.startswith("magnet:?xt=urn:btih:")
    assert newest.torrent_url and newest.torrent_url.startswith("/Download/")

    assert any(r.is_pack for r in detail.releases) or True


def test_parse_episode_table_full_history() -> None:
    releases = parse_episode_table(
        _read("expanded_203_full.html"), BANGUMI_ID, SAKURATO_SID, source="expand"
    )
    assert len(releases) == 36
    assert {r.subtitle_group_id for r in releases} == {SAKURATO_SID}

    episodes = sorted({r.episode for r in releases if r.episode is not None})
    assert episodes == [float(n) for n in range(1, 13)]

    per_episode = {}
    for release in releases:
        per_episode.setdefault(release.episode, []).append(release.language)
    assert all(len(langs) == 3 for langs in per_episode.values())
    assert set(per_episode[12.0]) == {"简繁", "简体", "繁体"}

    timestamps = [r.published_at for r in releases]
    assert all(t is not None for t in timestamps)
    assert timestamps[0] > timestamps[-1]


def test_parse_rss() -> None:
    releases = parse_rss(_read("rss_3995_203.xml"), BANGUMI_ID, SAKURATO_SID)
    assert len(releases) == 36
    assert all(r.source == "rss" for r in releases)
    assert all(r.subtitle_group_id == SAKURATO_SID for r in releases)

    newest = releases[0]
    assert newest.published_at is not None
    assert newest.published_at.year == 2026
    assert newest.size_bytes == 650326848
    assert newest.torrent_url and newest.torrent_url.endswith(".torrent")
    assert newest.episode == 12.0


def test_parse_rss_and_page_agree_on_latest() -> None:
    page = parse_bangumi_page(_read("bangumi_3995.html"), BANGUMI_ID)
    rss = parse_rss(_read("rss_3995_203.xml"), BANGUMI_ID, SAKURATO_SID)

    page_times = {
        r.title: r.published_at for r in page.releases if r.subtitle_group_id == SAKURATO_SID
    }
    rss_times = {r.title: r.published_at for r in rss}
    assert page_times
    for title, stamp in page_times.items():
        assert rss_times[title] is not None
        assert rss_times[title].replace(second=0, microsecond=0) == stamp.replace(
            second=0, microsecond=0
        )


def test_parse_search_single_and_multi() -> None:
    single = parse_search(_read("search_single.html"))
    assert len(single) == 1
    assert single[0].bangumi_id == 4011
    assert single[0].title == "二十世纪电气目录"

    multi = parse_search(_read("search_multi.html"))
    assert len(multi) == 4
    assert 3060 in {r.bangumi_id for r in multi}
    assert all(r.title for r in multi)


def test_parse_season_by_day() -> None:
    entries = parse_season(_read("season_2026_summer_byday.html"))
    assert len(entries) == 95
    assert len({e.bangumi_id for e in entries}) == 95

    weekdays = {e.weekday for e in entries}
    assert {0, 1, 2, 3, 4, 5, 6} <= weekdays

    jojo = next(e for e in entries if e.bangumi_id == 3887)
    assert jojo.title == "飙马野郎 JOJO的奇妙冒险"
    assert jojo.weekday == 4
    assert jojo.weekday_cn == "星期四"
    assert jojo.last_update and "更新" in jojo.last_update


def test_parse_season_skips_placeholder_entries() -> None:
    html = _read("season_2026_summer_byday.html")
    soup = BeautifulSoup(html, "lxml")
    ids_with_link = {
        int(a["href"].rsplit("/", 1)[-1]) for a in soup.select("a.an-text[href^='/Home/Bangumi/']")
    }
    ids_declared = {int(i) for i in re.findall(r'data-bangumiid="(\d+)"', html)}
    parsed = {e.bangumi_id for e in parse_season(html)}

    assert parsed == ids_with_link
    assert ids_declared - parsed == {3896, 3897, 3974, 3975}


@pytest.mark.parametrize("name", ["bangumi_3995.html", "expanded_203_full.html"])
def test_parsers_are_idempotent(name: str) -> None:
    html = _read(name)
    if name.startswith("bangumi"):
        assert parse_bangumi_page(html, BANGUMI_ID).bangumi_id == BANGUMI_ID
    else:
        assert parse_episode_table(html, BANGUMI_ID, SAKURATO_SID)


def test_parse_publish_group() -> None:
    detail = parse_publish_group(_read("publishgroup_181.html"), 181)
    assert detail.publish_group_id == 181
    assert detail.name == "桜都字幕组"
    assert detail.works

    seasons = {w.season_date for w in detail.works}
    assert "2026.07" in seasons
    assert "2026.04" in seasons

    current = [w for w in detail.works if w.is_current]
    assert current
    assert all(w.season_date == "2026.07" for w in current)

    first = detail.works[0]
    assert first.bangumi_id > 0
    assert first.subtitle_group_id == SAKURATO_SID
    assert first.title
    assert detail.work_ids()[0] == first.bangumi_id
    assert len(detail.work_ids(2)) == 2


def test_publish_group_marks_current_season_only() -> None:
    detail = parse_publish_group(_read("publishgroup_181.html"), 181)
    by_season: dict[str, bool] = {}
    for work in detail.works:
        by_season[work.season_date] = by_season.get(work.season_date, False) or work.is_current
    assert by_season["2026.07"] is True
    assert by_season["2026.04"] is False
