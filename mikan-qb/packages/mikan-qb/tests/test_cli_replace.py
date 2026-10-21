"""CLI replace 子命令的离线测试：阶段一、status、finalize、abort。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import support
from mikan_qb.cli import app
from mikan_qb.config import AppConfig, EnvSettings
from mikan_qb.replace.job import (
    PHASE_AWAITING,
    PHASE_DONE,
    ReplaceJob,
    TorrentSnapshot,
    load_job,
    save_job,
)
from mikan_qb.rules import Plan, build_plan
from mikan_qb.state import ManagedEntry, State

BANGUMI_ID = 3995
SAKURATO_SID = 203
OLD_SID = 236


def _make_plan() -> Plan:
    """用测试夹具构建一份真实的 203 字幕组计划。"""
    return build_plan(
        AppConfig(),
        EnvSettings(_env_file=None),
        support.load_detail(),
        support.load_releases(),
        SAKURATO_SID,
        "normal",
    )


def _old_entry(subgroup_id: int = OLD_SID, rule_name: str = "简体/无职转生") -> ManagedEntry:
    """构造一条旧字幕组的托管记录。"""
    return ManagedEntry(
        bangumi_id=BANGUMI_ID,
        subgroup_id=subgroup_id,
        bangumi_title="无职转生",
        subgroup_name="旧组",
        season="2026夏",
        mode="normal",
        feed_path="2026夏/无职转生@旧组",
        feed_url="https://mikan/RSS/Bangumi?bangumiId=3995&subgroupid=236",
        feed_created=True,
        rule_name=rule_name,
        tag="mikan-3995",
    )


@pytest.fixture
def jobs(tmp_path: Path, monkeypatch) -> Path:
    """把 replace job 目录指向 tmp。"""
    target = tmp_path / "jobs"
    monkeypatch.setattr("mikan_qb.replace.job.jobs_dir", lambda: target)
    return target


def _patch(monkeypatch, tmp_path: Path, client, state: State | None = None):
    """替换 replace_cmd 的数据源 / qB / 状态读写，返回 (状态, 保存记录列表)。"""
    monkeypatch.setattr(
        "mikan_qb.commands.replace_cmd.build_source", lambda *a, **k: support.FakeSource()
    )
    monkeypatch.setattr("mikan_qb.commands.replace_cmd._bangumi", lambda *a, **k: None)
    monkeypatch.setattr("mikan_qb.commands.replace_cmd._qb", lambda app_ctx: client)
    monkeypatch.setattr(
        "mikan_qb.commands.replace_cmd.save_plan", lambda plan: tmp_path / "plan.json"
    )
    saved: list[State] = []
    st = state if state is not None else State(entries=[_old_entry()])
    monkeypatch.setattr("mikan_qb.commands.replace_cmd.load_state", lambda: st)
    monkeypatch.setattr("mikan_qb.commands.replace_cmd.save_state", lambda s: saved.append(s))
    return st, saved


def _replace_job(job_id: str = "job0001", **overrides) -> ReplaceJob:
    """构造一条可写入 jobs 目录的替换任务。"""
    plan = _make_plan()
    data = {
        "job_id": job_id,
        "bangumi_id": BANGUMI_ID,
        "bangumi_title": "无职转生",
        "from_subgroup_id": OLD_SID,
        "from_subgroup_name": "旧组",
        "to_subgroup_id": SAKURATO_SID,
        "to_subgroup_name": plan.subgroup_name,
        "mode": "normal",
        "season": "2026夏",
        "created_at": datetime(2026, 1, 1, 12, 0),
        "updated_at": datetime(2026, 1, 1, 12, 0),
        "new_feed_path": "2026夏/无职转生@桜都字幕组",
        "new_feed_url": "https://mikan/RSS/Bangumi?bangumiId=3995&subgroupid=203",
        "new_feed_created": True,
        "new_rule_name": "简繁/无职转生",
        "new_tag": "简繁",
        "old_feed_path": "2026夏/无职转生@旧组",
        "old_feed_created": True,
        "old_rule_name": "简体/无职转生",
        "old_tag": "mikan-3995",
        "episodes": [1.0, 2.0],
        "old_torrents": [TorrentSnapshot(hash="old1", name="[旧组] 无职转生 [01]")],
    }
    data.update(overrides)
    return ReplaceJob(**data)


def _tagged(name: str, hash_: str, progress: float = 1.0) -> support.FakeTorrent:
    """构造带 mikan 标签的种子。"""
    return support.FakeTorrent(name, hash_, progress=progress, tags=["mikan-3995"])


def test_replace_start_dry_run(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch, jobs: Path
) -> None:
    client = support.FakeQBClient(torrents=[_tagged("[旧组] 无职转生 [01]", "old1")])
    _patch(monkeypatch, tmp_path, client)
    result = runner.invoke(app, cli_base + ["replace", "--bangumi", "3995", "--to", "203"])
    assert result.exit_code == 0
    assert "dry-run" in result.output
    assert not any(call[0] == "set_rule" for call in client.calls)
    assert not list(jobs.glob("*.json"))


def test_replace_start_apply(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch, jobs: Path
) -> None:
    plan = _make_plan()
    client = support.FakeQBClient(torrents=[_tagged("[旧组] 无职转生 [01]", "old1")])
    _st, saved = _patch(monkeypatch, tmp_path, client)
    result = runner.invoke(
        app, cli_base + ["replace", "--bangumi", "3995", "--to", "203", "--apply"]
    )
    assert result.exit_code == 0
    assert ("set_rule", plan.rule_name) in client.calls
    assert ("add_feed", plan.feed_path) in client.calls
    assert any(call[0] == "add_torrents" for call in client.calls)
    assert len(saved) == 1
    assert {e.subgroup_id for e in saved[0].entries} == {OLD_SID, SAKURATO_SID}

    files = list(jobs.glob("*.json"))
    assert len(files) == 1
    job = load_job(files[0].stem)
    assert job.phase == PHASE_AWAITING
    assert job.old_torrents[0].hash == "old1"
    assert job.to_subgroup_id == SAKURATO_SID
    assert job.episodes == [m.episode for m in plan.matches]


def test_replace_start_missing_state_exits_3(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch, jobs: Path
) -> None:
    client = support.FakeQBClient()
    _patch(monkeypatch, tmp_path, client, state=State())
    result = runner.invoke(app, cli_base + ["replace", "--bangumi", "3995", "--to", "203"])
    assert result.exit_code == 3
    assert "没有番剧" in result.output


def test_replace_start_ambiguous_requires_from(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch, jobs: Path
) -> None:
    state = State(entries=[_old_entry(OLD_SID), _old_entry(210, rule_name="繁中/无职转生")])
    client = support.FakeQBClient(torrents=[_tagged("[旧组] 无职转生 [01]", "old1")])
    _patch(monkeypatch, tmp_path, client, state=state)

    ambiguous = runner.invoke(app, cli_base + ["replace", "--bangumi", "3995", "--to", "203"])
    assert ambiguous.exit_code == 3
    assert "多条托管记录" in ambiguous.output

    chosen = runner.invoke(
        app, cli_base + ["replace", "--bangumi", "3995", "--to", "203", "--from", "236"]
    )
    assert chosen.exit_code == 0


def test_replace_start_requires_bangumi_and_to(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch, jobs: Path
) -> None:
    client = support.FakeQBClient()
    _patch(monkeypatch, tmp_path, client)
    result = runner.invoke(app, cli_base + ["replace", "--bangumi", "3995"])
    assert result.exit_code == 1


def test_replace_status_reports_missing(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch, jobs: Path
) -> None:
    save_job(_replace_job())
    client = support.FakeQBClient(
        torrents=[
            _tagged("[旧组] 无职转生 [01]", "old1"),
            _tagged("[桜都字幕组] 无职转生 [01][1080p][简繁内封]", "new1", progress=1.0),
            _tagged("[桜都字幕组] 无职转生 [02][1080p][简繁内封]", "new2", progress=0.4),
        ]
    )
    _patch(monkeypatch, tmp_path, client)
    result = runner.invoke(app, cli_base + ["replace", "status", "job0001"])
    assert result.exit_code == 0
    assert "已完成 1/2" in result.output
    assert "2.0" in result.output

    as_json = runner.invoke(app, cli_base + ["replace", "status", "job0001", "--json"])
    assert as_json.exit_code == 0
    payload = json.loads(as_json.output)
    assert payload["missing"] == [2.0]
    assert payload["complete"] is False


def test_replace_status_missing_job_exits_1(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch, jobs: Path
) -> None:
    _patch(monkeypatch, tmp_path, support.FakeQBClient())
    result = runner.invoke(app, cli_base + ["replace", "status", "nope"])
    assert result.exit_code == 1


def test_replace_finalize_dry_run_no_delete(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch, jobs: Path
) -> None:
    save_job(_replace_job())
    client = support.FakeQBClient(
        torrents=[
            _tagged("[旧组] 无职转生 [01]", "old1"),
            _tagged("[桜都字幕组] 无职转生 [01][1080p][简繁内封]", "new1"),
            _tagged("[桜都字幕组] 无职转生 [02][1080p][简繁内封]", "new2"),
        ]
    )
    _patch(monkeypatch, tmp_path, client)
    result = runner.invoke(app, cli_base + ["replace", "finalize", "job0001"])
    assert result.exit_code == 0
    assert "dry-run" in result.output
    assert not any(call[0] == "delete_torrents" for call in client.calls)


def test_replace_finalize_blocks_when_incomplete(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch, jobs: Path
) -> None:
    save_job(_replace_job())
    client = support.FakeQBClient(
        torrents=[
            _tagged("[旧组] 无职转生 [01]", "old1"),
            _tagged("[桜都字幕组] 无职转生 [01][1080p][简繁内封]", "new1"),
            _tagged("[桜都字幕组] 无职转生 [02][1080p][简繁内封]", "new2", progress=0.2),
        ]
    )
    _patch(monkeypatch, tmp_path, client)
    result = runner.invoke(app, cli_base + ["replace", "finalize", "job0001", "--apply"])
    assert result.exit_code == 3
    assert "未下完" in result.output
    assert not any(call[0] == "delete_torrents" for call in client.calls)


def test_replace_finalize_apply_deletes_old(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch, jobs: Path
) -> None:
    save_job(_replace_job())
    client = support.FakeQBClient(
        torrents=[
            _tagged("[旧组] 无职转生 [01]", "old1"),
            _tagged("[桜都字幕组] 无职转生 [01][1080p][简繁内封]", "new1"),
            _tagged("[桜都字幕组] 无职转生 [02][1080p][简繁内封]", "new2"),
        ]
    )
    _st, saved = _patch(monkeypatch, tmp_path, client)
    result = runner.invoke(app, cli_base + ["replace", "finalize", "job0001", "--apply"])
    assert result.exit_code == 0
    assert ("delete_torrents", ("old1",), True) in client.calls
    assert ("remove_rule", "简体/无职转生") in client.calls
    assert ("remove_item", "2026夏/无职转生@旧组") in client.calls
    assert saved[0].entries == []
    assert load_job("job0001").phase == PHASE_DONE


def test_replace_finalize_force_ignores_missing(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch, jobs: Path
) -> None:
    save_job(_replace_job())
    client = support.FakeQBClient(
        torrents=[
            _tagged("[旧组] 无职转生 [01]", "old1"),
            _tagged("[桜都字幕组] 无职转生 [01][1080p][简繁内封]", "new1"),
            _tagged("[桜都字幕组] 无职转生 [02][1080p][简繁内封]", "new2", progress=0.1),
        ]
    )
    _patch(monkeypatch, tmp_path, client)
    result = runner.invoke(app, cli_base + ["replace", "finalize", "job0001", "--apply", "--force"])
    assert result.exit_code == 0
    assert ("delete_torrents", ("old1",), True) in client.calls


def test_replace_abort_dry_run(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch, jobs: Path
) -> None:
    save_job(_replace_job())
    client = support.FakeQBClient(
        torrents=[_tagged("[桜都字幕组] 无职转生 [01][1080p][简繁内封]", "new1")]
    )
    _patch(monkeypatch, tmp_path, client)
    result = runner.invoke(app, cli_base + ["replace", "abort", "job0001"])
    assert result.exit_code == 0
    assert "dry-run" in result.output
    assert not any(call[0] == "remove_rule" for call in client.calls)


def test_replace_abort_purge_new(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch, jobs: Path
) -> None:
    save_job(_replace_job())
    client = support.FakeQBClient(
        torrents=[
            _tagged("[桜都字幕组] 无职转生 [01][1080p][简繁内封]", "new1"),
            _tagged("[桜都字幕组] 无职转生 [02][1080p][简繁内封]", "new2"),
        ]
    )
    state = State(entries=[_old_entry(), _old_entry(SAKURATO_SID, rule_name="简繁/无职转生")])
    _st, saved = _patch(monkeypatch, tmp_path, client, state=state)
    result = runner.invoke(
        app, cli_base + ["replace", "abort", "job0001", "--apply", "--purge-new"]
    )
    assert result.exit_code == 0
    assert ("delete_torrents", ("new1", "new2"), True) in client.calls
    assert ("remove_rule", "简繁/无职转生") in client.calls
    assert ("remove_item", "2026夏/无职转生@桜都字幕组") in client.calls
    assert {e.subgroup_id for e in saved[0].entries} == {OLD_SID}
