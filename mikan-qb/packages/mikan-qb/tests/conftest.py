"""mikan-qb 测试共享夹具：CLI runner、全局参数与样例数据。"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import support
from mikan_health.data.models import BangumiDetail, Release
from mikan_qb.paths import default_config_path


@pytest.fixture
def runner() -> CliRunner:
    """Typer CLI 测试运行器。"""
    return CliRunner()


@pytest.fixture
def cli_base(tmp_path: Path) -> list[str]:
    """CLI 全局参数：用包内 config.toml 与一个不存在的 .env，保证离线可复现。"""
    return ["--config", str(default_config_path()), "--env", str(tmp_path / "missing.env")]


@pytest.fixture
def detail() -> BangumiDetail:
    """测试夹具解析出的番剧详情。"""
    return support.load_detail()


@pytest.fixture
def releases() -> list[Release]:
    """测试夹具解析出的某字幕组发布列表。"""
    return support.load_releases()
