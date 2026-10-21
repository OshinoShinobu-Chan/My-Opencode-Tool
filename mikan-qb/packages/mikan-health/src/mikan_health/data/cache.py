"""HTTP 响应的 SQLite 缓存（带 TTL）。"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from mikan_health.paths import default_cache_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    url        TEXT PRIMARY KEY,
    body       BLOB NOT NULL,
    status     INTEGER NOT NULL,
    fetched_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_http_cache_fetched_at ON http_cache (fetched_at);
"""


@dataclass
class CacheStats:
    """缓存统计信息（条目数、字节数与时间范围）。

    Attributes:
        path: 缓存数据库路径。
        entries: 缓存条目数。
        bytes: 缓存总体积（字节）。
        oldest: 最旧条目的抓取时间戳（Unix 秒）；无条目为 None。
        newest: 最新条目的抓取时间戳（Unix 秒）；无条目为 None。
    """

    path: str
    entries: int
    bytes: int
    oldest: float | None
    newest: float | None


class HttpCache:
    """基于 SQLite 的按 URL 缓存，支持 TTL 与过期清理。"""

    def __init__(self, path: Path | None = None) -> None:
        """初始化缓存并建表。

        Args:
            path: 数据库路径；None 用默认路径。
        """
        self.path = Path(path) if path is not None else default_cache_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def get(self, url: str, ttl_hours: float) -> bytes | None:
        """取缓存体；缺失或超过 ttl_hours 返回 None。

        Args:
            url: 缓存键（完整请求 URL）。
            ttl_hours: 有效期（小时）；<=0 直接视为未命中。
        """
        if ttl_hours <= 0:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT body, fetched_at FROM http_cache WHERE url = ?", (url,)
            ).fetchone()
        if row is None:
            return None
        body, fetched_at = row
        if time.time() - fetched_at > ttl_hours * 3600:
            return None
        return body

    def put(self, url: str, body: bytes, status: int = 200) -> None:
        """写入/覆盖某 URL 的响应体。

        Args:
            url: 缓存键（完整请求 URL）。
            body: 响应体字节。
            status: HTTP 状态码。
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO http_cache (url, body, status, fetched_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(url) DO UPDATE SET body = excluded.body, "
                "status = excluded.status, fetched_at = excluded.fetched_at",
                (url, body, status, time.time()),
            )

    def stats(self) -> CacheStats:
        """汇总缓存条目数、总字节与时间范围。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(body)), 0), MIN(fetched_at), MAX(fetched_at) "
                "FROM http_cache"
            ).fetchone()
        entries, size, oldest, newest = row
        return CacheStats(
            path=str(self.path), entries=entries, bytes=size, oldest=oldest, newest=newest
        )

    def clear(self, older_than_hours: float | None = None) -> int:
        """删除全部缓存，或只删超过 older_than_hours 的条目；返回删除数。

        Args:
            older_than_hours: 仅删更早的条目；None 表示清空全部。
        """
        with self._connect() as conn:
            if older_than_hours is None:
                cursor = conn.execute("DELETE FROM http_cache")
            else:
                cutoff = time.time() - older_than_hours * 3600
                cursor = conn.execute("DELETE FROM http_cache WHERE fetched_at < ?", (cutoff,))
            return cursor.rowcount
