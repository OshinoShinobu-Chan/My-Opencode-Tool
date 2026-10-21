"""subgroup / rule / apply 子命令的命令行测试：评分、巡检、规则模拟与写入。"""

from __future__ import annotations

from typer.testing import CliRunner

import support
from mikan_qb.cli import app
from mikan_qb.config import AppConfig, EnvSettings
from mikan_qb.rules.plan import build_plan
from mikan_qb.state import ManagedEntry, State


def _patch_subgroup(monkeypatch, source, bangumi=None) -> None:
    """把 subgroup_cmd 的数据源与 Bangumi 开关替换为假实现。"""
    monkeypatch.setattr("mikan_qb.commands.subgroup_cmd.build_source", lambda *a, **k: source)
    monkeypatch.setattr("mikan_qb.commands.subgroup_cmd._bangumi", lambda *a, **k: bangumi)


def test_subgroup_rank_default(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_subgroup(monkeypatch, support.FakeSource())
    result = runner.invoke(app, cli_base + ["subgroup", "rank", "3995"])
    assert result.exit_code == 0
    assert "候选" in result.output


def test_subgroup_rank_explain(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_subgroup(monkeypatch, support.FakeSource())
    result = runner.invoke(app, cli_base + ["subgroup", "rank", "3995", "--explain"])
    assert result.exit_code == 0


def test_subgroup_rank_page_only(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_subgroup(monkeypatch, support.FakeSource())
    result = runner.invoke(
        app, cli_base + ["subgroup", "rank", "3995", "--page-only", "--deep", "0"]
    )
    assert result.exit_code == 0


def test_subgroup_rank_deep_zero(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_subgroup(monkeypatch, support.FakeSource())
    result = runner.invoke(app, cli_base + ["subgroup", "rank", "3995", "--deep", "0", "--json"])
    assert result.exit_code == 0


def test_subgroup_stats_by_name(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    index = support.FakeIndex(matches=[(203, "桜都字幕组", 999)], lookup_value=("桜都字幕组", 999))
    _patch_subgroup(monkeypatch, support.FakeSource(index=index))
    result = runner.invoke(app, cli_base + ["subgroup", "stats", "--name", "桜都"])
    assert result.exit_code == 0
    assert "203" in result.output


def test_subgroup_stats_ambiguous_name(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    index = support.FakeIndex(matches=[(203, "桜都字幕组", 999), (204, "桜都字幕组二队", 998)])
    _patch_subgroup(monkeypatch, support.FakeSource(index=index))
    result = runner.invoke(app, cli_base + ["subgroup", "stats", "--name", "桜都"])
    assert result.exit_code == 3
    assert "匹配到多个字幕组" in result.output


def test_subgroup_stats_name_not_found(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_subgroup(monkeypatch, support.FakeSource(index=support.FakeIndex(matches=[])))
    result = runner.invoke(app, cli_base + ["subgroup", "stats", "--name", "不存在"])
    assert result.exit_code == 1


def test_subgroup_stats_missing_id(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_subgroup(monkeypatch, support.FakeSource())
    result = runner.invoke(app, cli_base + ["subgroup", "stats"])
    assert result.exit_code == 1


def test_subgroup_stats_from_bangumi(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_subgroup(monkeypatch, support.FakeSource())
    monkeypatch.setattr(
        "mikan_qb.commands.subgroup_cmd._load_bangumi",
        lambda *a, **k: support.load_detail(),
    )
    result = runner.invoke(app, cli_base + ["subgroup", "stats", "203", "--bangumi", "3995"])
    assert result.exit_code == 0


def test_subgroup_audit_scan(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    state = State(
        entries=[
            ManagedEntry(
                bangumi_id=3995,
                subgroup_id=203,
                bangumi_title="无职转生",
                subgroup_name="桜都字幕组",
                season="2026夏",
            )
        ]
    )
    _patch_subgroup(monkeypatch, support.FakeSource())
    monkeypatch.setattr("mikan_qb.commands.subgroup_cmd.load_state", lambda: state)
    result = runner.invoke(app, cli_base + ["subgroup", "audit", "--scan", "2026夏"])
    assert result.exit_code == 0
    assert "巡检" in result.output


def test_subgroup_audit_empty_state(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_subgroup(monkeypatch, support.FakeSource())
    monkeypatch.setattr("mikan_qb.commands.subgroup_cmd.load_state", lambda: State())
    result = runner.invoke(app, cli_base + ["subgroup", "audit"])
    assert result.exit_code == 0
    assert "托管清单为空" in result.output


def test_rule_preview_page_only(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    monkeypatch.setattr(
        "mikan_qb.commands.rule_cmd.build_source", lambda *a, **k: support.FakeSource()
    )
    result = runner.invoke(app, cli_base + ["rule", "preview", "3995", "--page-only"])
    assert result.exit_code == 0
    assert "规则离线模拟" in result.output


def test_rule_preview_single_subgroup(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    monkeypatch.setattr(
        "mikan_qb.commands.rule_cmd.build_source", lambda *a, **k: support.FakeSource()
    )
    result = runner.invoke(
        app, cli_base + ["rule", "preview", "3995", "--subgroup", "203", "--json"]
    )
    assert result.exit_code == 0


def _make_plan():
    """用夹具数据构建一份可用计划，供 apply 命令测试复用。"""
    return build_plan(
        AppConfig(),
        EnvSettings(_env_file=None),
        support.load_detail(),
        support.load_releases(),
        203,
        "normal",
    )


def _patch_apply(monkeypatch, client: support.FakeQBClient):
    """替换 apply_cmd 的计划解析、qB 客户端与状态读写，返回保存记录列表。"""
    plan = _make_plan()
    saved: list[State] = []
    monkeypatch.setattr("mikan_qb.commands.apply_cmd._resolve_plan", lambda *a, **k: plan)
    monkeypatch.setattr("mikan_qb.commands.apply_cmd._qb", lambda app_ctx: client)
    monkeypatch.setattr("mikan_qb.commands.apply_cmd.load_state", lambda: State())
    monkeypatch.setattr("mikan_qb.commands.apply_cmd.save_state", lambda state: saved.append(state))
    return plan, saved


def test_apply_add_dry_run(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    client = support.FakeQBClient()
    _patch_apply(monkeypatch, client)
    result = runner.invoke(app, cli_base + ["apply", "add", "--bangumi", "3995"])
    assert result.exit_code == 0
    assert "dry-run" in result.output
    assert not any(call[0] == "set_rule" for call in client.calls)


def test_apply_add_apply(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    client = support.FakeQBClient()
    plan, saved = _patch_apply(monkeypatch, client)
    result = runner.invoke(app, cli_base + ["apply", "add", "--bangumi", "3995", "--apply"])
    assert result.exit_code == 0
    assert ("set_rule", plan.rule_name) in client.calls
    assert ("add_feed", plan.feed_path) in client.calls
    assert len(saved) == 1
    assert saved[0].entries[0].bangumi_id == 3995


def test_apply_add_backfill_paused(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    client = support.FakeQBClient()
    _patch_apply(monkeypatch, client)
    result = runner.invoke(
        app, cli_base + ["apply", "add", "--bangumi", "3995", "--apply", "--backfill", "--paused"]
    )
    assert result.exit_code == 0
    assert any(call[0] == "add_torrents" and call[2] is True for call in client.calls)


def test_apply_backfill_dry_run(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    client = support.FakeQBClient()
    _patch_apply(monkeypatch, client)
    result = runner.invoke(
        app,
        cli_base + ["apply", "backfill", "--bangumi", "3995", "--from-ep", "2", "--to-ep", "5"],
    )
    assert result.exit_code == 0
    assert "dry-run" in result.output
    assert not any(call[0] == "add_torrents" for call in client.calls)


def test_apply_backfill_apply(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    client = support.FakeQBClient()
    _patch_apply(monkeypatch, client)
    result = runner.invoke(
        app,
        cli_base
        + ["apply", "backfill", "--bangumi", "3995", "--from-ep", "2", "--to-ep", "5", "--apply"],
    )
    assert result.exit_code == 0
    calls = [call for call in client.calls if call[0] == "add_torrents"]
    assert calls
    assert calls[0][2] is False


def test_apply_remove_dry_run(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    client = support.FakeQBClient(torrents=[support.FakeTorrent("t", "h")])
    _patch_apply(monkeypatch, client)
    result = runner.invoke(app, cli_base + ["apply", "remove", "--bangumi", "3995"])
    assert result.exit_code == 0
    assert "dry-run" in result.output
    assert not any(call[0] == "remove_rule" for call in client.calls)


def test_apply_remove_apply(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    client = support.FakeQBClient(torrents=[support.FakeTorrent("t", "h")])
    plan, _saved = _patch_apply(monkeypatch, client)
    result = runner.invoke(
        app,
        cli_base
        + ["apply", "remove", "--bangumi", "3995", "--apply", "--delete-files", "--keep-feed"],
    )
    assert result.exit_code == 0
    assert ("remove_rule", plan.rule_name) in client.calls
    assert ("delete_torrents", ("h",), True) in client.calls
    assert not any(call[0] == "remove_item" for call in client.calls)
