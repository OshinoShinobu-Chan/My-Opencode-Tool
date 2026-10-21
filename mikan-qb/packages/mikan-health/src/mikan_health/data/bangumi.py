"""Bangumi.tv API 客户端与数据模型（精确放送日程来源）。"""

from __future__ import annotations

import os
import re
from datetime import date as _date
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from mikan_health.data.cache import HttpCache
from mikan_health.data.http import BaseHttpClient, HttpError

DEFAULT_BASE_URL = "https://api.bgm.tv/v0"
DEFAULT_USER_AGENT = "mikan-qb/0.1 (personal use; +https://bgm.tv)"
EPISODE_TYPE_MAIN = 0
TOKEN_ENV_VARS = ("BANGUMI_TOKEN", "BGM_TOKEN")
PROXY_ENV_VARS = (
    "BGM_PROXY",
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
)


_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")


class BangumiError(HttpError):
    """Bangumi 访问异常。"""

    pass


def _coerce_date(value: object) -> object:
    """把 Bangumi 的日期字段归一为 pydantic 可解析形式。

    空串等占位值转 None；`2026-4-26` 这类未补零日期补零为 `2026-04-26`。
    """
    if value in ("", "None", "0000-00-00"):
        return None
    if isinstance(value, str):
        # 容忍前后缀注释与不规范分隔（如「原定2021-07-28」「2022-10-02（…）」）
        match = _DATE_RE.search(value.strip())
        if not match:
            return None  # 纯年份 / 年月 / 手误值一律视为未知
        year, month, day = (int(part) for part in match.groups())
        try:
            return _date(year, month, day).isoformat()
        except ValueError:
            return None
    return value


class BangumiEpisode(BaseModel):
    """Bangumi 单集信息。

    Attributes:
        id: 剧集 id。
        type: 剧集类型（0=本篇，其余为 OP/ED/特典）。
        sort: 排序序号。
        ep: 集号；未定为 None。
        airdate: 放送日期；未知为 None。
        name: 原名。
        name_cn: 中文名。
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    type: int = 0
    sort: float = 0
    ep: float | None = None
    airdate: _date | None = None
    name: str = ""
    name_cn: str = ""

    @field_validator("airdate", mode="before")
    @classmethod
    def _normalize_airdate(cls, value: object) -> object:
        """归一放送日期（空串→None，未补零→补零）。"""
        return _coerce_date(value)

    @field_validator("ep", mode="before")
    @classmethod
    def _blank_number(cls, value: object) -> object:
        """把空字符串集号归一为 None。"""
        return None if value in ("", "None") else value

    @property
    def is_main(self) -> bool:
        """是否为本篇（非 OP/ED/特典）。"""
        return self.type == EPISODE_TYPE_MAIN


class BangumiEpisodes(BaseModel):
    """某条目的全部剧集及总数。

    Attributes:
        subject_id: 所属条目 id。
        episodes: 剧集列表。
        total: 剧集总数。
    """

    subject_id: int
    episodes: list[BangumiEpisode] = Field(default_factory=list)
    total: int = 0

    def main_episodes(self) -> list[BangumiEpisode]:
        """只保留本篇剧集。"""
        return [ep for ep in self.episodes if ep.is_main]

    def airdates(self) -> list[_date]:
        """本篇剧集的放送日期（跳过无日期的集）。"""
        return [ep.airdate for ep in self.main_episodes() if ep.airdate is not None]


class BangumiSubject(BaseModel):
    """Bangumi 条目信息（名称、放送日期、放送星期等）。

    Attributes:
        id: 条目 id。
        name: 原名。
        name_cn: 中文名。
        air_date: 放送开始日期（API 字段别名为 date）。
        eps: 话数（bgm 原始字段）。
        total_episodes: 总集数。
        platform: 平台（TV / WEB 等）。
        infobox: 条目信息框键值列表。
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    name: str = ""
    name_cn: str = ""
    air_date: _date | None = Field(default=None, alias="date")
    eps: int | None = None
    total_episodes: int | None = None
    platform: str = ""
    infobox: list[dict] = Field(default_factory=list)

    @field_validator("air_date", mode="before")
    @classmethod
    def _normalize_air_date(cls, value: object) -> object:
        """归一放送开始日期（空串→None，未补零→补零）。"""
        return _coerce_date(value)

    def infobox_value(self, key: str) -> str | None:
        """从 infobox 中按 key 取值，兼容字符串与列表两种形式。

        Args:
            key: infobox 的键名（如「放送星期」）。
        """
        for item in self.infobox:
            if item.get("key") != key:
                continue
            value = item.get("value")
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                parts = [str(v["v"]) for v in value if isinstance(v, dict) and v.get("v")]
                return " / ".join(parts) if parts else None
        return None

    @property
    def broadcast_weekday(self) -> str | None:
        """从 infobox 读取「放送星期」；无则 None。"""
        return self.infobox_value("放送星期")


def detect_proxy() -> str | None:
    """代理探测：BGM_PROXY → 通用代理环境变量 → Windows 注册表 → 直连。"""
    for var in PROXY_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return _normalize_proxy(value)
    registry = _registry_proxy()
    return _normalize_proxy(registry) if registry else None


def _normalize_proxy(value: str) -> str:
    """补全代理地址的协议前缀（缺省 http://）。

    Args:
        value: 原始代理地址，可含或省略协议。
    """
    value = value.strip()
    return value if "://" in value else f"http://{value}"


