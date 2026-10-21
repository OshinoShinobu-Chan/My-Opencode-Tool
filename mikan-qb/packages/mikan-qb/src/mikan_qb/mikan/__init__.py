"""Mikan 数据源与 Bangumi 客户端的构建及本地缓存路径。"""

from __future__ import annotations

from pathlib import Path

from mikan_health.data import HttpCache, MikanSource, SubgroupIndex
from mikan_health.data.bangumi import BangumiClient
from mikan_qb.config import AppConfig, EnvSettings, effective_mikan_base
from mikan_qb.paths import data_dir, repo_root


def cache_path():
    """HTTP 缓存数据库路径。"""
    return data_dir() / "cache.sqlite3"


def index_path():
    """字幕组索引数据库路径。"""
    return data_dir() / "subgroups.sqlite3"


def build_source(config: AppConfig, env: EnvSettings) -> MikanSource:
    """按配置构建带缓存、限速与字幕组索引的 MikanSource。

    Args:
        config: 应用配置，提供地址、缓存与限速参数。
        env: 环境凭据，可覆盖 Mikan 地址。
    """
    return MikanSource(
        base_url=effective_mikan_base(config, env),
        taxonomy=config.taxonomy,
        cache=HttpCache(cache_path()),
        index=SubgroupIndex(index_path()),
        ttl_hours=config.mikan.cache_ttl_hours,
        rate_limit_per_sec=config.mikan.rate_limit_per_sec,
        timeout=config.mikan.timeout_seconds,
        max_retries=config.mikan.max_retries,
    )


def default_bgm_token_paths() -> list[Path]:
    """Bangumi token 的默认候选路径列表。"""
    return [
        repo_root() / "token.txt",
        repo_root().parent / "bangumi" / "token.txt",
        Path.home() / ".config" / "bangumi" / "token",
    ]


def build_bangumi_client(config: AppConfig, env: EnvSettings) -> BangumiClient:
    """按配置构建 BangumiClient，token 优先取 .env 中的 BGM_TOKEN。

    Args:
        config: 应用配置，提供地址、缓存与 token 文件参数。
        env: 环境凭据，bgm_token / bgm_proxy 可覆盖。
    """
    token_paths = default_bgm_token_paths()
    if config.bangumi.token_file:
        token_paths.insert(0, Path(config.bangumi.token_file))
    return BangumiClient(
        token=env.bgm_token or None,
        base_url=config.bangumi.base_url,
        cache=HttpCache(cache_path()),
        ttl_hours=config.bangumi.cache_ttl_hours,
        proxy=env.bgm_proxy or None,
        rate_limit_per_sec=config.bangumi.rate_limit_per_sec,
        timeout=config.bangumi.timeout_seconds,
        token_paths=token_paths,
    )
