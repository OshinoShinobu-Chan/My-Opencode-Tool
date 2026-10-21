"""anime / season 子命令的命令行测试：元数据、发布记录与季度列表。"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import support
from mikan_health.data import MikanError
from mikan_qb.cli import app


class _MikanErrorSource(support.FakeSource):
    """search / bangumi 抛出 MikanError 的假数据源，用于覆盖错误分支。"""

    def search(self, keyword: str, force: bool = False):
        """无论如何都抛 MikanError。"""
        raise MikanError("搜索失败", url="https://mikan.invalid/search?q=x")

    def bangumi(self, bangumi_id: int, force: bool = False):
        """无论如何都抛 MikanError。"""
        raise MikanError("详情抓取失败", url="https://mikan.invalid/Home/Bangumi/3995")


def _patch_anime(
    monkeypatch,
    source: support.FakeSource | None = None,
    detail: object | None = None,
    bangumi: support.FakeBangumiClient | None = None,
    patch_load: bool = True,
) -> support.FakeSource:
    """把 anime_cmd 的数据源、详情与 Bangumi 客户端替换为假实现。"""
    active_source = source if source is not None else support.FakeSource()
    active_detail = detail if detail is not None else support.load_detail()
    active_bangumi = bangumi if bangumi is not None else support.FakeBangumiClient()
    monkeypatch.setattr("mikan_qb.commands.anime_cmd.build_source", lambda *a, **k: active_source)
    monkeypatch.setattr("mikan_qb.commands._shared.build_source", lambda *a, **k: active_source)
    if patch_load:
        monkeypatch.setattr(
            "mikan_qb.commands.anime_cmd._load_bangumi", lambda *a, **k: active_detail
        )
    monkeypatch.setattr(
        "mikan_qb.commands.anime_cmd.build_bangumi_client", lambda *a, **k: active_bangumi
    )
    monkeypatch.setattr("mikan_qb.commands.anime_cmd.subject_id_from_url", lambda url: 12345)
    return active_source


def test_anime_search(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_anime(monkeypatch)
    result = runner.invoke(app, cli_base + ["anime", "search", "无职转生"])
    assert result.exit_code == 0
    assert "无职转生" in result.output


def test_anime_search_limit_json(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_anime(monkeypatch)
    result = runner.invoke(
        app, cli_base + ["anime", "search", "无职转生", "--limit", "1", "--json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 1


def test_anime_search_mikan_error(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_anime(monkeypatch, source=_MikanErrorSource())
    result = runner.invoke(app, cli_base + ["anime", "search", "无职转生"])
    assert result.exit_code == 2
    assert "搜索失败" in result.output


def test_anime_info(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_anime(monkeypatch)
    result = runner.invoke(app, cli_base + ["anime", "info", "3995"])
    assert result.exit_code == 0
    assert "3995" in result.output


def test_anime_info_mikan_error(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_anime(monkeypatch, source=_MikanErrorSource(), patch_load=False)
    result = runner.invoke(app, cli_base + ["anime", "info", "3995"])
    assert result.exit_code == 2
    assert "详情抓取失败" in result.output


@pytest.mark.parametrize("sort", ["latest", "name", "releases"])
def test_anime_subgroups_sorts(
    runner: CliRunner, cli_base: list[str], monkeypatch, sort: str
) -> None:
    _patch_anime(monkeypatch)
    result = runner.invoke(app, cli_base + ["anime", "subgroups", "3995", "--sort", sort, "--json"])
    assert result.exit_code == 0
    subgroups = json.loads(result.stdout)["subgroups"]
    assert subgroups
    if sort == "name":
        names = [s["name"] for s in subgroups]
        assert names == sorted(names)
    if sort == "releases":
        counts = [s["release_count"] for s in subgroups]
        assert counts == sorted(counts, reverse=True)


def test_anime_releases_default(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_anime(monkeypatch)
    result = runner.invoke(app, cli_base + ["anime", "releases", "3995", "--json"])
    assert result.exit_code == 0
    assert len(json.loads(result.stdout)) > 0


@pytest.mark.parametrize(
    "extra",
    [
        ["--full", "--limit", "3"],
        ["--limit", "2"],
        ["--subgroup", "203"],
        ["--ep", "1"],
        ["--lang", "简繁"],
        ["--include-pack"],
    ],
)
def test_anime_releases_filters(
    runner: CliRunner, cli_base: list[str], monkeypatch, extra: list[str]
) -> None:
    _patch_anime(monkeypatch)
    result = runner.invoke(app, cli_base + ["anime", "releases", "3995", *extra, "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    if "--limit" in extra and "--full" not in extra:
        assert len(payload) == 2
    if "--subgroup" in extra:
        assert all(r["subtitle_group_id"] == 203 for r in payload)
    if "--ep" in extra:
        assert all(r["episode"] == 1 for r in payload)
    if "--lang" in extra:
        assert all(r["language"] == "简繁" for r in payload)


def test_anime_releases_full_subgroup(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_anime(monkeypatch)
    result = runner.invoke(
        app, cli_base + ["anime", "releases", "3995", "--full", "--subgroup", "203", "--limit", "3"]
    )
    assert result.exit_code == 0


def test_anime_bangumi(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_anime(monkeypatch)
    result = runner.invoke(app, cli_base + ["anime", "bangumi", "3995"])
    assert result.exit_code == 0
    assert "Mushoku Tensei" in result.output


def test_anime_bangumi_schedule(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_anime(monkeypatch)
    result = runner.invoke(app, cli_base + ["anime", "bangumi", "3995", "--schedule", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["bgm_subject_id"] == 12345
    assert len(payload["schedule"]) == 3


def test_anime_bangumi_missing_subject_url(
    runner: CliRunner, cli_base: list[str], monkeypatch
) -> None:
    _patch_anime(monkeypatch)
    monkeypatch.setattr("mikan_qb.commands.anime_cmd.subject_id_from_url", lambda url: None)
    result = runner.invoke(app, cli_base + ["anime", "bangumi", "3995"])
    assert result.exit_code == 1
    assert "bgm.tv" in result.output


def test_season_list_all(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    source = support.FakeSource()
    monkeypatch.setattr("mikan_qb.commands.season_cmd.build_source", lambda *a, **k: source)
    result = runner.invoke(
        app, cli_base + ["season", "list", "--year", "2026", "--season", "夏", "--json"]
    )
    assert result.exit_code == 0
    assert len(json.loads(result.stdout)) == 2


def test_season_list_weekday_filter(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    source = support.FakeSource()
    monkeypatch.setattr("mikan_qb.commands.season_cmd.build_source", lambda *a, **k: source)
    result = runner.invoke(
        app,
        cli_base
        + ["season", "list", "--year", "2026", "--season", "夏", "--weekday", "6", "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert payload[0]["bangumi_id"] == 3995
