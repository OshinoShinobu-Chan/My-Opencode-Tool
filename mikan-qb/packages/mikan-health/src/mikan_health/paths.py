"""包内路径解析：package/profiles 目录与缓存数据库位置。"""

from __future__ import annotations

import os
from pathlib import Path

from mikan_health import PROFILES_DIR_NAME


def package_root() -> Path:
    """返回包根目录（其下含 profiles/，也即项目/安装根）。"""
    return Path(__file__).resolve().parent.parent.parent


def profiles_dir() -> Path:
    """返回内置 profile 的目录。"""
    return package_root() / PROFILES_DIR_NAME


def default_cache_path() -> Path:
    """缓存数据库路径：MIKAN_HEALTH_CACHE 覆盖优先，否则用系统缓存目录。"""
    override = os.environ.get("MIKAN_HEALTH_CACHE")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "mikan-health" / "cache.sqlite3"
