"""AppContext 构建、json_output 与 ConfigError 分支的单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from mikan_qb.context import ConfigError, GlobalOptions, build_context
from mikan_qb.paths import default_config_path


def test_build_context_with_defaults(tmp_path: Path) -> None:
    options = GlobalOptions(config=default_config_path(), env=tmp_path / "missing.env")
    ctx = build_context(options)
    assert ctx.config_path == default_config_path()
    assert ctx.env_path is None
    assert ctx.json_output is False
    assert ctx.options is options


def test_build_context_reads_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / "custom.env"
    env_file.write_text("QB_HOST=http://qb.local:8080\n", encoding="utf-8")
    options = GlobalOptions(config=default_config_path(), env=env_file)
    ctx = build_context(options)
    assert ctx.env_path == env_file
    assert ctx.env.qb_host == "http://qb.local:8080"


def test_json_output_flag(tmp_path: Path) -> None:
    options = GlobalOptions(
        config=default_config_path(), env=tmp_path / "missing.env", json_output=True
    )
    assert build_context(options).json_output is True


def test_missing_config_raises_config_error(tmp_path: Path) -> None:
    options = GlobalOptions(config=tmp_path / "nope.toml", env=tmp_path / "missing.env")
    with pytest.raises(ConfigError):
        build_context(options)
