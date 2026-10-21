"""QBClient 的离线单元测试：用假 qbittorrentapi.Client 覆盖各 API 与错误翻译。"""

from __future__ import annotations

from typing import Any

import pytest
import qbittorrentapi

import support
from mikan_qb.config import EnvSettings
from mikan_qb.qb.client import (
    QBClient,
    QBError,
    _count_feed_items,
    _response_text,
    walk_feeds,
)


class FakeHTTPError(Exception):
    """带 response.status_code 的假 HTTP 异常。"""

    def __init__(self, status_code: int, text: str = "") -> None:
        super().__init__(text)
        self.response = type("_Resp", (), {"status_code": status_code})()


class FakeConnectionError(Exception):
    """类名含 Connection，触发连接失败翻译。"""


class FakeTimeoutError(Exception):
    """类名含 Timeout，触发超时翻译。"""


class FakeRaw:
    """可编程的假 qbittorrentapi.Client。"""

    def __init__(self) -> None:
        self.preferences: dict[str, Any] = {
            "rss_download_repack_proper_episodes": True,
            "web_ui_port": 8080,
        }
        self.items: dict[str, Any] = {}
        self.rules_map: dict[str, dict] = {}
        self.categories: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.login_calls = 0
        self.login_error: Exception | None = None
        self.folder_error: Exception | None = None

    def _record(self, label: str, **kwargs: Any) -> None:
        self.calls.append((label, kwargs))

    def auth_log_in(self) -> None:
        self.login_calls += 1
        if self.login_error is not None:
            raise self.login_error

    def app_version(self) -> str:
        return "5.0.0"

    def app_web_api_version(self) -> str:
        return "2.11.0"

    def app_preferences(self) -> dict:
        return dict(self.preferences)

    def rss_items(self, with_data: bool = False) -> dict:
        return self.items

    def rss_rules(self) -> dict:
        return dict(self.rules_map)

    def rss_add_folder(self, folder_path: str) -> None:
        if self.folder_error is not None:
            raise self.folder_error
        self._record("rss_add_folder", folder_path=folder_path)

    def rss_add_feed(self, url: str, item_path: str, refresh_interval: int) -> None:
        self._record(
            "rss_add_feed", url=url, item_path=item_path, refresh_interval=refresh_interval
        )

    def rss_remove_item(self, item_path: str) -> None:
        self._record("rss_remove_item", item_path=item_path)

    def rss_set_rule(self, rule_name: str, rule_def: dict) -> None:
        self.rules_map[rule_name] = rule_def
        self._record("rss_set_rule", rule_name=rule_name, rule_def=rule_def)

    def rss_remove_rule(self, rule_name: str) -> None:
        self.rules_map.pop(rule_name, None)
        self._record("rss_remove_rule", rule_name=rule_name)

    def rss_matching_articles(self, rule_name: str) -> dict:
        return {rule_name: ["a", "b"]}

    def torrents_categories(self) -> dict:
        return dict(self.categories)

    def torrents_edit_category(self, name: str, save_path: str) -> None:
        self._record("torrents_edit_category", name=name, save_path=save_path)

    def torrents_create_category(self, name: str, save_path: str | None = None) -> None:
        self._record("torrents_create_category", name=name, save_path=save_path)

    def torrents_add(self, **kwargs: Any) -> None:
        self._record("torrents_add", **kwargs)

    def torrents_info(self, tag: str | None = None, **kwargs: Any) -> list:
        return [support.FakeTorrent("t", "h")]

    def torrents_delete(self, delete_files: bool, torrent_hashes: list[str]) -> None:
        self._record("torrents_delete", delete_files=delete_files, torrent_hashes=torrent_hashes)

    def app_set_preferences(self, prefs: dict) -> None:
        self._record("app_set_preferences", prefs=prefs)


@pytest.fixture
def fake_raw(monkeypatch) -> FakeRaw:
    """把 qbittorrentapi.Client 替换为返回同一 FakeRaw 的工厂。"""
    raw = FakeRaw()
    monkeypatch.setattr(qbittorrentapi, "Client", lambda **kwargs: raw)
    return raw


@pytest.fixture
def env() -> EnvSettings:
    """带完整 qB 凭据的环境设置。"""
    return EnvSettings(
        _env_file=None,
        qb_host="http://qb.local:8080",
        qb_username="admin",
        qb_password="secret",
    )


@pytest.fixture
def client(env, fake_raw) -> QBClient:
    """待测客户端。"""
    return QBClient(env)


def test_init_requires_qb_host() -> None:
    with pytest.raises(QBError):
        QBClient(EnvSettings(_env_file=None))


def test_raw_property(client, fake_raw) -> None:
    assert client.raw is fake_raw


