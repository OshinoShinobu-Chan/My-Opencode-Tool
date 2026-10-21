"""config.py 加载、校验、模板展开与有效地址的补充单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from mikan_qb.config import (
    AppConfig,
    ConfigError,
    EnvSettings,
    effective_mikan_base,
    env_example_path,
    expand_template,
    load_config,
    load_env,
    validate_config,
)
from mikan_qb.paths import repo_root


def _validate(config: AppConfig, **env_kwargs) -> tuple[list[str], list[str]]:
    """用无 .env 的 EnvSettings 校验配置，可覆盖任意环境字段。"""
    return validate_config(config, EnvSettings(_env_file=None, **env_kwargs))


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")


def test_load_config_invalid_value_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[mikan]\nrate_limit_per_sec = "fast"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_env_missing_returns_none_path(tmp_path: Path) -> None:
    settings, path = load_env(tmp_path / "nope.env")
    assert path is None
    assert settings.qb_host == ""


def test_load_env_reads_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("QB_HOST=http://qb.local:8080\n", encoding="utf-8")
    settings, path = load_env(env_file)
    assert path == env_file
    assert settings.qb_host == "http://qb.local:8080"


def test_bad_base_url_error() -> None:
    errors, _ = _validate(AppConfig.model_validate({"mikan": {"base_url": "mikanani.kas.pub"}}))
    assert any("base_url" in e for e in errors)


def test_rate_limit_non_positive_error() -> None:
    errors, _ = _validate(AppConfig.model_validate({"mikan": {"rate_limit_per_sec": 0}}))
    assert any("rate_limit_per_sec" in e for e in errors)


def test_cache_ttl_negative_error() -> None:
    errors, _ = _validate(AppConfig.model_validate({"mikan": {"cache_ttl_hours": -1}}))
    assert any("cache_ttl_hours" in e for e in errors)


def test_timeout_non_positive_error() -> None:
    errors, _ = _validate(AppConfig.model_validate({"mikan": {"timeout_seconds": 0}}))
    assert any("timeout_seconds" in e for e in errors)


def test_unknown_placeholder_error() -> None:
    config = AppConfig.model_validate({"qb": {"rule_name_template": "{nope}/{anime}"}})
    errors, _ = _validate(config)
    assert any("未知占位符" in e for e in errors)


def test_save_path_without_anime_warns() -> None:
    config = AppConfig.model_validate({"qb": {"save_path_template": "{root}/{season}"}})
    errors, warnings = _validate(config)
    assert errors == []
    assert any("{anime}" in w for w in warnings)


def test_empty_save_root_warns() -> None:
    config = AppConfig.model_validate({"qb": {"save_root": ""}})
    _, warnings = _validate(config)
    assert any("save_root" in w for w in warnings)


def test_tag_without_bangumi_id_warns() -> None:
    config = AppConfig.model_validate({"qb": {"torrent_tag_template": "mikan"}})
    _, warnings = _validate(config)
    assert any("bangumi_id" in w for w in warnings)


def test_refresh_interval_non_positive_error() -> None:
    config = AppConfig.model_validate({"qb": {"refresh_interval_minutes": 0}})
    errors, _ = _validate(config)
    assert any("refresh_interval_minutes" in e for e in errors)


@pytest.mark.parametrize(
    "group",
    ["simplified", "traditional", "japanese", "dual_zh_jp", "softsub", "hardsub"],
)
def test_empty_taxonomy_group_error(group: str) -> None:
    config = AppConfig.model_validate({"taxonomy": {group: []}})
    errors, _ = _validate(config)
    assert any(f"taxonomy.{group}" in e for e in errors)


def test_taxonomy_overlap_error() -> None:
    config = AppConfig.model_validate(
        {"taxonomy": {"simplified": ["简体", "中"], "traditional": ["繁体", "中"]}}
    )
    errors, _ = _validate(config)
    assert any("重叠" in e for e in errors)


def test_taxonomy_single_char_labels_not_overlap() -> None:
    config = AppConfig.model_validate(
        {"taxonomy": {"simplified": ["简体", "简"], "traditional": ["繁体", "繁"]}}
    )
    errors, _ = _validate(config)
    assert not any("重叠" in e for e in errors)


def test_missing_qb_host_warns() -> None:
    _, warnings = _validate(AppConfig())
    assert any("QB_HOST" in w for w in warnings)


def test_missing_bgm_token_warns() -> None:
    _, warnings = _validate(AppConfig())
    assert any("BGM_TOKEN" in w for w in warnings)


def test_token_file_suppresses_bgm_warning() -> None:
    config = AppConfig.model_validate({"bangumi": {"token_file": "/tmp/token.txt"}})
    _, warnings = _validate(config)
    assert not any("BGM_TOKEN" in w for w in warnings)


def test_mikan_base_mismatch_warns() -> None:
    _, warnings = _validate(AppConfig(), mikan_base="https://other.example")
    assert any("不一致" in w for w in warnings)


def test_mikan_base_same_does_not_warn() -> None:
    config = AppConfig()
    _, warnings = _validate(config, mikan_base=config.mikan.base_url + "/")
    assert not any("不一致" in w for w in warnings)


def test_effective_mikan_base_prefers_env() -> None:
    config = AppConfig()
    env = EnvSettings(_env_file=None, mikan_base="https://env.example/")
    assert effective_mikan_base(config, env) == "https://env.example"


def test_effective_mikan_base_falls_back_to_config() -> None:
    config = AppConfig()
    env = EnvSettings(_env_file=None, mikan_base="")
    assert effective_mikan_base(config, env) == config.mikan.base_url


def test_expand_template_success() -> None:
    expanded = expand_template("{root}/{anime}", root="/media", anime="无职转生")
    assert expanded == "/media/无职转生"


def test_expand_template_missing_key_raises() -> None:
    with pytest.raises(ConfigError):
        expand_template("{root}/{season}", root="/media")


def test_env_example_path() -> None:
    path = env_example_path()
    assert path.name == ".env.example"
    assert path.parent == repo_root()
