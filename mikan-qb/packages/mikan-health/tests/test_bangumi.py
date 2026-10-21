from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from mikan_health.core.airtime import (
    EpisodeAnchor,
    Schedule,
    build_anchor,
    build_schedule,
)
from mikan_health.core.episodes import EpisodeEvent
from mikan_health.core.metrics import airdate_delays
from mikan_health.data.bangumi import (
    BangumiClient,
    BangumiEpisode,
    BangumiEpisodes,
    BangumiSubject,
    detect_proxy,
    load_token,
    subject_id_from_url,
)

FIXTURES = Path(__file__).parent / "fixtures"
SUBJECT_ID = 501963


def _subject() -> BangumiSubject:
    return BangumiSubject.model_validate(
        json.loads((FIXTURES / "bgm_subject_501963.json").read_text(encoding="utf-8"))
    )


def _episodes() -> BangumiEpisodes:
    payload = json.loads((FIXTURES / "bgm_episodes_501963.json").read_text(encoding="utf-8"))
    return BangumiEpisodes(
        subject_id=SUBJECT_ID,
        episodes=[BangumiEpisode.model_validate(row) for row in payload["data"]],
        total=payload["total"],
    )


def test_subject_id_from_url() -> None:
    assert subject_id_from_url("https://bgm.tv/subject/501963") == 501963
    assert subject_id_from_url("http://bgm.tv/subject/515759?foo=1") == 515759
    assert subject_id_from_url("https://bangumi.tv/subject/100") == 100
    assert subject_id_from_url("https://bgm.tv/anime/list") is None
    assert subject_id_from_url(None) is None
    assert subject_id_from_url("") is None


def test_detect_proxy_prefers_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("BGM_PROXY", "127.0.0.1:1080")
    assert detect_proxy() == "http://127.0.0.1:1080"
    monkeypatch.setenv("BGM_PROXY", "socks5://127.0.0.1:1080")
    assert detect_proxy() == "socks5://127.0.0.1:1080"


