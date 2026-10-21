"""MikanSource / BangumiClient 构建与本地缓存路径的单元测试。"""

from __future__ import annotations

from pathlib import Path

from mikan_health.data import MikanSource
from mikan_health.data.bangumi import BangumiClient
from mikan_qb.config import AppConfig, EnvSettings
from mikan_qb.mikan import (
    build_bangumi_client,
    build_source,
    cache_path,
    default_bgm_token_paths,
    index_path,
)
from mikan_qb.paths import data_dir, repo_root


def _offline_paths(tmp_path: Path, monkeypatch) -> None:
    """把缓存与索引数据库路径改到 tmp，避免污染仓库 data 目录。"""
    monkeypatch.setattr("mikan_qb.mikan.cache_path", lambda: tmp_path / "cache.sqlite3")
    monkeypatch.setattr("mikan_qb.mikan.index_path", lambda: tmp_path / "subgroups.sqlite3")


def test_cache_and_index_paths() -> None:
    assert cache_path() == data_dir() / "cache.sqlite3"
    assert index_path() == data_dir() / "subgroups.sqlite3"


def test_default_bgm_token_paths() -> None:
    paths = default_bgm_token_paths()
    assert paths[0] == repo_root() / "token.txt"
    assert any(p.name == "token" for p in paths)
    assert all(isinstance(p, Path) for p in paths)


def test_build_source_returns_offline_source(tmp_path: Path, monkeypatch) -> None:
    _offline_paths(tmp_path, monkeypatch)
    config = AppConfig()
    source = build_source(config, EnvSettings(_env_file=None))
    assert isinstance(source, MikanSource)
    assert source.base_url == config.mikan.base_url
    assert source.index is not None
    assert source.cache is not None
    assert (tmp_path / "cache.sqlite3").is_file()
    assert (tmp_path / "subgroups.sqlite3").is_file()


def test_build_source_respects_env_base(tmp_path: Path, monkeypatch) -> None:
    _offline_paths(tmp_path, monkeypatch)
    env = EnvSettings(_env_file=None, mikan_base="https://env.example/")
    source = build_source(AppConfig(), env)
    assert source.base_url == "https://env.example"


def test_build_bangumi_client_returns_client(tmp_path: Path, monkeypatch) -> None:
    _offline_paths(tmp_path, monkeypatch)
    config = AppConfig()
    client = build_bangumi_client(config, EnvSettings(_env_file=None))
    assert isinstance(client, BangumiClient)
    assert client.base_url == config.bangumi.base_url
    assert (tmp_path / "cache.sqlite3").is_file()


def test_build_bangumi_client_uses_token_file(tmp_path: Path, monkeypatch) -> None:
    _offline_paths(tmp_path, monkeypatch)
    token_file = tmp_path / "token.txt"
    token_file.write_text("secret-token", encoding="utf-8")
    config = AppConfig.model_validate({"bangumi": {"token_file": str(token_file)}})
    client = build_bangumi_client(config, EnvSettings(_env_file=None, bgm_token=""))
    assert client.has_token is True
