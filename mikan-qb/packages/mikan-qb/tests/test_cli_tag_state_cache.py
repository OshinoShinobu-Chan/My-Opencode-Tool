"""CLI tag / state / cache 子命令的离线测试。"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from mikan_health.data import HttpCache
from mikan_qb.cli import app
from mikan_qb.state import ManagedEntry, State

TITLE = "[组] 番 [01][1080p][简繁内封]"


def test_tag_parse_table_and_json(runner: CliRunner, cli_base: list[str]) -> None:
    """tag parse 的表格与 JSON 输出均可正常渲染。"""
    table = runner.invoke(app, cli_base + ["tag", "parse", TITLE])
    assert table.exit_code == 0
    assert "标题解析" in table.output

    as_json = runner.invoke(app, cli_base + ["tag", "parse", TITLE, "--json"])
    assert as_json.exit_code == 0
    assert '"raw"' in as_json.output


def _sample_state() -> State:
    """构造含 1 条托管记录的 State。"""
    return State(
        entries=[
            ManagedEntry(
                bangumi_id=3995,
                subgroup_id=203,
                bangumi_title="无职转生",
                subgroup_name="桜都字幕组",
                season="2026夏",
                mode="normal",
                tag="mikan-3995",
            )
        ]
    )


def test_state_list_with_entry_and_empty(
    runner: CliRunner, cli_base: list[str], monkeypatch
) -> None:
    """state list 覆盖有记录与空清单两个分支。"""
    monkeypatch.setattr("mikan_qb.commands.state_cmd.load_state", _sample_state)
    listed = runner.invoke(app, cli_base + ["state", "list"])
    assert listed.exit_code == 0
    assert "无职转生" in listed.output

    monkeypatch.setattr("mikan_qb.commands.state_cmd.load_state", State)
    empty = runner.invoke(app, cli_base + ["state", "list"])
    assert empty.exit_code == 0
    assert "托管清单为空" in empty.output


def test_cache_stats_and_clear(
    runner: CliRunner, cli_base: list[str], tmp_path: Path, monkeypatch
) -> None:
    """cache stats 统计真实写入的条目；clear 支持全清与按时间清理。"""
    db = tmp_path / "cache.sqlite3"
    monkeypatch.setattr("mikan_qb.commands.cache_cmd.cache_path", lambda: db)

    HttpCache(db).put("https://example.invalid/a", b"hello", 200)

    as_json = runner.invoke(app, cli_base + ["cache", "stats", "--json"])
    assert as_json.exit_code == 0
    assert '"entries": 1' in as_json.output

    table = runner.invoke(app, cli_base + ["cache", "stats"])
    assert table.exit_code == 0
    assert "HTTP 缓存" in table.output

    cleared = runner.invoke(app, cli_base + ["cache", "clear", "--json"])
    assert cleared.exit_code == 0
    assert '"removed": 1' in cleared.output

    HttpCache(db).put("https://example.invalid/b", b"world", 200)
    recent = runner.invoke(app, cli_base + ["cache", "clear", "--older-than", "1", "--json"])
    assert recent.exit_code == 0
    assert '"removed": 0' in recent.output