def test_connect_success(client, fake_raw) -> None:
    fake_raw.items = {"某番": {"url": "http://x/rss", "refreshInterval": 30}}
    status = client.connect()
    assert status.host == "http://qb.local:8080"
    assert status.version == "5.0.0"
    assert status.webapi_version == "2.11.0"
    assert status.feed_count == 1
    assert status.rule_count == 0
    assert status.rss_settings["rss_download_repack_proper_episodes"] is True


def test_connect_login_failed(client, fake_raw) -> None:
    fake_raw.login_error = qbittorrentapi.LoginFailed()
    with pytest.raises(QBError) as exc:
        client.connect()
    assert "登录失败" in str(exc.value)


def test_connect_retries_then_raises(client, fake_raw, monkeypatch) -> None:
    monkeypatch.setattr("mikan_qb.qb.client.time.sleep", lambda *_: None)
    fake_raw.login_error = FakeConnectionError("refused")
    with pytest.raises(QBError) as exc:
        client.connect()
    assert fake_raw.login_calls == 3
    assert "无法连接" in str(exc.value)


def test_connect_wraps_status_error(client, fake_raw) -> None:
    def boom() -> str:
        raise FakeConnectionError("down")

    fake_raw.app_version = boom
    with pytest.raises(QBError):
        client.connect()


def test_rss_settings_filters_prefix(client) -> None:
    settings = client.rss_settings()
    assert "rss_download_repack_proper_episodes" in settings
    assert "web_ui_port" not in settings


def test_feeds_flattens_tree(client, fake_raw) -> None:
    fake_raw.items = {"分组": {"children": {"某番": {"url": "http://x/1", "refreshInterval": 30}}}}
    rows = client.feeds()
    assert [r["path"] for r in rows] == ["分组", "分组/某番"]
    assert rows[1]["url"] == "http://x/1"
    assert rows[1]["type"] == "feed"


def test_add_folder_calls_api(client, fake_raw) -> None:
    client.add_folder("2026夏")
    assert ("rss_add_folder", {"folder_path": "2026夏"}) in fake_raw.calls


def test_add_folder_idempotent_on_409(client, fake_raw) -> None:
    fake_raw.folder_error = FakeHTTPError(409, "conflict")
    client.add_folder("2026夏")


def test_add_folder_reraises_other_errors(client, fake_raw) -> None:
    fake_raw.folder_error = FakeHTTPError(500, "boom")
    with pytest.raises(QBError):
        client.add_folder("2026夏")


def test_ensure_feed_parent_creates_parent(client, fake_raw) -> None:
    client.ensure_feed_parent("2026夏/某番@字幕组")
    assert ("rss_add_folder", {"folder_path": "2026夏"}) in fake_raw.calls


def test_ensure_feed_parent_root_is_noop(client, fake_raw) -> None:
    client.ensure_feed_parent("某番@字幕组")
    assert not any(name == "rss_add_folder" for name, _ in fake_raw.calls)


def test_add_feed(client, fake_raw) -> None:
    client.add_feed("http://x/feed", "2026夏/某番", 30)
    assert (
        "rss_add_feed",
        {"url": "http://x/feed", "item_path": "2026夏/某番", "refresh_interval": 30},
    ) in fake_raw.calls


def test_remove_item(client, fake_raw) -> None:
    client.remove_item("2026夏/某番")
    assert ("rss_remove_item", {"item_path": "2026夏/某番"}) in fake_raw.calls


def test_rules_and_rule(client, fake_raw) -> None:
    fake_raw.rules_map = {"r1": {"mustContain": "x"}}
    assert client.rules() == {"r1": {"mustContain": "x"}}
    assert client.rule("r1") == {"mustContain": "x"}
    assert client.rule("missing") is None


def test_set_rule_defaults_priority(client, fake_raw) -> None:
    client.set_rule("r1", {"mustContain": "x"})
    assert fake_raw.rules_map["r1"]["priority"] == 0


def test_set_rule_keeps_explicit_priority(client, fake_raw) -> None:
    client.set_rule("r1", {"mustContain": "x", "priority": 5})
    assert fake_raw.rules_map["r1"]["priority"] == 5


def test_remove_rule(client, fake_raw) -> None:
    fake_raw.rules_map = {"r1": {}}
    client.remove_rule("r1")
    assert "r1" not in fake_raw.rules_map


def test_matching_articles(client) -> None:
    assert client.matching_articles("r1") == {"r1": ["a", "b"]}


def test_ensure_category_existing_same_path(client, fake_raw) -> None:
    fake_raw.categories = {"番剧": {"savePath": "/downloads/anime"}}
    assert client.ensure_category("番剧", "/downloads/anime") is False
    assert not any(name == "torrents_edit_category" for name, _ in fake_raw.calls)


