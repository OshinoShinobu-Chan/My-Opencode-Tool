"""定期清理扫描 / 执行逻辑的单元测试。"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from mikan_qb.maintenance import cleanup
from mikan_qb.replace.job import PHASE_DONE, ReplaceJob
from mikan_qb.state import ManagedEntry, State

DAY = 86400.0


@pytest.fixture
def dirs(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    """把 plans / jobs / data 目录重定向到 tmp。"""
    plans = tmp_path / "plans"
    jobs = tmp_path / "jobs"
    data = tmp_path / "data"
    plans.mkdir()
    jobs.mkdir()
    data.mkdir()
    monkeypatch.setattr("mikan_qb.maintenance.cleanup.plans_dir", lambda: plans)
    monkeypatch.setattr("mikan_qb.maintenance.cleanup.jobs_dir", lambda: jobs)
    monkeypatch.setattr("mikan_qb.maintenance.cleanup.data_dir", lambda: data)
    monkeypatch.setattr("mikan_qb.replace.job.jobs_dir", lambda: jobs)
    return {"plans": plans, "jobs": jobs, "data": data}


def _age(path: Path, days: float) -> None:
    """把文件 mtime 设为 days 天前。"""
    stamp = time.time() - days * DAY
    os.utime(path, (stamp, stamp))


def _plan_file(root: Path, bangumi: int, subgroup: int, days: float) -> Path:
    """写一个计划文件并把 mtime 调旧。"""
    path = root / f"{bangumi}-{subgroup}.json"
    path.write_text("{}", encoding="utf-8")
    _age(path, days)
    return path


def _job_file(root: Path, job_id: str, phase: str, days: float) -> Path:
    """写一个终态 job 并调旧 mtime。"""
    now = datetime.now()
    job = ReplaceJob(
        job_id=job_id,
        bangumi_id=1,
        to_subgroup_id=2,
        phase=phase,
        created_at=now,
        updated_at=now,
    )
    path = root / f"{job_id}.json"
    path.write_text(job.model_dump_json(), encoding="utf-8")
    _age(path, days)
    return path


def _entry(bangumi: int, subgroup: int) -> ManagedEntry:
    """构造一条托管记录。"""
    return ManagedEntry(bangumi_id=bangumi, subgroup_id=subgroup)


def test_scan_removes_stale_plan(dirs: dict[str, Path]) -> None:
    old = _plan_file(dirs["plans"], 3995, 203, days=40)
    fresh = _plan_file(dirs["plans"], 3995, 534, days=1)
    plan = cleanup.scan_cleanup(State(), plan_days=30, include_cache=False)
    assert old in plan.plans
    assert fresh not in plan.plans


def test_scan_keeps_managed_plan_even_if_old(dirs: dict[str, Path]) -> None:
    managed = _plan_file(dirs["plans"], 3995, 203, days=99)
    state = State(entries=[_entry(3995, 203)])
    plan = cleanup.scan_cleanup(state, plan_days=30, include_cache=False)
    assert managed not in plan.plans
    assert plan.kept_plans == 1


def test_scan_managed_plan_removed_when_not_keeping(dirs: dict[str, Path]) -> None:
    managed = _plan_file(dirs["plans"], 3995, 203, days=99)
    state = State(entries=[_entry(3995, 203)])
    plan = cleanup.scan_cleanup(state, plan_days=30, include_cache=False, keep_managed=False)
    assert managed in plan.plans


def test_scan_plan_days_zero_disables(dirs: dict[str, Path]) -> None:
    _plan_file(dirs["plans"], 3995, 203, days=99)
    plan = cleanup.scan_cleanup(State(), plan_days=0, include_cache=False)
    assert plan.plans == []


def test_scan_only_terminal_jobs(dirs: dict[str, Path]) -> None:
    done = _job_file(dirs["jobs"], "jobdone", PHASE_DONE, days=40)
    aborted = _job_file(dirs["jobs"], "jobabort", "aborted", days=40)
    awaiting = _job_file(dirs["jobs"], "jobawait", "awaiting_verification", days=40)
    plan = cleanup.scan_cleanup(State(), job_days=30, include_cache=False)
    assert set(plan.jobs) == {done, aborted}
    assert awaiting not in plan.jobs


def test_scan_fresh_job_kept(dirs: dict[str, Path]) -> None:
    fresh = _job_file(dirs["jobs"], "jobfresh", PHASE_DONE, days=1)
    plan = cleanup.scan_cleanup(State(), job_days=30, include_cache=False)
    assert fresh not in plan.jobs


def test_scan_cache_recurses(dirs: dict[str, Path]) -> None:
    nested = dirs["data"] / "sub" / "http.sqlite"
    nested.parent.mkdir()
    nested.write_text("x", encoding="utf-8")
    _age(nested, days=10)
    keep = dirs["data"] / "fresh.sqlite"
    keep.write_text("x", encoding="utf-8")
    plan = cleanup.scan_cleanup(State(), cache_days=7)
    assert nested in plan.cache
    assert keep not in plan.cache


def test_scan_cache_disabled(dirs: dict[str, Path]) -> None:
    nested = dirs["data"] / "old.sqlite"
    nested.write_text("x", encoding="utf-8")
    _age(nested, days=100)
    plan = cleanup.scan_cleanup(State(), cache_days=0)
    assert plan.cache == []
    plan2 = cleanup.scan_cleanup(State(), cache_days=7, include_cache=False)
    assert plan2.cache == []


def test_apply_cleanup_deletes_and_counts(dirs: dict[str, Path]) -> None:
    old = _plan_file(dirs["plans"], 3995, 203, days=40)
    job = _job_file(dirs["jobs"], "jobdone", PHASE_DONE, days=40)
    plan = cleanup.scan_cleanup(State(), plan_days=30, job_days=30, include_cache=False)
    counts = cleanup.apply_cleanup(plan)
    assert counts == {"plans": 1, "jobs": 1, "cache": 0}
    assert not old.exists()
    assert not job.exists()


def test_apply_cleanup_tolerates_missing(dirs: dict[str, Path]) -> None:
    old = _plan_file(dirs["plans"], 3995, 203, days=40)
    plan = cleanup.scan_cleanup(State(), plan_days=30, include_cache=False)
    old.unlink()
    counts = cleanup.apply_cleanup(plan)
    assert counts["plans"] == 0


def test_auto_cleanup_ignores_cache(dirs: dict[str, Path]) -> None:
    old = _plan_file(dirs["plans"], 3995, 203, days=40)
    cache = dirs["data"] / "old.sqlite"
    cache.write_text("x", encoding="utf-8")
    _age(cache, days=40)
    removed = cleanup.auto_cleanup(State(), plan_days=30, job_days=30)
    assert old in removed
    assert cache.exists()


def test_scan_ignores_state_json_in_jobs_dir(dirs: dict[str, Path]) -> None:
    state_file = dirs["jobs"] / "state.json"
    state_file.write_text(json.dumps({"entries": []}), encoding="utf-8")
    _age(state_file, days=99)
    plan = cleanup.scan_cleanup(State(), job_days=30, include_cache=False)
    assert state_file not in plan.jobs
