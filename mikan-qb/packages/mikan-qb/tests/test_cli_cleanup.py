"""CLI cleanup 命令的离线测试。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from typer.testing import CliRunner

from mikan_qb.cli import app
from mikan_qb.state import ManagedEntry, State

DAY = 86400.0


def _age(path: Path, days: float) -> None:
    """把文件 mtime 设为 days 天前。"""
    stamp = time.time() - days * DAY
    os.utime(path, (stamp, stamp))


def _dirs(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    """把 plans / jobs / data 重定向到 tmp。"""
    plans = tmp_path / "plans"
    jobs = tmp_path / "jobs"
    data = tmp_path / "data"
    for d in (plans, jobs, data):
        d.mkdir()
    monkeypatch.setattr("mikan_qb.maintenance.cleanup.plans_dir", lambda: plans)
    monkeypatch.setattr("mikan_qb.maintenance.cleanup.jobs_dir", lambda: jobs)
    monkeypatch.setattr("mikan_qb.maintenance.cleanup.data_dir", lambda: data)
    monkeypatch.setattr("mikan_qb.replace.job.jobs_dir", lambda: jobs)
    return {"plans": plans, "jobs": jobs, "data": data}


def _patch_state(monkeypatch, state: State | None = None) -> None:
    """替换 cleanup_cmd 的 load_state。"""
    st = state if state is not None else State()
    monkeypatch.setattr("mikan_qb.commands.cleanup_cmd.load_state", lambda: st)


def test_cleanup_dry_run_lists_stale(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch
) -> None:
    dirs = _dirs(tmp_path, monkeypatch)
    _patch_state(monkeypatch)
    old = dirs["plans"] / "3995-203.json"
    old.write_text("{}", encoding="utf-8")
    _age(old, days=40)

    result = runner.invoke(app, cli_base + ["cleanup", "run"])
    assert result.exit_code == 0
    assert "dry-run" in result.output
    assert old.exists()


def test_cleanup_apply_removes_stale(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch
) -> None:
    dirs = _dirs(tmp_path, monkeypatch)
    _patch_state(monkeypatch)
    old = dirs["plans"] / "3995-203.json"
    old.write_text("{}", encoding="utf-8")
    _age(old, days=40)

    result = runner.invoke(app, cli_base + ["cleanup", "run", "--apply", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["applied"] is True
    assert payload["counts"]["plans"] == 1
    assert not old.exists()


def test_cleanup_keeps_managed_plan(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch
) -> None:
    dirs = _dirs(tmp_path, monkeypatch)
    _patch_state(monkeypatch, State(entries=[ManagedEntry(bangumi_id=3995, subgroup_id=203)]))
    managed = dirs["plans"] / "3995-203.json"
    managed.write_text("{}", encoding="utf-8")
    _age(managed, days=99)

    result = runner.invoke(app, cli_base + ["cleanup", "run", "--apply", "--json"])
    payload = json.loads(result.output)
    assert payload["kept_plans"] == 1
    assert payload["counts"]["plans"] == 0
    assert managed.exists()


def test_cleanup_no_cache_flag(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch
) -> None:
    dirs = _dirs(tmp_path, monkeypatch)
    _patch_state(monkeypatch)
    cache = dirs["data"] / "old.sqlite"
    cache.write_text("x", encoding="utf-8")
    _age(cache, days=40)

    result = runner.invoke(app, cli_base + ["cleanup", "run", "--no-cache", "--apply", "--json"])
    payload = json.loads(result.output)
    assert payload["counts"]["cache"] == 0
    assert cache.exists()
