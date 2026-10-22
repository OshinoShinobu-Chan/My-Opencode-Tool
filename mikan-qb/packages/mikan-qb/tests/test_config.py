from __future__ import annotations

import pytest

from mikan_qb.config import (
    AppConfig,
    ConfigError,
    EnvSettings,
    expand_template,
    load_config,
    validate_config,
)


def test_shipped_taxonomy_matches_defaults() -> None:
    from mikan_health.core.taxonomy import Taxonomy

    config, _ = load_config()
    shipped = config.taxonomy.model_dump()
    defaults = Taxonomy().model_dump()
    # subgroup_languages 是个人白名单，允许与库默认值不同
    shipped.pop("subgroup_languages")
    defaults.pop("subgroup_languages")
    assert shipped == defaults


def test_default_config_loads_and_validates() -> None:
    config, path = load_config()
    assert path.name == "config.toml"
    errors, warnings = validate_config(config, EnvSettings(_env_file=None))
    assert errors == []
    assert any("QB_HOST" in w for w in warnings)


def test_env_file_is_read(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "QB_HOST=http://example.invalid:8080\nQB_USERNAME=admin\nQB_PASSWORD=secret\n",
        encoding="utf-8",
    )
    settings = EnvSettings(_env_file=env_file)
    assert settings.has_credentials() is True
    assert settings.qb_host == "http://example.invalid:8080"


def test_unknown_template_placeholder_rejected() -> None:
    config = AppConfig.model_validate({"qb": {"save_path_template": "{root}/{nope}/{anime}"}})
    errors, _ = validate_config(config, EnvSettings(_env_file=None))
    assert any("未知占位符" in e for e in errors)


def test_save_path_without_anime_warns() -> None:
    config = AppConfig.model_validate({"qb": {"save_path_template": "{root}/{season}"}})
    errors, warnings = validate_config(config, EnvSettings(_env_file=None))
    assert errors == []
    assert any("{anime}" in w for w in warnings)


def test_save_path_with_anime_does_not_warn() -> None:
    config = AppConfig.model_validate({"qb": {"save_path_template": "{root}/{season}/{anime}"}})
    _, warnings = validate_config(config, EnvSettings(_env_file=None))
    assert not any("{anime}" in w for w in warnings)


def test_tag_without_bangumi_id_warns() -> None:
    config = AppConfig.model_validate({"qb": {"torrent_tag_template": "mikan"}})
    _, warnings = validate_config(config, EnvSettings(_env_file=None))
    assert any("bangumi_id" in w for w in warnings)


def test_default_save_path_is_flat_per_anime() -> None:
    config = AppConfig()
    assert config.qb.save_root == "/downloads/anime"
    errors, warnings = validate_config(config, EnvSettings(_env_file=None))
    assert errors == []
    assert not any("save_root" in w for w in warnings)
    assert not any("bangumi_id" in w for w in warnings)


def test_rule_name_and_tag_templates_add() -> None:
    config = AppConfig()
    merged = {
        "lang": "简体",
        "anime": "无职转生",
        "bangumi_id": 3995,
        "season": "2026夏",
        "subgroup": "樱都",
    }
    assert expand_template(config.qb.rule_name_template, **merged) == "简体/无职转生@樱都"
    assert expand_template(config.qb.torrent_tag_template, **merged) == "mikan-3995"


def test_bad_base_url_rejected() -> None:
    config = AppConfig.model_validate({"mikan": {"base_url": "mikanani.kas.pub"}})
    errors, _ = validate_config(config, EnvSettings(_env_file=None))
    assert any("base_url" in e for e in errors)


def test_empty_taxonomy_group_rejected() -> None:
    config = AppConfig.model_validate({"taxonomy": {"simplified": []}})
    errors, _ = validate_config(config, EnvSettings(_env_file=None))
    assert any("taxonomy.simplified" in e for e in errors)


def test_ambiguous_taxonomy_labels_rejected() -> None:
    config = AppConfig.model_validate(
        {"taxonomy": {"simplified": ["简体", "中"], "traditional": ["繁体", "中"]}}
    )
    errors, _ = validate_config(config, EnvSettings(_env_file=None))
    assert any("重叠" in e for e in errors)


def test_expand_template() -> None:
    expanded = expand_template(
        "{root}/{season}/{anime}", root="/media/番剧", season="2026夏", anime="无职转生"
    )
    assert expanded == "/media/番剧/2026夏/无职转生"


def test_expand_template_missing_key() -> None:
    with pytest.raises(ConfigError):
        expand_template("{root}/{season}", root="/media")


def test_missing_config_file_raises(tmp_path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")
