"""state.json 托管清单的读写、查找与增删单元测试。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from mikan_qb.state import ManagedEntry, State, load_state, save_state


def _entry(bangumi_id: int = 1, subgroup_id: int = 10, **overrides) -> ManagedEntry:
    """构造一条托管记录，允许覆盖任意字段。"""
    data = {
        "bangumi_id": bangumi_id,
        "subgroup_id": subgroup_id,
        "bangumi_title": "测试番",
        "updated_at": datetime(2026, 1, 1, 12, 0),
    }
    data.update(overrides)
    return ManagedEntry(**data)


@pytest.fixture
def state_file(tmp_path: Path, monkeypatch) -> Path:
    """把 state_path 指向 tmp 下的嵌套路径。"""
    path = tmp_path / "nested" / "state.json"
    monkeypatch.setattr("mikan_qb.state.state_path", lambda: path)
    return path


def test_load_state_missing_file_returns_empty(state_file: Path) -> None:
    state = load_state()
    assert isinstance(state, State)
    assert state.entries == []
    assert state.version == 1


def test_load_state_corrupted_json_returns_empty(state_file: Path) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("{ 这不是合法 JSON", encoding="utf-8")
    assert load_state().entries == []


def test_save_state_creates_parent_and_roundtrips(state_file: Path) -> None:
    save_state(State(entries=[_entry()]))
    assert state_file.is_file()
    loaded = load_state()
    assert len(loaded.entries) == 1
    assert loaded.entries[0].bangumi_id == 1
    assert loaded.entries[0].bangumi_title == "测试番"


def test_find_with_and_without_subgroup() -> None:
    state = State(entries=[_entry(1, 10), _entry(1, 20), _entry(2, 10)])
    assert state.find(1, 10).subgroup_id == 10
    assert state.find(1, 20).subgroup_id == 20
    assert state.find(1).subgroup_id == 20
    assert state.find(1, 99) is None
    assert state.find(3) is None


def test_upsert_deduplicates_by_bangumi_and_subgroup() -> None:
    state = State(entries=[_entry(1, 10, bangumi_title="旧")])
    state.upsert(_entry(1, 10, bangumi_title="新"))
    assert len(state.entries) == 1
    assert state.entries[0].bangumi_title == "新"

    state.upsert(_entry(1, 20))
    assert len(state.entries) == 2

    state.upsert(_entry(2, 10))
    assert len(state.entries) == 3


def test_drop_returns_removed_count() -> None:
    state = State(entries=[_entry(1, 10), _entry(1, 20), _entry(2, 10)])
    assert state.drop(1, 10) == 1
    assert state.drop(1) == 1
    assert state.drop(9) == 0
    assert len(state.entries) == 1