def test_detect_proxy_falls_back_to_https_proxy(monkeypatch) -> None:
    monkeypatch.delenv("BGM_PROXY", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    assert detect_proxy() == "http://127.0.0.1:7890"


def test_load_token_precedence(tmp_path, monkeypatch) -> None:
    token_file = tmp_path / "token.txt"
    token_file.write_text("from-file\n", encoding="utf-8")
    monkeypatch.setenv("BGM_TOKEN", "from-env")
    assert load_token("explicit") == "explicit"
    assert load_token(None) == "from-env"
    monkeypatch.delenv("BGM_TOKEN", raising=False)
    monkeypatch.delenv("BANGUMI_TOKEN", raising=False)
    assert load_token(None, [token_file]) == "from-file"
    assert load_token(None, [tmp_path / "missing.txt"]) is None


def test_subject_fixture_fields() -> None:
    subject = _subject()
    assert subject.id == SUBJECT_ID
    assert subject.name_cn.startswith("无职转生 第三季")
    assert subject.air_date == date(2026, 7, 4)
    assert subject.total_episodes == 14
    assert subject.platform == "TV"
    assert subject.broadcast_weekday == "星期日"
    assert subject.infobox_value("话数") == "14"
    assert subject.infobox_value("不存在的键") is None


def test_episodes_fixture_fields() -> None:
    episodes = _episodes()
    assert episodes.total == 14
    assert len(episodes.episodes) == 14
    mains = episodes.main_episodes()
    assert len(mains) == 14
    assert mains[0].ep == 1
    assert mains[0].airdate == date(2026, 7, 4)
    assert mains[1].airdate == date(2026, 7, 4)
    assert mains[2].airdate == date(2026, 7, 12)
    assert episodes.airdates()[0] == date(2026, 7, 4)


def test_build_schedule() -> None:
    schedule = build_schedule(_subject(), _episodes())
    assert len(schedule) == 14
    assert schedule.total_episodes == 14
    assert schedule.weekday == "星期日"
    assert schedule.date_of_ordinal(1) == date(2026, 7, 4)
    assert schedule.date_of_ordinal(14) == date(2026, 9, 27)
    assert schedule.date_of_ordinal(15) is None
    assert schedule.aired_count(datetime(2026, 9, 18, 12)) == 12
    assert schedule.next_airdate(datetime(2026, 9, 18, 12)) == date(2026, 9, 20)


def test_build_anchor_normalizes_absolute_numbering() -> None:
    schedule = build_schedule(_subject(), _episodes())
    relative = build_anchor(1, [float(n) for n in range(1, 13)], schedule)
    absolute = build_anchor(2, [float(n) for n in range(29, 41)], schedule)

    assert relative.ordinal_of[1.0] == 1
    assert relative.ordinal_of[12.0] == 12
    assert absolute.ordinal_of[29.0] == 1
    assert absolute.ordinal_of[40.0] == 12
    assert relative.ordinals() == absolute.ordinals()


def test_airdate_delays_uses_schedule() -> None:
    schedule = build_schedule(_subject(), _episodes())
    events = {
        1.0: EpisodeEvent(episode=1.0, released_at=datetime(2026, 7, 5, 12, 0), variant_count=1),
        2.0: EpisodeEvent(episode=2.0, released_at=datetime(2026, 7, 4, 23, 30), variant_count=1),
        3.0: EpisodeEvent(episode=3.0, released_at=datetime(2026, 7, 14, 0, 0), variant_count=1),
    }
    delays = airdate_delays(events, schedule)
    assert delays[1.0] == pytest.approx(36.0)
    assert delays[2.0] == pytest.approx(23.5)
    assert delays[3.0] == pytest.approx(48.0)


def test_episodes_pagination() -> None:
    pages = [
        {"data": [{"id": 1, "sort": 1, "ep": 1, "type": 0}], "total": 2},
        {"data": [{"id": 2, "sort": 2, "ep": 2, "type": 0}], "total": 2},
    ]
    calls: list[dict] = []

    class Stub(BangumiClient):
        def _get(self, path, params=None, force=False):  # noqa: ANN001
            calls.append(params or {})
            return pages[len(calls) - 1]

    client = Stub(token="dummy")
    result = client.episodes(7)
    assert [ep.id for ep in result.episodes] == [1, 2]
    assert result.total == 2
    assert calls[0]["offset"] == 0
    assert calls[1]["offset"] == 1


def test_client_reports_missing_token() -> None:
    client = BangumiClient(token=None, token_paths=[])
    assert client.has_token is False
    with_token = BangumiClient(token="abc")
    assert with_token.has_token is True


def test_blank_and_malformed_dates_normalized() -> None:
    """Bangumi 的占位/未补零日期应被归一，不抛 ValidationError。"""
    episode = BangumiEpisode.model_validate(
        {"id": 1, "sort": 1, "ep": 1, "type": 0, "airdate": "2026-4-26"}
    )
    assert episode.airdate == date(2026, 4, 26)
    blank = BangumiEpisode.model_validate({"id": 2, "sort": 2, "type": 0, "airdate": ""})
    assert blank.airdate is None
    assert BangumiSubject.model_validate({"id": 3, "date": "2099"}).air_date is None
    with_time = BangumiSubject.model_validate({"id": 4, "date": "2026-04-26T00:00:00"})
    assert with_time.air_date == date(2026, 4, 26)


def test_schedule_without_airdates_is_empty() -> None:
    subject = BangumiSubject(id=1, name="x")
    schedule: Schedule = build_schedule(subject, BangumiEpisodes(subject_id=1))
    assert len(schedule) == 0
    anchor: EpisodeAnchor = build_anchor(1, [1.0, 2.0], schedule)
    assert anchor.ordinal_of == {1.0: 1, 2.0: 2}
