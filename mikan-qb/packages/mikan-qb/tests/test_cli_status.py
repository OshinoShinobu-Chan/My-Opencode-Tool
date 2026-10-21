"""CLI status 命令的离线测试：托管清单与 qB feed/规则状态总览。"""

from __future__ import annotations

import json

from typer.testing import CliRunner

import support
from mikan_qb.cli import app
from mikan_qb.state import ManagedEntry, State

BANGUMI_ID = 3995
SAKURATO_SID = 203
FEED_URL = "https://mikan/RSS/Bangumi?bangumiId=3995&subgroupid=203"
RULE_NAME = "简繁/无职转生@桜都字幕组"


def _entry(season: str = "2026夏", subgroup_id: int = SAKURATO_SID) -> ManagedEntry:
    """构造一条与 qB 侧 URL / 规则名匹配的托管记录。"""
    return ManagedEntry(
        bangumi_id=BANGUMI_ID,
        subgroup_id=subgroup_id,
        bangumi_title="无职转生 第三季",
        subgroup_name="桜都字幕组",
        season=season,
        mode="normal",
        feed_path=f"{season}/无职转生@桜都字幕组",
        feed_url=FEED_URL,
        feed_created=True,
        rule_name=RULE_NAME,
        tag="mikan-3995",
    )


def _feed() -> dict:
    """构造 qB feed 行。"""
    return {"path": "2026夏/无职转生@桜都字幕组", "url": FEED_URL, "type": "feed"}


def _patch(monkeypatch, client, state: State | None = None) -> None:
    """替换 status_cmd 的数据源 / qB / 状态读写。"""
    monkeypatch.setattr(
        "mikan_qb.commands.status_cmd.build_source", lambda *a, **k: support.FakeSource()
    )
    monkeypatch.setattr("mikan_qb.commands.status_cmd._qb", lambda app_ctx: client)
    monkeypatch.setattr("mikan_qb.commands.status_cmd._bangumi", lambda *a, **k: None)
    st = state if state is not None else State(entries=[_entry()])
    monkeypatch.setattr("mikan_qb.commands.status_cmd.load_state", lambda: st)


def test_status_ok(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    client = support.FakeQBClient(
        feeds=[_feed()],
        rules={RULE_NAME: {"enabled": True, "lastMatch": "2026-01-01T12:00:00"}},
    )
    _patch(monkeypatch, client)
    result = runner.invoke(app, cli_base + ["status"])
    assert result.exit_code == 0
    assert "订阅状态" in result.output
    assert "2026-01-01 12" in result.output


def test_status_json_and_derived_fields(
    runner: CliRunner, cli_base: list[str], monkeypatch
) -> None:
    client = support.FakeQBClient(
        feeds=[_feed()],
        rules={RULE_NAME: {"enabled": True, "lastMatch": ""}},
    )
    _patch(monkeypatch, client)
    result = runner.invoke(app, cli_base + ["status", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert len(payload["entries"]) == 1
    row = payload["entries"][0]
    assert row["feed_ok"] is True
    assert row["rule_ok"] is True
    assert row["rule_enabled"] is True
    assert row["bangumi_id"] == BANGUMI_ID
    assert "stale_days" in row
    assert "missing_vs_peer" in row


def test_status_missing_rule(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    client = support.FakeQBClient(feeds=[_feed()], rules={})
    _patch(monkeypatch, client)
    result = runner.invoke(app, cli_base + ["status", "--json"])
    payload = json.loads(result.output)
    assert payload["entries"][0]["status"] == "规则缺失"


def test_status_disabled_rule(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    client = support.FakeQBClient(feeds=[_feed()], rules={RULE_NAME: {"enabled": False}})
    _patch(monkeypatch, client)
    result = runner.invoke(app, cli_base + ["status", "--json"])
    payload = json.loads(result.output)
    assert payload["entries"][0]["rule_enabled"] is False
    assert payload["entries"][0]["status"] == "规则禁用"


def test_status_missing_feed(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    client = support.FakeQBClient(feeds=[], rules={RULE_NAME: {"enabled": True}})
    _patch(monkeypatch, client)
    result = runner.invoke(app, cli_base + ["status", "--json"])
    payload = json.loads(result.output)
    assert payload["entries"][0]["status"] == "feed 缺失"


def test_status_missing_subscription(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    client = support.FakeQBClient(feeds=[], rules={})
    _patch(monkeypatch, client)
    result = runner.invoke(app, cli_base + ["status", "--json"])
    payload = json.loads(result.output)
    assert payload["entries"][0]["status"] == "订阅缺失"


def test_status_season_filter_excludes(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    client = support.FakeQBClient(feeds=[_feed()], rules={RULE_NAME: {"enabled": True}})
    _patch(monkeypatch, client, state=State(entries=[_entry(season="2025冬")]))
    result = runner.invoke(app, cli_base + ["status", "--season", "2026夏"])
    assert result.exit_code == 0
    assert "没有可显示的托管项" in result.output


def test_status_season_filter_keeps(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    client = support.FakeQBClient(feeds=[_feed()], rules={RULE_NAME: {"enabled": True}})
    _patch(monkeypatch, client, state=State(entries=[_entry(season="2026夏")]))
    result = runner.invoke(app, cli_base + ["status", "--season", "2026夏", "--json"])
    payload = json.loads(result.output)
    assert len(payload["entries"]) == 1


def test_status_empty_state(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch(monkeypatch, support.FakeQBClient(), state=State())
    result = runner.invoke(app, cli_base + ["status"])
    assert result.exit_code == 0
    assert "没有可显示的托管项" in result.output
