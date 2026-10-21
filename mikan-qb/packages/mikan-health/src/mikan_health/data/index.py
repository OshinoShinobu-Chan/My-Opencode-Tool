"""字幕组 id 与作品年表 id 的本地 SQLite 索引。"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from mikan_health.data.models import Subgroup
from mikan_health.paths import default_cache_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS subgroup_index (
    subgroup_id      INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    publish_group_id INTEGER,
    seen_at          REAL NOT NULL
);
"""


class SubgroupIndex:
    """subgroup_id → (名称, publish_group_id) 的本地索引。

    用于对应「RSS 的 subgroupid」与「年表页的 PublishGroup id」；解析番剧页时更新。
    """

    def __init__(self, path: Path | None = None) -> None:
        """初始化索引并建表。

        Args:
            path: 数据库路径；None 用默认路径。
        """
        self.path = (
            Path(path) if path is not None else default_cache_path().with_name("subgroups.sqlite3")
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def upsert(self, subgroups: list[Subgroup]) -> None:
        """批量写入/更新字幕组索引。

        Args:
            subgroups: 待写入的字幕组列表；空列表直接返回。
        """
        if not subgroups:
            return
        now = time.time()
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO subgroup_index (subgroup_id, name, publish_group_id, seen_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(subgroup_id) DO UPDATE SET name = excluded.name, "
                "publish_group_id = COALESCE(excluded.publish_group_id, publish_group_id), "
                "seen_at = excluded.seen_at",
                [(s.subtitle_group_id, s.name, s.publish_group_id, now) for s in subgroups],
            )

    def lookup(self, subgroup_id: int) -> tuple[str, int | None] | None:
        """按 subgroup_id 查 (名称, publish_group_id)；无则 None。

        Args:
            subgroup_id: 字幕组 id。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name, publish_group_id FROM subgroup_index WHERE subgroup_id = ?",
                (subgroup_id,),
            ).fetchone()
        return (row[0], row[1]) if row else None

    def search(self, keyword: str) -> list[tuple[int, str, int | None]]:
        """按名称模糊搜索，返回 (id, 名称, publish_group_id) 列表。

        Args:
            keyword: 名称包含的搜索关键词。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT subgroup_id, name, publish_group_id FROM subgroup_index "
                "WHERE name LIKE ? ORDER BY name",
                (f"%{keyword}%",),
            ).fetchall()
        return [(int(r[0]), r[1], r[2]) for r in rows]
