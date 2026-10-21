"""仓库内配置文件、状态文件与数据目录的路径解析。"""

from __future__ import annotations

from pathlib import Path


def package_dir() -> Path:
    """mikan-qb 包根目录。"""
    return Path(__file__).resolve().parent.parent.parent


def repo_root() -> Path:
    """仓库根目录。"""
    return package_dir().parent.parent


def default_config_path() -> Path:
    """包内自带的 config.toml 路径。"""
    return package_dir() / "config.toml"


def default_env_path() -> Path:
    """仓库根目录下的 .env 路径。"""
    return repo_root() / ".env"


def find_config_path(explicit: Path | None = None) -> Path:
    """定位 config.toml：显式路径 > 当前目录 > 包内默认。

    Args:
        explicit: 显式指定的路径；非 None 时直接返回。
    """
    if explicit is not None:
        return explicit
    candidate = Path.cwd() / "config.toml"
    if candidate.is_file():
        return candidate
    return default_config_path()


def find_env_path(explicit: Path | None = None) -> Path:
    """定位 .env：显式路径 > 当前目录 > 仓库默认。

    Args:
        explicit: 显式指定的路径；非 None 时直接返回。
    """
    if explicit is not None:
        return explicit
    candidate = Path.cwd() / ".env"
    if candidate.is_file():
        return candidate
    return default_env_path()


def state_path() -> Path:
    """托管清单 state.json 路径。"""
    return repo_root() / "state.json"


def data_dir() -> Path:
    """本地缓存与索引的数据目录。"""
    return repo_root() / "data"


def plans_dir() -> Path:
    """订阅计划 JSON 的存放目录。"""
    return repo_root() / "plans"


def jobs_dir() -> Path:
    """换组替换事务（job）JSON 的存放目录。"""
    return repo_root() / "jobs"
