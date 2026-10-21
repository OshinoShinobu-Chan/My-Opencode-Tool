"""config / env / state / data / plans 路径解析的单元测试。"""

from __future__ import annotations

from pathlib import Path

from mikan_qb.paths import (
    data_dir,
    default_config_path,
    default_env_path,
    find_config_path,
    find_env_path,
    plans_dir,
    repo_root,
    state_path,
)


def test_find_config_path_explicit(tmp_path: Path) -> None:
    explicit = tmp_path / "custom.toml"
    assert find_config_path(explicit) == explicit


def test_find_config_path_prefers_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    candidate = tmp_path / "config.toml"
    candidate.write_text("[mikan]\n", encoding="utf-8")
    assert find_config_path() == candidate


def test_find_config_path_falls_back_to_package(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert find_config_path() == default_config_path()


def test_find_env_path_explicit(tmp_path: Path) -> None:
    explicit = tmp_path / "custom.env"
    assert find_env_path(explicit) == explicit


def test_find_env_path_prefers_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    candidate = tmp_path / ".env"
    candidate.write_text("QB_HOST=x\n", encoding="utf-8")
    assert find_env_path() == candidate


def test_find_env_path_falls_back_to_repo(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert find_env_path() == default_env_path()


def test_default_locations() -> None:
    assert default_config_path().name == "config.toml"
    assert default_env_path() == repo_root() / ".env"
    assert state_path() == repo_root() / "state.json"
    assert data_dir() == repo_root() / "data"
    assert plans_dir() == repo_root() / "plans"
