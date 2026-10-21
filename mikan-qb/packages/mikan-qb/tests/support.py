"""mikan-qb 测试共享数据与假实现：假 Mikan 数据源 / qB 客户端 / Bangumi 客户端。"""

from __future__ import annotations

from datetime import date, datetime
from functools import cache
from pathlib import Path

from mikan_health.data.bangumi import BangumiEpisode, BangumiEpisodes, BangumiSubject
from mikan_health.data.models import (
    BangumiDetail,
    PublishGroupDetail,
    Release,
    SearchResult,
    SeasonEntry,
    Subgroup,
    WorkRef,
)
from mikan_health.data.parse import parse_bangumi_page, parse_episode_table
from mikan_qb.qb.client import QBStatus

FIXTURES = Path(__file__).resolve().parents[2] / "mikan-health" / "tests" / "fixtures"
BANGUMI_ID = 3995
SAKURATO_SID = 203


@cache
def _parsed_detail() -> BangumiDetail:
    """解析并缓存番剧页夹具；只读使用，避免每个测试重复解析。"""
    html = (FIXTURES / "bangumi_3995.html").read_text(encoding="utf-8")
    return parse_bangumi_page(html, BANGUMI_ID)


def load_detail(bangumi_tv_url: str | None = None) -> BangumiDetail:
    """读取测试夹具的番剧详情；可覆盖 bgm.tv 链接。"""
    detail = _parsed_detail()
    if bangumi_tv_url is not None:
        return detail.model_copy(update={"bangumi_tv_url": bangumi_tv_url})
    return detail


@cache
def _parsed_releases() -> tuple[Release, ...]:
    """解析并缓存某字幕组的展开发布列表；只读使用。"""
    html = (FIXTURES / "expanded_203_full.html").read_text(encoding="utf-8")
    return tuple(parse_episode_table(html, BANGUMI_ID, SAKURATO_SID, source="expand"))


def load_releases() -> list[Release]:
    """读取测试夹具中某字幕组的展开发布列表（每次返回新的列表）。"""
    return list(_parsed_releases())


def make_release(episode: float, subgroup_id: int = SAKURATO_SID, **overrides) -> Release:
    """构造一条单集发布，默认字段可直接用于解析与模拟。"""
    base = {
        "bangumi_id": BANGUMI_ID,
        "subtitle_group_id": subgroup_id,
        "title": f"[桜都字幕组] 测试番 [ {int(episode):02d} ][1080p][简繁内封]",
        "episode": episode,
        "episode_raw": f"{int(episode):02d}",
        "language": "简繁",
        "language_source": "explicit",
        "sub_type": "内封",
        "published_at": datetime(2026, 1, 1, 12, 0),
        "torrent_url": f"https://example.invalid/{int(episode)}.torrent",
        "magnet": f"magnet:?xt=urn:btih:{int(episode):040d}",
        "size_bytes": 1024 * 1024,
    }
    base.update(overrides)
    return Release(**base)


def make_season_entries() -> list[SeasonEntry]:
    """构造季度列表的两个条目。"""
    return [
        SeasonEntry(
            bangumi_id=BANGUMI_ID,
            title="无职转生 第三季",
            weekday=6,
            weekday_cn="周六",
            last_update="2026-01-01 12:00",
        ),
        SeasonEntry(bangumi_id=2, title="另一部番", weekday=0, weekday_cn="周日", last_update=None),
    ]


def make_search_results() -> list[SearchResult]:
    """构造两条搜索结果。"""
    return [
        SearchResult(bangumi_id=BANGUMI_ID, title="无职转生", cover_url="https://x/cover.jpg"),
        SearchResult(bangumi_id=2, title="无职转生 OVA", cover_url=None),
    ]


def make_subject() -> BangumiSubject:
    """构造一条 Bangumi 条目。"""
    return BangumiSubject(
        id=12345,
        name="Mushoku Tensei",
        name_cn="无职转生",
        date=date(2026, 1, 1),
        eps=12,
        total_episodes=12,
        platform="TV",
        infobox=[{"key": "放送星期", "value": "星期六"}],
    )


def make_episodes() -> BangumiEpisodes:
    """构造含 3 集本篇的 Bangumi 剧集列表。"""
    items = [
        BangumiEpisode(id=i, type=0, sort=i, ep=float(i), airdate=date(2026, 1, i), name=f"EP{i}")
        for i in (1, 2, 3)
    ]
    return BangumiEpisodes(subject_id=12345, episodes=items, total=3)


class FakeIndex:
    """假的字幕组索引，返回预设搜索结果。"""

    def __init__(self, matches=None, lookup_value=None) -> None:
        self._matches = matches if matches is not None else []
        self._lookup = lookup_value

    def search(self, keyword: str) -> list[tuple[int, str, int | None]]:
        return self._matches

    def lookup(self, subgroup_id: int):
        return self._lookup

    def upsert(self, subgroups) -> None:
        return None


