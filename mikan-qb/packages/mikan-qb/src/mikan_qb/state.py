"""托管清单的持久化：记录已纳管的番剧 / 字幕组 / 规则。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from mikan_qb.paths import state_path


class ManagedEntry(BaseModel):
    """一条托管记录：某番剧采用的字幕组及对应 feed / 规则信息。

    Attributes:
        bangumi_id: 番剧 id。
        subgroup_id: 字幕组 id。
        bangumi_title: 番剧标题。
        subgroup_name: 字幕组名称。
        season: 季度标签。
        mode: 运行模式。
        feed_path: RSS feed 路径。
        feed_url: RSS feed URL。
        feed_created: feed 是否由本工具创建。
        rule_name: qB 规则名。
        tag: 用于定位种子的标签。
        save_path: 保存路径。
        updated_at: 最近更新时间。
    """

    bangumi_id: int
    subgroup_id: int
    bangumi_title: str = ""
    subgroup_name: str = ""
    season: str = ""
    mode: str = "normal"
    feed_path: str = ""
    feed_url: str = ""
    feed_created: bool = False
    rule_name: str = ""
    tag: str | None = None
    save_path: str = ""
    updated_at: datetime = Field(default_factory=datetime.now)


class State(BaseModel):
    """托管清单根对象，含版本号与条目列表。

    Attributes:
        version: 清单格式版本。
        entries: 托管条目列表。
    """

    version: int = 1
    entries: list[ManagedEntry] = Field(default_factory=list)

    def find(self, bangumi_id: int, subgroup_id: int | None = None) -> ManagedEntry | None:
        """按番剧与可选字幕组查找最近一条记录，找不到返回 None。

        Args:
            bangumi_id: 番剧 id。
            subgroup_id: 字幕组 id；None 表示不限字幕组。
        """
        matches = [
            e
            for e in self.entries
            if e.bangumi_id == bangumi_id and (subgroup_id is None or e.subgroup_id == subgroup_id)
        ]
        return matches[-1] if matches else None

    def upsert(self, entry: ManagedEntry) -> None:
        """按 (番剧, 字幕组) 去重后写入或更新记录。

        Args:
            entry: 待写入的托管条目。
        """
        self.entries = [
            e
            for e in self.entries
            if not (e.bangumi_id == entry.bangumi_id and e.subgroup_id == entry.subgroup_id)
        ]
        self.entries.append(entry)

    def drop(self, bangumi_id: int, subgroup_id: int | None = None) -> int:
        """删除匹配记录，返回删除条数。

        Args:
            bangumi_id: 番剧 id。
            subgroup_id: 字幕组 id；None 表示不限字幕组。
        """
        before = len(self.entries)
        self.entries = [
            e
            for e in self.entries
            if not (
                e.bangumi_id == bangumi_id and (subgroup_id is None or e.subgroup_id == subgroup_id)
            )
        ]
        return before - len(self.entries)


def load_state() -> State:
    """读取 state.json；缺失或损坏时返回空 State。"""
    path = state_path()
    if not path.is_file():
        return State()
    try:
        return State.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return State()


def save_state(state: State) -> None:
    """将 State 以缩进 JSON 写回磁盘，自动创建父目录。

    Args:
        state: 待保存的托管清单。
    """
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
