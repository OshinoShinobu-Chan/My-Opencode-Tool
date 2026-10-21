"""CLI 全局选项与运行时上下文（配置 + 环境 + 路径）的构建。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mikan_qb.config import AppConfig, ConfigError, EnvSettings, load_config, load_env


@dataclass
class GlobalOptions:
    """命令行全局选项容器。

    Attributes:
        config: config.toml 路径覆盖；None 表示自动查找。
        env: .env 路径覆盖；None 表示自动查找。
        json_output: 是否以 JSON 输出。
        verbose: 是否输出调试信息。
    """

    config: Path | None = None
    env: Path | None = None
    json_output: bool = False
    verbose: bool = False


@dataclass
class AppContext:
    """一次命令执行所需的配置、环境与路径集合。

    Attributes:
        config: 生效的应用配置。
        env: 生效的环境凭据。
        config_path: 实际使用的 config.toml 路径。
        env_path: 实际使用的 .env 路径；未找到为 None。
        options: 原始全局选项。
    """

    config: AppConfig
    env: EnvSettings
    config_path: Path
    env_path: Path | None
    options: GlobalOptions

    @property
    def json_output(self) -> bool:
        """是否以 JSON 输出。"""
        return self.options.json_output


def build_context(options: GlobalOptions) -> AppContext:
    """按全局选项加载配置与环境，组装 AppContext。

    Args:
        options: 命令行全局选项，含配置 / 环境路径覆盖。
    """
    config, config_path = load_config(options.config)
    env, env_path = load_env(options.env)
    return AppContext(
        config=config,
        env=env,
        config_path=config_path,
        env_path=env_path,
        options=options,
    )


__all__ = ["AppContext", "ConfigError", "GlobalOptions", "build_context"]
