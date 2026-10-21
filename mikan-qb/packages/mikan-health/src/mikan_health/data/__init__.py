"""data 包对外导出的公共 API（数据源、模型与解析器）。"""

from mikan_health.data.cache import CacheStats, HttpCache
from mikan_health.data.index import SubgroupIndex
from mikan_health.data.models import (
    SOURCE_BANGUMI_PAGE,
    SOURCE_EXPAND,
    SOURCE_RSS,
    BangumiDetail,
    PublishGroupDetail,
    Release,
    SearchResult,
    SeasonEntry,
    Subgroup,
    WorkRef,
)
from mikan_health.data.parse import (
    parse_bangumi_page,
    parse_episode_table,
    parse_publish_group,
    parse_rss,
    parse_search,
    parse_season,
)
from mikan_health.data.source import (
    MikanError,
    MikanSource,
    normalize_season,
    season_label,
)

__all__ = [
    "SOURCE_BANGUMI_PAGE",
    "SOURCE_EXPAND",
    "SOURCE_RSS",
    "BangumiDetail",
    "CacheStats",
    "HttpCache",
    "MikanError",
    "MikanSource",
    "PublishGroupDetail",
    "Release",
    "SearchResult",
    "SeasonEntry",
    "Subgroup",
    "SubgroupIndex",
    "WorkRef",
    "normalize_season",
    "parse_bangumi_page",
    "parse_episode_table",
    "parse_publish_group",
    "parse_rss",
    "parse_search",
    "parse_season",
    "season_label",
]
