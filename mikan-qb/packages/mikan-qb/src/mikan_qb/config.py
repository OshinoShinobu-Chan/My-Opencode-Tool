"""应用配置（config.toml）与环境变量（.env）的加载、校验及模板展开。"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from mikan_health.core.taxonomy import Taxonomy
from mikan_qb.paths import default_env_path, find_config_path, find_env_path

TEMPLATE_PLACEHOLDERS: dict[str, set[str]] = {
    "season_folder_template": {"year", "season", "season_en"},
    "save_path_template": {"root", "season", "anime", "subgroup"},
    "feed_folder_template": {"season", "root"},
    "feed_name_template": {"anime", "subgroup", "season", "lang", "bangumi_id"},
    "rule_name_template": {"anime", "subgroup", "season", "lang", "bangumi_id"},
    "torrent_tag_template": {"anime", "subgroup", "season", "lang", "bangumi_id"},
}


class MikanConfig(BaseModel):
    """Mikan 站点抓取参数（地址 / 限速 / 缓存 / 超时 / 重试）。

    Attributes:
        base_url: Mikan 站点地址。
        rate_limit_per_sec: 每秒最大请求数。
        cache_ttl_hours: HTTP 缓存有效期（小时）。
        timeout_seconds: 单请求超时（秒）。
        max_retries: 失败重试次数。
    """

    base_url: str = "https://mikanani.kas.pub"
    rate_limit_per_sec: float = 1.0
    cache_ttl_hours: float = 6.0
    timeout_seconds: float = 20.0
    max_retries: int = 3


class QBConfig(BaseModel):
    """qBittorrent 写入参数：分类、标签、保存路径与命名模板。

    Attributes:
        category: qB 分类名。
        tags: 附加到规则 / 种子的标签列表。
        save_root: 保存根目录。
        season_folder_template: 季度目录名模板（预留，当前未接线）。
        save_path_template: 保存路径模板，支持 {root}/{season}/{anime}/{subgroup}。
        feed_folder_template: RSS 目录名模板。
        feed_name_template: feed 名模板。
        rule_name_template: 规则名模板（含 {subgroup}，使换组时新旧规则名不冲突）。
        torrent_tag_template: 种子标签模板。
        add_paused: 回填种子是否暂停（预留，当前未接线）。
        refresh_interval_minutes: feed 刷新间隔（分钟）。
        download_repack_proper: 是否自动下载 repack/proper（预留，当前未接线）。
    """

    category: str = "番剧"
    tags: list[str] = Field(default_factory=list)
    save_root: str = "/downloads/anime"
    season_folder_template: str = "{year}{season}"
    save_path_template: str = "{root}/{anime}"
    feed_folder_template: str = "{season}"
    feed_name_template: str = "{anime}@{subgroup}"
    rule_name_template: str = "{lang}/{anime}@{subgroup}"
    torrent_tag_template: str = "mikan-{bangumi_id}"
    add_paused: bool = False
    refresh_interval_minutes: int = 30
    download_repack_proper: bool = False


class AnalysisRef(BaseModel):
    """选用的健康度分析 profile 及其自定义目录。

    Attributes:
        profile: 选用的分析 profile 名称。
        profiles_dir: 自定义 profile 目录；空串表示用内置目录。
    """

    profile: str = "personal-normal"
    profiles_dir: str = ""


class BangumiConfig(BaseModel):
    """Bangumi API 客户端参数（地址、缓存、限速、token）。

    Attributes:
        enabled: 是否启用 Bangumi 精确放送日程。
        base_url: Bangumi API 地址。
        cache_ttl_hours: 缓存有效期（小时）。
        rate_limit_per_sec: 每秒最大请求数。
        timeout_seconds: 单请求超时（秒）。
        token_file: 存放 token 的文件路径；空串表示从环境变量读取。
    """

    enabled: bool = True
    base_url: str = "https://api.bgm.tv/v0"
    cache_ttl_hours: float = 12.0
    rate_limit_per_sec: float = 1.0
    timeout_seconds: float = 20.0
    token_file: str = ""


class AppConfig(BaseModel):
    """完整应用配置：聚合 mikan / qb / bangumi / taxonomy / analysis 各段。

    Attributes:
        mikan: Mikan 抓取参数。
        qb: qBittorrent 写入参数。
        bangumi: Bangumi 参数。
        taxonomy: 标题标签词典。
        analysis: 分析 profile 引用。
    """

    mikan: MikanConfig = Field(default_factory=MikanConfig)
    qb: QBConfig = Field(default_factory=QBConfig)
    bangumi: BangumiConfig = Field(default_factory=BangumiConfig)
    taxonomy: Taxonomy = Field(default_factory=Taxonomy)
    analysis: AnalysisRef = Field(default_factory=AnalysisRef)


class EnvSettings(BaseSettings):
    """从 .env 读取的敏感凭据与环境覆盖项。

    Attributes:
        qb_host: qB WebUI 地址。
        qb_username: qB 用户名。
        qb_password: qB 密码。
        mikan_base: Mikan 地址（覆盖 config.mikan.base_url）。
        bgm_token: Bangumi API token。
        bgm_proxy: Bangumi 代理地址。
    """

    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    qb_host: str = ""
    qb_username: str = ""
    qb_password: str = ""
    mikan_base: str = ""
    bgm_token: str = ""
    bgm_proxy: str = ""

    def has_credentials(self) -> bool:
        """三项 qB 凭据是否齐全。"""
        return bool(self.qb_host and self.qb_username and self.qb_password)


class ConfigError(Exception):
    """配置缺失或校验失败时抛出。"""


def load_config(path: Path | None = None) -> tuple[AppConfig, Path]:
    """读取并校验 config.toml，返回 (AppConfig, 实际路径)；失败时抛 ConfigError。

    Args:
        path: config.toml 路径；None 时按查找规则自动定位。
    """
    resolved = find_config_path(path)
    if not resolved.is_file():
        raise ConfigError(f"找不到配置文件：{resolved}（可先运行 `mikan config init`）")
    with resolved.open("rb") as fh:
        raw: dict[str, Any] = tomllib.load(fh)
    try:
        return AppConfig.model_validate(raw), resolved
    except ValidationError as exc:
        raise ConfigError(f"配置文件校验失败：{resolved}\n{exc}") from exc


def load_env(path: Path | None = None) -> tuple[EnvSettings, Path | None]:
    """读取 .env 设置，返回 (EnvSettings, 文件路径)；文件不存在时路径为 None。

    Args:
        path: .env 路径；None 时按查找规则自动定位。
    """
    resolved = find_env_path(path)
    env_file = resolved if resolved.is_file() else None
    settings = EnvSettings(_env_file=env_file)  # type: ignore[call-arg]
    return settings, env_file


def validate_config(config: AppConfig, env: EnvSettings) -> tuple[list[str], list[str]]:
    """校验配置与环境变量，返回 (errors, warnings) 两个字符串列表。

    Args:
        config: 待校验的应用配置。
        env: 待校验的环境凭据。
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not config.mikan.base_url.startswith(("http://", "https://")):
        errors.append(f"mikan.base_url 必须以 http(s):// 开头：{config.mikan.base_url}")
    if config.mikan.rate_limit_per_sec <= 0:
        errors.append("mikan.rate_limit_per_sec 必须大于 0")
    if config.mikan.cache_ttl_hours < 0:
        errors.append("mikan.cache_ttl_hours 不能为负")
    if config.mikan.timeout_seconds <= 0:
        errors.append("mikan.timeout_seconds 必须大于 0")

    for field_name, allowed in TEMPLATE_PLACEHOLDERS.items():
        template = getattr(config.qb, field_name)
        used = _placeholders(template)
        unknown = used - allowed
        if unknown:
            errors.append(
                f"qb.{field_name} 含未知占位符 {sorted(unknown)}，允许：{sorted(allowed)}"
            )
        if field_name == "save_path_template" and "anime" not in used:
            warnings.append("qb.save_path_template 未包含 {anime}，同一季所有番剧会落在同一目录")

    if not config.qb.save_root:
        warnings.append("qb.save_root 为空，save_path 模板中的 {root} 会展开为空字符串")

    if "bangumi_id" not in _placeholders(config.qb.torrent_tag_template):
        warnings.append(
            "qb.torrent_tag_template 未包含 {bangumi_id}，换组替换（W4）将无法精确定位种子"
        )

    if config.qb.refresh_interval_minutes <= 0:
        errors.append("qb.refresh_interval_minutes 必须大于 0")

    for group in ("simplified", "traditional", "japanese", "dual_zh_jp", "softsub", "hardsub"):
        values = getattr(config.taxonomy, group)
        if not values:
            errors.append(f"taxonomy.{group} 不能为空")

    overlap = set(config.taxonomy.simplified) & set(config.taxonomy.traditional)
    ambiguous = overlap - {"简", "繁"}
    if ambiguous:
        errors.append(f"taxonomy 中简体与繁体标签重叠：{sorted(ambiguous)}")

    if not config.taxonomy.dual_zh_jp and config.analysis.profile.endswith("normal"):
        warnings.append("taxonomy.dual_zh_jp 为空，普通模式下无法识别双语字幕")

    if not env.qb_host:
        warnings.append("未配置 QB_HOST（.env），qb 相关命令不可用")
    if not env.qb_username or not env.qb_password:
        warnings.append("未配置 QB_USERNAME / QB_PASSWORD（.env），无法登录 qBittorrent")
    if config.bangumi.enabled and not env.bgm_token and not config.bangumi.token_file:
        warnings.append(
            "未配置 BGM_TOKEN（.env），Bangumi 精确放送日程不可用；"
            "可按 config.bangumi.token_file 指定 token 文件"
        )
    if env.mikan_base and env.mikan_base.rstrip("/") != config.mikan.base_url.rstrip("/"):
        warnings.append(
            f"MIKAN_BASE({env.mikan_base}) 与 "
            f"config.mikan.base_url({config.mikan.base_url}) 不一致，以 .env 为准"
        )

    return errors, warnings


def effective_mikan_base(config: AppConfig, env: EnvSettings) -> str:
    """返回生效的 Mikan 地址（.env 覆盖 config），并去掉末尾斜杠。

    Args:
        config: 应用配置，提供 mikan.base_url。
        env: 环境凭据，mikan_base 非空时优先。
    """
    base = env.mikan_base or config.mikan.base_url
    return base.rstrip("/")


def expand_template(template: str, **values: Any) -> str:
    """按关键字参数展开 {占位符} 模板；缺值时抛 ConfigError。

    Args:
        template: 含 {占位符} 的模板字符串。
        **values: 占位符名到取值的映射。
    """
    try:
        return template.format(**values)
    except KeyError as exc:
        raise ConfigError(f"模板 {template!r} 缺少占位符值：{exc}") from exc


def _placeholders(template: str) -> set[str]:
    """解析模板中出现的占位符名集合。"""
    import string

    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def env_example_path() -> Path:
    """包内置 .env.example 的路径。"""
    return default_env_path().with_name(".env.example")