def _registry_proxy() -> str | None:
    """读取 Windows 注册表中的系统代理；不可用或失败返回 None。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        return server if enabled and server else None
    except Exception:  # noqa: BLE001
        return None


def load_token(explicit: str | None = None, extra_paths: list[Path] | None = None) -> str | None:
    """按 显式参数 → 环境变量 → token 文件 的顺序查找 Bangumi token。

    Args:
        explicit: 显式传入的 token；None/空则继续查找。
        extra_paths: 额外 token 文件路径列表；None 表示无。
    """
    if explicit:
        return explicit.strip()
    for var in TOKEN_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return value.strip()
    for path in extra_paths or []:
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
    return None


def subject_id_from_url(url: str | None) -> int | None:
    """从 Bangumi 链接中抽取 subject id；解析失败返回 None。

    Args:
        url: Bangumi 条目链接；None 或格式不符返回 None。
    """
    if not url:
        return None
    marker = "/subject/"
    if marker not in url:
        return None
    tail = url.split(marker, 1)[1]
    digits = ""
    for char in tail:
        if char.isdigit():
            digits += char
        else:
            break
    return int(digits) if digits else None


class BangumiClient(BaseHttpClient):
    """访问 Bangumi v0 API 的客户端（带缓存/限速/可选鉴权）。"""

    service_name = "Bangumi"
    error_cls = BangumiError

    def __init__(
        self,
        token: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        cache: HttpCache | None = None,
        ttl_hours: float = 12.0,
        proxy: str | None = None,
        rate_limit_per_sec: float = 1.0,
        timeout: float = 20.0,
        max_retries: int = 3,
        user_agent: str = DEFAULT_USER_AGENT,
        token_paths: list[Path] | None = None,
    ) -> None:
        """初始化客户端并加载 token 与代理。

        Args:
            token: 显式 token；None 时按环境变量/文件查找。
            base_url: API 根地址。
            cache: HTTP 缓存；None 表示不缓存。
            ttl_hours: 缓存有效期（小时）。
            proxy: 代理地址；None 表示自动探测。
            rate_limit_per_sec: 每秒请求上限。
            timeout: 单次请求超时（秒）。
            max_retries: 最大重试次数。
            user_agent: 请求 User-Agent。
            token_paths: 额外 token 文件路径列表。
        """
        super().__init__(
            base_url=base_url,
            cache=cache,
            ttl_hours=ttl_hours,
            rate_limit_per_sec=rate_limit_per_sec,
            timeout=timeout,
            max_retries=max_retries,
            user_agent=user_agent,
        )
        self.token = load_token(token, token_paths)
        self.proxy = proxy if proxy is not None else detect_proxy()

    def _headers(self) -> dict[str, str]:
        """在基础头之上附加 JSON Accept 与可选 Bearer token。"""
        headers = {**super()._headers(), "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _proxy(self) -> str | None:
        return self.proxy or None

    def _status_error(self, response: httpx.Response, url: str) -> HttpError:
        """把 401/403/404 翻译成更具体的 BangumiError。"""
        if response.status_code in (401, 403):
            return BangumiError(
                f"Bangumi 拒绝访问（{response.status_code}）：token 缺失或无效",
                url=url,
                status=response.status_code,
            )
        if response.status_code == 404:
            return BangumiError("Bangumi 条目不存在（404）", url=url, status=404)
        return super()._status_error(response, url)

    def _request_failed_error(self, last_error: Exception | None, url: str) -> HttpError:
        return BangumiError(f"访问 Bangumi 失败：{last_error}", url=url)

    def _decode_response(self, response: httpx.Response) -> dict:
        return response.json()

    def _decode_cache(self, body: bytes) -> dict:
        import json

        return json.loads(body.decode("utf-8", errors="replace"))

    def _encode_cache(self, value: dict) -> bytes:
        import json

        return json.dumps(value, ensure_ascii=False).encode("utf-8")

    @property
    def has_token(self) -> bool:
        """是否已配置 token。"""
        return bool(self.token)

    def subject(self, subject_id: int, force: bool = False) -> BangumiSubject:
        """获取条目基本信息。

        Args:
            subject_id: Bangumi 条目 id。
            force: 为真时跳过缓存强制拉取。
        """
        payload = self._get(f"/subjects/{subject_id}", force=force)
        return BangumiSubject.model_validate(payload)

    def episodes(self, subject_id: int, force: bool = False) -> BangumiEpisodes:
        """分页拉取条目的全部剧集并按 sort/id 排序。

        Args:
            subject_id: Bangumi 条目 id。
            force: 为真时跳过缓存强制拉取。
        """
        collected: list[dict] = []
        total = 0
        offset = 0
        while True:
            payload = self._get(
                "/episodes",
                params={"subject_id": subject_id, "limit": 100, "offset": offset},
                force=force,
            )
            rows = payload.get("data") or payload.get("episodes") or []
            total = int(payload.get("total") or len(rows))
            collected.extend(rows)
            offset += len(rows)
            if not rows or offset >= total:
                break
        episodes = [BangumiEpisode.model_validate(row) for row in collected]
        episodes.sort(key=lambda ep: (ep.sort, ep.id))
        return BangumiEpisodes(subject_id=subject_id, episodes=episodes, total=total)
