"""Mikan 站点数据源：抓取番剧、发布、RSS、季度与搜索。"""

from __future__ import annotations

import httpx

from mikan_health.core.taxonomy import Taxonomy
from mikan_health.data.cache import HttpCache
from mikan_health.data.http import BaseHttpClient, HttpError
from mikan_health.data.index import SubgroupIndex
from mikan_health.data.models import (
    SOURCE_EXPAND,
    BangumiDetail,
    PublishGroupDetail,
    Release,
    SearchResult,
    SeasonEntry,
)
from mikan_health.data.parse import (
    parse_bangumi_page,
    parse_episode_table,
    parse_publish_group,
    parse_rss,
    parse_search,
    parse_season,
)

DEFAULT_USER_AGENT = "mikan-qb/0.1 (+https://mikanani.kas.pub; personal use)"
DEFAULT_BASE_URL = "https://mikanani.kas.pub"
EXPAND_TAKE = 500

SEASON_EN = {"冬": "winter", "春": "spring", "夏": "summer", "秋": "fall"}
SEASON_CN = {en: cn for cn, en in SEASON_EN.items()}
SEASON_CN["autumn"] = "秋"
SEASON_ALIASES = {
    **{cn: cn for cn in SEASON_EN},
    **SEASON_CN,
    "1": "冬",
    "2": "春",
    "3": "夏",
    "4": "秋",
    "01": "冬",
    "02": "春",
    "03": "夏",
    "04": "秋",
}


class MikanError(HttpError):
    """Mikan 访问异常。"""

    pass


def normalize_season(value: str) -> str:
    """把季度别名（冬/1/winter 等）归一为中文季度；无法识别则抛错。

    Args:
        value: 季度别名文本。
    """
    key = str(value).strip().lower()
    if key not in SEASON_ALIASES:
        raise ValueError(
            f"无法识别的季度：{value!r}（可用 冬/春/夏/秋 或 winter/spring/summer/fall）"
        )
    return SEASON_ALIASES[key]


def season_label(year: int, season: str) -> str:
    """返回如 "2024春" 的季度标签。

    Args:
        year: 年份。
        season: 季度别名（冬/1/winter 等）。
    """
    return f"{year}{normalize_season(season)}"


class MikanSource(BaseHttpClient):
    """访问 Mikan 的客户端（带缓存/限速，结果解析为数据模型）。"""

    service_name = "Mikan"
    error_cls = MikanError

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        taxonomy: Taxonomy | None = None,
        cache: HttpCache | None = None,
        index: SubgroupIndex | None = None,
        ttl_hours: float = 6.0,
        rate_limit_per_sec: float = 1.0,
        timeout: float = 20.0,
        max_retries: int = 3,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        """初始化客户端参数（不发起请求）。

        Args:
            base_url: Mikan 站点根地址。
            taxonomy: 标签表；None 用默认。
            cache: HTTP 缓存；None 表示不缓存。
            index: 字幕组索引；None 表示不更新。
            ttl_hours: 缓存有效期（小时）。
            rate_limit_per_sec: 每秒请求上限。
            timeout: 单次请求超时（秒）。
            max_retries: 最大重试次数。
            user_agent: 请求 User-Agent。
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
        self.taxonomy = taxonomy or Taxonomy()
        self.index = index

    def _headers(self) -> dict[str, str]:
        return {
            **super()._headers(),
            "X-Requested-With": "XMLHttpRequest",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

    def _decode_response(self, response: httpx.Response) -> str:
        return response.text

    def _decode_cache(self, body: bytes) -> str:
        return body.decode("utf-8", errors="replace")

    def _encode_cache(self, value: str) -> bytes:
        return value.encode("utf-8")

    def bangumi(self, bangumi_id: int, force: bool = False) -> BangumiDetail:
        """抓取番剧主页并顺手更新字幕组索引。

        Args:
            bangumi_id: 番剧 id。
            force: 为真时跳过缓存强制拉取。
        """
        html = self._get(f"/Home/Bangumi/{bangumi_id}", force=force)
        detail = parse_bangumi_page(html, bangumi_id, self.taxonomy)
        if self.index is not None:
            self.index.upsert(detail.subgroups)
        return detail

    def subgroup_releases(
        self,
        bangumi_id: int,
        subgroup_id: int,
        take: int = EXPAND_TAKE,
        force: bool = False,
    ) -> list[Release]:
        """抓取某字幕组的展开剧集表。

        Args:
            bangumi_id: 番剧 id。
            subgroup_id: 字幕组 id。
            take: 最多拉取的条数。
            force: 为真时跳过缓存强制拉取。
        """
        html = self._get(
            "/Home/ExpandEpisodeTable",
            params={"bangumiId": bangumi_id, "subtitleGroupId": subgroup_id, "take": take},
            force=force,
        )
        return parse_episode_table(
            html, bangumi_id, subgroup_id, self.taxonomy, source=SOURCE_EXPAND
        )

    def rss(self, bangumi_id: int, subgroup_id: int, force: bool = False) -> list[Release]:
        """抓取某字幕组的 RSS 发布列表。

        Args:
            bangumi_id: 番剧 id。
            subgroup_id: 字幕组 id。
            force: 为真时跳过缓存强制拉取。
        """
        xml_text = self._get(
            "/RSS/Bangumi",
            params={"bangumiId": bangumi_id, "subgroupid": subgroup_id},
            force=force,
        )
        return parse_rss(xml_text, bangumi_id, subgroup_id, self.taxonomy)

    def season(self, year: int, season: str, force: bool = False) -> list[SeasonEntry]:
        """抓取指定年份/季度的番剧列表。

        Args:
            year: 年份。
            season: 季度别名或中文（如 春 / spring）。
            force: 为真时跳过缓存强制拉取。
        """
        html = self._get(
            "/Home/BangumiCoverFlowByDayOfWeek",
            params={"year": year, "seasonStr": normalize_season(season)},
            force=force,
        )
        return parse_season(html)

    def search(self, keyword: str, force: bool = False) -> list[SearchResult]:
        """按关键词搜索番剧。

        Args:
            keyword: 搜索关键词。
            force: 为真时跳过缓存强制拉取。
        """
        html = self._get("/Home/Search", params={"searchstr": keyword}, force=force)
        return parse_search(html)

    def publish_group(self, publish_group_id: int, force: bool = False) -> PublishGroupDetail:
        """抓取字幕组年表（其作品列表）。

        Args:
            publish_group_id: 作品年表 id。
            force: 为真时跳过缓存强制拉取。
        """
        html = self._get(f"/Home/PublishGroup/{publish_group_id}", force=force)
        return parse_publish_group(html, publish_group_id)
