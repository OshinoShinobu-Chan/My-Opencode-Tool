"""qb 子命令的命令行测试：连接检查、feed 浏览与 RSS 规则管理。"""

from __future__ import annotations

import json

from typer.testing import CliRunner

import support
from mikan_qb.cli import app
from mikan_qb.qb import QBError

RULE_NAME = "简繁/无职转生"
RULE = {
    "enabled": True,
    "affectedFeeds": [],
    "mustContain": "x",
    "mustNotContain": "",
    "savePath": "/x",
}
RSS_TREE = {
    "2026夏": {
        "children": {
            "无职转生@桜都字幕组": {
                "url": "https://mikan.invalid/RSS/Bangumi?bangumiId=3995&subgroupid=203",
                "refreshInterval": 30,
            }
        }
    }
}


def _patch_client(monkeypatch, client: support.FakeQBClient) -> None:
    """把 qb_cmd 内的 QBClient 替换为返回给定假客户端的工厂。"""
    monkeypatch.setattr("mikan_qb.commands.qb_cmd.QBClient", lambda *a, **k: client)


def _patch_qb(monkeypatch, client: support.FakeQBClient) -> None:
    """把 qb_cmd 内的 _qb 替换为返回给定假客户端。"""
    monkeypatch.setattr("mikan_qb.commands.qb_cmd._qb", lambda app_ctx: client)


