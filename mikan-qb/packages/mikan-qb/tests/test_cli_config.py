"""CLI config 子命令的离线测试：init / show / validate。"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from mikan_qb.cli import app


def test_config_init_writes_and_force_overwrites(
    runner: CliRunner, tmp_path: Path, monkeypatch
) -> None:
    """config init 写入模板；重复运行跳过，--force 时覆盖。"""
    env_example = tmp_path / ".env.example"
    monkeypatch.setattr("mikan_qb.commands.config_cmd.env_example_path", lambda: env_example)
    config_path = tmp_path / "config.toml"
    args = [
        "--config",
        str(config_path),
        "--env",
        str(tmp_path / "missing.env"),
        "config",
        "init",
    ]

    first = runner.invoke(app, args)
    assert first.exit_code == 0
    assert config_path.is_file()
    assert env_example.is_file()
    original = config_path.read_text(encoding="utf-8")

    config_path.write_text("stale", encoding="utf-8")
    skipped = runner.invoke(app, args)
    assert skipped.exit_code == 0
    assert config_path.read_text(encoding="utf-8") == "stale"

    forced = runner.invoke(app, args + ["--force"])
    assert forced.exit_code == 0
    assert config_path.read_text(encoding="utf-8") == original


def test_config_show_table_and_json(runner: CliRunner, cli_base: list[str]) -> None:
    """config show 的表格与 JSON 输出均可正常渲染。"""
    table = runner.invoke(app, cli_base + ["config", "show"])
    assert table.exit_code == 0
    assert "生效配置" in table.output

    as_json = runner.invoke(app, cli_base + ["config", "show", "--json"])
    assert as_json.exit_code == 0
    assert '"config_path"' in as_json.output


def test_config_validate_ok(runner: CliRunner, cli_base: list[str]) -> None:
    """包内默认配置应校验通过（exit 0）。"""
    result = runner.invoke(app, cli_base + ["config", "validate"])
    assert result.exit_code == 0
    assert "配置校验通过" in result.output


def test_config_validate_bad_base_url_exits_1(runner: CliRunner, tmp_path: Path) -> None:
    """base_url 缺少 scheme 的坏配置应以退出码 1 结束。"""
    bad = tmp_path / "bad.toml"
    bad.write_text('[mikan]\nbase_url = "mikanani.kas.pub"\n', encoding="utf-8")

    result = runner.invoke(
        app,
        ["--config", str(bad), "--env", str(tmp_path / "missing.env"), "config", "validate"],
    )
    assert result.exit_code == 1
    assert "base_url" in result.output