def test_ensure_category_existing_other_path(client, fake_raw) -> None:
    fake_raw.categories = {"番剧": {"savePath": "/old"}}
    assert client.ensure_category("番剧", "/new") is False
    assert ("torrents_edit_category", {"name": "番剧", "save_path": "/new"}) in fake_raw.calls


def test_ensure_category_creates_when_missing(client, fake_raw) -> None:
    assert client.ensure_category("新分类") is True
    assert ("torrents_create_category", {"name": "新分类", "save_path": None}) in fake_raw.calls


def test_add_torrents_iterates_urls(client, fake_raw) -> None:
    client.add_torrents(["a", "b"], save_path="/d", category="c", tags=["t1", "t2"], paused=True)
    added = [call for call in fake_raw.calls if call[0] == "torrents_add"]
    assert len(added) == 2
    assert added[0][1]["urls"] == "a"
    assert added[0][1]["tags"] == "t1,t2"
    assert added[0][1]["is_paused"] is True


def test_torrents_by_tag(client) -> None:
    rows = client.torrents_by_tag("mikan-3995")
    assert [row.hash for row in rows] == ["h"]


def test_delete_torrents_empty_is_noop(client, fake_raw) -> None:
    client.delete_torrents([], True)
    assert not any(name == "torrents_delete" for name, _ in fake_raw.calls)


def test_delete_torrents_non_empty(client, fake_raw) -> None:
    client.delete_torrents(["h1", "h2"], False)
    assert (
        "torrents_delete",
        {"delete_files": False, "torrent_hashes": ["h1", "h2"]},
    ) in fake_raw.calls


def test_set_preferences(client, fake_raw) -> None:
    client.set_preferences({"rss_download_repack_proper_episodes": False})
    assert any(name == "app_set_preferences" for name, _ in fake_raw.calls)


def test_repack_proper_enabled(client, fake_raw) -> None:
    assert client.repack_proper_enabled() is True
    fake_raw.preferences["rss_download_repack_proper_episodes"] = False
    assert client.repack_proper_enabled() is False


def test_call_translates_exception(client, fake_raw) -> None:
    def boom(**kwargs: Any) -> None:
        raise FakeHTTPError(400, "bad")

    fake_raw.rss_add_feed = boom
    with pytest.raises(QBError) as exc:
        client.add_feed("u", "p", 10)
    assert exc.value.status == 400


def test_translate_login_failed(client) -> None:
    assert "登录失败" in str(client._translate(qbittorrentapi.LoginFailed()))


@pytest.mark.parametrize("code", [401, 403])
def test_translate_auth_errors(client, code: int) -> None:
    error = client._translate(FakeHTTPError(code, "denied"))
    assert error.status == code
    assert "拒绝访问" in str(error)


def test_translate_404(client) -> None:
    error = client._translate(FakeHTTPError(404, "nf"))
    assert error.status == 404
    assert "不存在" in str(error)


def test_translate_409(client) -> None:
    error = client._translate(FakeHTTPError(409, "conflict"))
    assert error.status == 409
    assert "冲突" in str(error)


def test_translate_415(client) -> None:
    error = client._translate(FakeHTTPError(415, "bad"))
    assert error.status == 415


def test_translate_other_status(client) -> None:
    error = client._translate(FakeHTTPError(500, "oops"))
    assert error.status == 500
    assert "500" in str(error)


def test_translate_connection_error(client) -> None:
    assert "无法连接" in str(client._translate(FakeConnectionError("x")))


def test_translate_timeout_error(client) -> None:
    assert "无法连接" in str(client._translate(FakeTimeoutError("x")))


def test_translate_generic_error(client) -> None:
    assert "API 调用失败" in str(client._translate(ValueError("weird")))


def test_http_error_direct(client) -> None:
    error = QBClient._http_error(FakeHTTPError(404, "gone"), 404)
    assert error.status == 404


def test_response_text_returns_message() -> None:
    assert _response_text(ValueError("boom")) == "boom"


def test_response_text_falls_back_to_class_name() -> None:
    assert _response_text(FakeHTTPError(500, "")) == "FakeHTTPError"


def test_count_feed_items_nested() -> None:
    tree = {
        "a": {"url": "u"},
        "b": {"children": {"c": {"url": "u2"}, "d": {}}},
        "children": {},
    }
    assert _count_feed_items(tree) == 2
    assert _count_feed_items("nope") == 0


def test_walk_feeds_flattens() -> None:
    tree = {"dir": {"children": {"feed": {"url": "u", "refreshInterval": 15}}}}
    rows = walk_feeds(tree)
    assert [r["path"] for r in rows] == ["dir", "dir/feed"]
    assert rows[0]["type"] == "dir"
    assert rows[1]["type"] == "feed"
    assert rows[1]["refresh_interval"] == "15"
    assert walk_feeds(None) == []