def test_qb_check_renders_status(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_client(monkeypatch, support.FakeQBClient())
    result = runner.invoke(app, cli_base + ["qb", "check"])
    assert result.exit_code == 0
    assert "5.0.0" in result.output
    assert "http://qb.local:8080" in result.output


def test_qb_check_json_hides_global_by_default(
    runner: CliRunner, cli_base: list[str], monkeypatch
) -> None:
    _patch_client(monkeypatch, support.FakeQBClient())
    result = runner.invoke(app, cli_base + ["qb", "check", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["host"] == "http://qb.local:8080"
    assert payload["rss_settings"] == {}


def test_qb_check_show_global(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_client(monkeypatch, support.FakeQBClient(repack=True))
    result = runner.invoke(app, cli_base + ["qb", "check", "--show-global", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["rss_settings"] == {"rss_download_repack_proper_episodes": True}
    table = runner.invoke(app, cli_base + ["qb", "check", "--show-global"])
    assert table.exit_code == 0
    assert "rss_download_repack_proper_episodes" in table.output


def test_qb_check_remote_error(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    def _boom(env: object) -> None:
        raise QBError("无法连接 qBittorrent", hint="检查 QB_HOST")

    monkeypatch.setattr("mikan_qb.commands.qb_cmd.QBClient", _boom)
    result = runner.invoke(app, cli_base + ["qb", "check"])
    assert result.exit_code == 2
    assert "无法连接" in result.output
    assert "检查 QB_HOST" in result.output


def test_qb_check_remote_error_json(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    def _boom(env: object) -> None:
        raise QBError("无法连接 qBittorrent", hint="检查 QB_HOST")

    monkeypatch.setattr("mikan_qb.commands.qb_cmd.QBClient", _boom)
    result = runner.invoke(app, cli_base + ["qb", "check", "--json"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["hint"] == "检查 QB_HOST"


def test_qb_feeds_with_data(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_client(monkeypatch, support.FakeQBClient(raw_items=RSS_TREE))
    result = runner.invoke(app, cli_base + ["qb", "feeds", "--with-data"])
    assert result.exit_code == 0
    assert "2026夏" in result.output
    assert "1 个 feed" in result.output


def test_qb_feeds_empty(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_client(monkeypatch, support.FakeQBClient())
    result = runner.invoke(app, cli_base + ["qb", "feeds"])
    assert result.exit_code == 0
    assert "暂无 feed" in result.output


def test_qb_rules_list_with_rules(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_qb(monkeypatch, support.FakeQBClient(rules={RULE_NAME: RULE}))
    result = runner.invoke(app, cli_base + ["qb", "rules", "list"])
    assert result.exit_code == 0
    assert RULE_NAME in result.output


def test_qb_rules_list_empty(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_qb(monkeypatch, support.FakeQBClient())
    result = runner.invoke(app, cli_base + ["qb", "rules", "list"])
    assert result.exit_code == 0
    assert "暂无规则" in result.output


def test_qb_rules_show_existing(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_qb(monkeypatch, support.FakeQBClient(rules={RULE_NAME: RULE}))
    result = runner.invoke(app, cli_base + ["qb", "rules", "show", RULE_NAME])
    assert result.exit_code == 0
    assert "/x" in result.output


def test_qb_rules_show_missing(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_qb(monkeypatch, support.FakeQBClient(rules={RULE_NAME: RULE}))
    result = runner.invoke(app, cli_base + ["qb", "rules", "show", "nope"])
    assert result.exit_code == 1
    assert "规则不存在" in result.output


def test_qb_rules_remove_dry_run(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    client = support.FakeQBClient(rules={RULE_NAME: RULE})
    _patch_qb(monkeypatch, client)
    result = runner.invoke(app, cli_base + ["qb", "rules", "remove", RULE_NAME])
    assert result.exit_code == 0
    assert "dry-run" in result.output
    assert ("remove_rule", RULE_NAME) not in client.calls


def test_qb_rules_remove_apply(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    client = support.FakeQBClient(rules={RULE_NAME: RULE})
    _patch_qb(monkeypatch, client)
    result = runner.invoke(app, cli_base + ["qb", "rules", "remove", RULE_NAME, "--apply"])
    assert result.exit_code == 0
    assert "已删除规则" in result.output
    assert ("remove_rule", RULE_NAME) in client.calls


def test_qb_rules_remove_missing(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_qb(monkeypatch, support.FakeQBClient(rules={RULE_NAME: RULE}))
    result = runner.invoke(app, cli_base + ["qb", "rules", "remove", "nope"])
    assert result.exit_code == 1


def test_qb_rules_repack_proper_already(
    runner: CliRunner, cli_base: list[str], monkeypatch
) -> None:
    client = support.FakeQBClient(repack=True)
    _patch_qb(monkeypatch, client)
    result = runner.invoke(app, cli_base + ["qb", "rules", "repack-proper", "--set", "on"])
    assert result.exit_code == 0
    assert "已是" in result.output
    assert not any(call[0] == "set_preferences" for call in client.calls)


def test_qb_rules_repack_proper_dry_run(
    runner: CliRunner, cli_base: list[str], monkeypatch
) -> None:
    client = support.FakeQBClient(repack=False)
    _patch_qb(monkeypatch, client)
    result = runner.invoke(app, cli_base + ["qb", "rules", "repack-proper", "--set", "on"])
    assert result.exit_code == 0
    assert "dry-run" in result.output
    assert not any(call[0] == "set_preferences" for call in client.calls)


def test_qb_rules_repack_proper_apply(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    client = support.FakeQBClient(repack=False)
    _patch_qb(monkeypatch, client)
    result = runner.invoke(
        app, cli_base + ["qb", "rules", "repack-proper", "--set", "on", "--apply"]
    )
    assert result.exit_code == 0
    assert "已设置" in result.output
    assert ("set_preferences", {"rss_download_repack_proper_episodes": True}) in client.calls


def test_qb_rules_match(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    matched = {"2026夏/无职转生@桜都字幕组": ["[桜都字幕组] 无职转生 [01][1080p][简繁内封]"]}
    _patch_qb(monkeypatch, support.FakeQBClient(rules={RULE_NAME: RULE}, matched=matched))
    result = runner.invoke(app, cli_base + ["qb", "rules", "match", RULE_NAME])
    assert result.exit_code == 0
    assert "当前命中 1 条" in result.output
    assert "无职转生" in result.output


def test_qb_rules_match_missing(runner: CliRunner, cli_base: list[str], monkeypatch) -> None:
    _patch_qb(monkeypatch, support.FakeQBClient(rules={RULE_NAME: RULE}))
    result = runner.invoke(app, cli_base + ["qb", "rules", "match", "nope"])
    assert result.exit_code == 1