class FakeSource:
    """假的 MikanSource：所有抓取方法返回预设对象，不触网。"""

    def __init__(
        self,
        detail: BangumiDetail | None = None,
        releases: list[Release] | None = None,
        seasons: list[SeasonEntry] | None = None,
        search_results: list[SearchResult] | None = None,
        publish_group: PublishGroupDetail | None = None,
        index: FakeIndex | None = None,
    ) -> None:
        self._detail = detail if detail is not None else load_detail()
        self._releases = releases if releases is not None else load_releases()
        self._seasons = seasons if seasons is not None else make_season_entries()
        self._search = search_results if search_results is not None else make_search_results()
        self._publish_group = publish_group
        self.index = index if index is not None else FakeIndex()

    def bangumi(self, bangumi_id: int, force: bool = False) -> BangumiDetail:
        return self._detail

    def subgroup_releases(
        self, bangumi_id: int, subgroup_id: int, take: int = 40, force: bool = False
    ) -> list[Release]:
        return list(self._releases)

    def season(self, year: int, season: str, force: bool = False) -> list[SeasonEntry]:
        return list(self._seasons)

    def search(self, keyword: str, force: bool = False) -> list[SearchResult]:
        return list(self._search)

    def publish_group(self, publish_group_id: int, force: bool = False) -> PublishGroupDetail:
        if self._publish_group is not None:
            return self._publish_group
        return PublishGroupDetail(publish_group_id=publish_group_id, name="测试组", works=[])


class FakeBangumiClient:
    """假的 BangumiClient，返回预设条目与剧集。"""

    def __init__(self, subject: BangumiSubject | None = None, episodes=None) -> None:
        self._subject = subject if subject is not None else make_subject()
        self._episodes = episodes if episodes is not None else make_episodes()

    def subject(self, subject_id: int, force: bool = False) -> BangumiSubject:
        return self._subject

    def episodes(self, subject_id: int, force: bool = False) -> BangumiEpisodes:
        return self._episodes


class FakeRawQB:
    """模拟 qbittorrentapi.Client 的 raw 接口。"""

    def __init__(self, items: dict | None = None) -> None:
        self._items = items if items is not None else {}

    def rss_items(self, with_data: bool = False) -> dict:
        return self._items


class FakeTorrent:
    """模拟种子记录：名称、哈希、完成比例与标签。"""

    def __init__(self, name: str = "t", hash_: str = "h", progress: float = 1.0, tags=None) -> None:
        self.name = name
        self.hash = hash_
        self.progress = progress
        self.tags = list(tags or [])


class FakeQBClient:
    """假的 QBClient：记录写入动作，读取返回预设值，不触网。"""

    def __init__(
        self,
        feeds: list[dict] | None = None,
        rules: dict | None = None,
        repack: bool = False,
        torrents: list[FakeTorrent] | None = None,
        matched: dict | None = None,
        raw_items: dict | None = None,
    ) -> None:
        self._feeds = feeds if feeds is not None else []
        self._rules = rules if rules is not None else {}
        self._repack = repack
        self._torrents = torrents if torrents is not None else []
        self._matched = matched if matched is not None else {}
        self.raw = FakeRawQB(raw_items)
        self.calls: list[tuple] = []

    def connect(self) -> QBStatus:
        return QBStatus(
            host="http://qb.local:8080",
            version="5.0.0",
            webapi_version="2.11.0",
            feed_count=len(self._feeds),
            rule_count=len(self._rules),
            rss_settings={"rss_download_repack_proper_episodes": self._repack},
        )

    def feeds(self, with_data: bool = False) -> list[dict]:
        return list(self._feeds)

    def rules(self) -> dict:
        return dict(self._rules)

    def rule(self, name: str):
        return self._rules.get(name)

    def set_rule(self, name: str, rule_def: dict) -> None:
        self._rules[name] = rule_def
        self.calls.append(("set_rule", name))

    def remove_rule(self, name: str) -> None:
        self._rules.pop(name, None)
        self.calls.append(("remove_rule", name))

    def matching_articles(self, rule_name: str) -> dict:
        return self._matched

    def repack_proper_enabled(self) -> bool:
        return self._repack

    def set_preferences(self, values: dict) -> None:
        self._repack = bool(values.get("rss_download_repack_proper_episodes", self._repack))
        self.calls.append(("set_preferences", values))

    def ensure_category(self, name: str, save_path: str = "") -> bool:
        self.calls.append(("ensure_category", name))
        return True

    def ensure_feed_parent(self, feed_path: str) -> None:
        self.calls.append(("ensure_feed_parent", feed_path))

    def add_feed(self, url: str, item_path: str, refresh_interval: int) -> None:
        self.calls.append(("add_feed", item_path))

    def remove_item(self, path: str) -> None:
        self.calls.append(("remove_item", path))

    def torrents_by_tag(self, tag: str) -> list[FakeTorrent]:
        if self._torrents and any(t.tags for t in self._torrents):
            return [t for t in self._torrents if tag in t.tags]
        return list(self._torrents)

    def delete_torrents(self, hashes: list[str], delete_files: bool) -> None:
        self.calls.append(("delete_torrents", tuple(hashes), delete_files))

    def add_torrents(self, urls, save_path="", category="", tags=None, paused=False) -> None:
        self.calls.append(("add_torrents", tuple(urls), paused))


def make_work_refs() -> list[WorkRef]:
    """构造字幕组年表中的两条作品引用。"""
    return [
        WorkRef(
            bangumi_id=100,
            subtitle_group_id=SAKURATO_SID,
            title="旧番 A",
            season_date="2025.10",
            season_label="2025秋",
            is_current=False,
        ),
        WorkRef(
            bangumi_id=101,
            subtitle_group_id=SAKURATO_SID,
            title="旧番 B",
            season_date="2025.7",
            season_label="2025夏",
            is_current=False,
        ),
    ]


def make_subgroup(publish_group_id: int | None = 999) -> Subgroup:
    """构造一个字幕组条目。"""
    return Subgroup(
        subtitle_group_id=SAKURATO_SID,
        name="桜都字幕组",
        publish_group_id=publish_group_id,
        rss_url="/RSS/Bangumi?bangumiId=3995&subgroupid=203",
        last_update=datetime(2026, 1, 1, 12, 0),
        release_count=12,
    )
