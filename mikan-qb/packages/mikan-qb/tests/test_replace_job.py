"""替换 job 的模型与持久化单元测试。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mikan_qb.replace.job import (
    PHASE_ABORTED,
    PHASE_AWAITING,
    PHASE_DONE,
    ReplaceError,
    ReplaceJob,
    TorrentSnapshot,
    find_active_job,
    job_file,
    list_jobs,
    load_job,
    new_job_id,
    save_job,
)


@pytest.fixture
def jobs_dir(tmp_path: Path, monkeypatch) -> Path:
    """把 jobs_dir 指向 tmp 下的嵌套目录。"""
    target = tmp_path / "nested" / "jobs"
    monkeypatch.setattr("mikan_qb.replace.job.jobs_dir", lambda: target)
    return target


def _job(job_id: str = "abc123", **overrides) -> ReplaceJob:
    """构造一条替换任务，允许覆盖任意字段。"""
    data = {
        "job_id": job_id,
        "bangumi_id": 3995,
        "bangumi_title": "无职转生",
        "from_subgroup_id": 236,
        "from_subgroup_name": "旧组",
        "to_subgroup_id": 203,
        "to_subgroup_name": "桜都字幕组",
        "created_at": datetime(2026, 1, 1, 12, 0),
        "updated_at": datetime(2026, 1, 1, 12, 0),
        "old_tag": "mikan-3995",
        "episodes": [1.0, 2.0],
        "old_torrents": [TorrentSnapshot(hash="h1", name="[旧] 番 [01]")],
    }
    data.update(overrides)
    return ReplaceJob(**data)


def test_new_job_id_unique() -> None:
    ids = {new_job_id() for _ in range(50)}
    assert len(ids) == 50


def test_save_and_load_roundtrip(jobs_dir: Path) -> None:
    path = save_job(_job())
    assert path == job_file("abc123")
    assert path.is_file()
    loaded = load_job("abc123")
    assert loaded.bangumi_id == 3995
    assert loaded.to_subgroup_name == "桜都字幕组"
    assert loaded.old_torrents[0].hash == "h1"
    assert loaded.phase == PHASE_AWAITING


def test_save_refreshes_updated_at(jobs_dir: Path) -> None:
    job = _job()
    old = job.updated_at
    save_job(job)
    assert job.updated_at > old


def test_load_missing_raises(jobs_dir: Path) -> None:
    with pytest.raises(ReplaceError):
        load_job("nope")


def test_load_corrupted_raises(jobs_dir: Path) -> None:
    path = job_file("bad")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 不是合法 JSON", encoding="utf-8")
    with pytest.raises(ReplaceError):
        load_job("bad")


def test_list_jobs_sorted_desc_and_skips_broken(jobs_dir: Path) -> None:
    save_job(_job("old", created_at=datetime(2026, 1, 1)))
    save_job(_job("new", created_at=datetime(2026, 2, 1)))
    (jobs_dir / "broken.json").write_text("nope", encoding="utf-8")
    rows = list_jobs()
    assert [job.job_id for job in rows] == ["new", "old"]


def test_find_active_job_only_awaiting(jobs_dir: Path) -> None:
    save_job(_job("done", phase=PHASE_DONE))
    save_job(_job("active", phase=PHASE_AWAITING))
    save_job(_job("other", bangumi_id=1, phase=PHASE_ABORTED))
    active = find_active_job(3995)
    assert active is not None
    assert active.job_id == "active"
    assert find_active_job(1) is None


def test_updated_at_default_ordering(jobs_dir: Path) -> None:
    early = _job("a", created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1))
    late = _job(
        "b",
        created_at=datetime(2026, 1, 2),
        updated_at=datetime(2026, 1, 2) + timedelta(hours=1),
    )
    save_job(early)
    save_job(late)
    assert {job.job_id for job in list_jobs()} == {"a", "b"}
