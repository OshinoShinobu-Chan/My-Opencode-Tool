"""CLI plan 子命令的离线测试：show / diff / add 与错误分支。"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import support
from mikan_qb.cli import app
from mikan_qb.config import AppConfig, EnvSettings
from mikan_qb.rules import Plan, build_plan, save_plan

BANGUMI_ID = 3995
SAKURATO_SID = 203


def _plan() -> Plan:
    """用测试夹具构建一份真实的 203 字幕组计划。"""
    return build_plan(
        AppConfig(),
        EnvSettings(_env_file=None),
        support.load_detail(),
        support.load_releases(),
        SAKURATO_SID,
        "normal",
    )


def _write_plan(tmp_path: Path) -> Path:
    """把真实计划写入 tmp 并返回路径。"""
    path = tmp_path / "plan.json"
    save_plan(_plan(), path)
    return path


def test_plan_show_table_and_json(runner: CliRunner, cli_base: list[str], tmp_path: Path) -> None:
    """plan show 从文件读取计划的表格与 JSON 输出。"""
    path = _write_plan(tmp_path)

    table = runner.invoke(app, cli_base + ["plan", "show", str(path)])
    assert table.exit_code == 0
    assert "计划" in table.output

    as_json = runner.invoke(app, cli_base + ["plan", "show", str(path), "--json"])
    assert as_json.exit_code == 0
    assert f'"bangumi_id": {BANGUMI_ID}' in as_json.output


def test_plan_diff_with_and_without_changes(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch
) -> None:
    """plan diff 对空 qB 报告 create，对完全一致的 qB 报告 unchanged。"""
    plan = _plan()
    path = tmp_path / "plan.json"
    save_plan(plan, path)

    monkeypatch.setattr("mikan_qb.commands.plan_cmd._qb", lambda app_ctx: support.FakeQBClient())
    created = runner.invoke(app, cli_base + ["plan", "diff", str(path)])
    assert created.exit_code == 0
    assert "create" in created.output

    same_qb = support.FakeQBClient(
        feeds=[{"path": plan.feed_path, "url": plan.feed_url}],
        rules={plan.rule_name: plan.rule_def},
    )
    monkeypatch.setattr("mikan_qb.commands.plan_cmd._qb", lambda app_ctx: same_qb)
    unchanged = runner.invoke(app, cli_base + ["plan", "diff", str(path)])
    assert unchanged.exit_code == 0
    assert "unchanged" in unchanged.output


def test_plan_add_success(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch
) -> None:
    """plan add 成功构建计划并把保存路径写进输出。"""
    target = tmp_path / "added.json"
    monkeypatch.setattr(
        "mikan_qb.commands.plan_cmd.build_source", lambda *a, **k: support.FakeSource()
    )
    monkeypatch.setattr("mikan_qb.commands.plan_cmd._bangumi", lambda *a, **k: None)
    monkeypatch.setattr("mikan_qb.commands.plan_cmd.save_plan", lambda plan: target)

    result = runner.invoke(
        app, cli_base + ["plan", "add", str(BANGUMI_ID), "--subgroup", str(SAKURATO_SID)]
    )
    assert result.exit_code == 0
    assert target.name in result.output


def test_plan_add_unsolvable_subgroup_exits_3(
    runner: CliRunner, cli_base: list[str], monkeypatch
) -> None:
    """无解字幕组使 build_plan 抛 PlanError，命令以退出码 3 结束。"""
    monkeypatch.setattr(
        "mikan_qb.commands.plan_cmd.build_source", lambda *a, **k: support.FakeSource()
    )
    monkeypatch.setattr("mikan_qb.commands.plan_cmd._bangumi", lambda *a, **k: None)

    result = runner.invoke(app, cli_base + ["plan", "add", str(BANGUMI_ID), "--subgroup", "999999"])
    assert result.exit_code == 3


def test_plan_show_requires_path_or_bangumi(runner: CliRunner, cli_base: list[str]) -> None:
    """既不传路径也不传 --bangumi 时以退出码 1 结束。"""
    result = runner.invoke(app, cli_base + ["plan", "show"])
    assert result.exit_code == 1


def test_plan_show_missing_file_exits_1(
    runner: CliRunner, cli_base: list[str], tmp_path: Path
) -> None:
    """计划文件不存在时以退出码 1 结束。"""
    result = runner.invoke(app, cli_base + ["plan", "show", str(tmp_path / "nope.json")])
    assert result.exit_code == 1
