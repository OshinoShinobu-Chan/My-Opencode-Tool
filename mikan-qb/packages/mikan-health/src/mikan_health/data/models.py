"""数据模型：发布、字幕组、番剧详情与搜索结果等。"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from mikan_health.labels import is_explicit_chinese

SOURCE_BANGUMI_PAGE = "bangumi-page"
SOURCE_EXPAND = "expand"
SOURCE_RSS = "rss"


class Release(BaseModel):
    """一条发布记录（标题解析结果 + 来源元数据）。

    Attributes:
        bangumi_id: 所属番剧 id。
        subtitle_group_id: 字幕组 id。
        title: 发布标题。
        episode: 解析出的集号；非单集为 None。
        episode_raw: 集号的原始文本。
        is_pack: 是否为合集。
        is_extra: 是否为特典 / OP / ED 等。
        resolution: 分辨率标签。
        codec: 编码标签。
        language: 语言标签；默认「无中文」。
        language_source: 语言判定来源（explicit / inferred / raw / none）。
        sub_type: 字幕类型（内封 / 外挂 / 内嵌 / 内挂）。
        is_dual_zh_jp: 是否为简繁 / 中日双语。
        subgroup_label: 标题里的字幕组标签。
        published_at: 发布时间。
        size_bytes: 文件大小（字节）。
        magnet: 磁力链接。
        torrent_url: 种子链接。
        episode_url: 单集页面链接。
        source: 数据来源（bangumi-page / expand / rss）。
    """

    bangumi_id: int
    subtitle_group_id: int
    title: str
    episode: float | None = None
    episode_raw: str | None = None
    is_pack: bool = False
    is_extra: bool = False
    resolution: str | None = None
    codec: str | None = None
    language: str = "无中文"
    language_source: str = "none"
    sub_type: str | None = None
    is_dual_zh_jp: bool = False
    subgroup_label: str | None = None
    published_at: datetime | None = None
    size_bytes: int | None = None
    magnet: str | None = None
    torrent_url: str | None = None
    episode_url: str | None = None
    source: str = SOURCE_BANGUMI_PAGE

    @property
    def is_episode(self) -> bool:
        """是否为非合集、非特典且有集号的单集发布。"""
        return not self.is_pack and not self.is_extra and self.episode is not None

    @property
    def has_explicit_chinese(self) -> bool:
        """标题里明确写了简/繁；`language_source == "inferred"` 的默认值不算。"""
        return is_explicit_chinese(self.language, self.language_source)


class Subgroup(BaseModel):
    """字幕组条目（id、名称、年表 id、RSS 与更新情况）。

    Attributes:
        subtitle_group_id: 字幕组 id。
        name: 字幕组名称。
        publish_group_id: 作品年表 id；未知为 None。
        rss_url: 字幕组 RSS 链接。
        last_update: 最近更新时间。
        release_count: 本页可见的发布数。
    """

    subtitle_group_id: int
    name: str
    publish_group_id: int | None = None
    rss_url: str | None = None
    last_update: datetime | None = None
    release_count: int = 0


class BangumiDetail(BaseModel):
    """一部番剧的详情：标题、日期、字幕组与发布列表。

    Attributes:
        bangumi_id: Mikan 番剧 id。
        title: 番剧标题。
        weekday: 放送星期（中文）。
        start_date: 开播日期。
        official_site: 官网地址。
        bangumi_tv_url: bgm.tv 条目链接。
        rss_url: 番剧 RSS 链接（相对或绝对）。
        subgroups: 字幕组列表。
        releases: 页面可见的发布列表。
    """

    bangumi_id: int
    title: str
    weekday: str | None = None
    start_date: date | None = None
    official_site: str | None = None
    bangumi_tv_url: str | None = None
    rss_url: str | None = None
    subgroups: list[Subgroup] = Field(default_factory=list)
    releases: list[Release] = Field(default_factory=list)

    def subgroup(self, subgroup_id: int) -> Subgroup | None:
        """按 subtitle_group_id 查找字幕组；无则 None。

        Args:
            subgroup_id: 字幕组 id。
        """
        for item in self.subgroups:
            if item.subtitle_group_id == subgroup_id:
                return item
        return None


class SearchResult(BaseModel):
    """搜索结果条目。

    Attributes:
        bangumi_id: 番剧 id。
        title: 标题。
        cover_url: 封面图链接。
    """

    bangumi_id: int
    title: str
    cover_url: str | None = None


class WorkRef(BaseModel):
    """作品年表中「某字幕组的某部作品」引用。

    Attributes:
        bangumi_id: 作品番剧 id。
        subtitle_group_id: 字幕组 id。
        title: 作品标题。
        season_date: 季度（如 "2024.4"）；未知为空串。
        season_label: 季度中文标签（如 "2024春"）。
        is_current: 是否为当季（连载中）。
    """

    bangumi_id: int
    subtitle_group_id: int
    title: str
    season_date: str = ""
    season_label: str = ""
    is_current: bool = False


class PublishGroupDetail(BaseModel):
    """字幕组年表详情（含其作品列表）。

    Attributes:
        publish_group_id: 作品年表 id。
        name: 字幕组名称。
        works: 作品引用列表。
    """

    publish_group_id: int
    name: str = ""
    works: list[WorkRef] = Field(default_factory=list)

    def work_ids(self, limit: int | None = None) -> list[int]:
        """去重后的 bangumi_id 列表，可截断到 limit。

        Args:
            limit: 最多返回条数；None 表示不截断。
        """
        seen: list[int] = []
        for work in self.works:
            if work.bangumi_id not in seen:
                seen.append(work.bangumi_id)
        return seen[:limit] if limit else seen


class SeasonEntry(BaseModel):
    """季度番剧列表条目。

    Attributes:
        bangumi_id: 番剧 id。
        title: 标题。
        weekday: 放送星期（0=周日 … 6=周六）。
        weekday_cn: 放送星期中文。
        last_update: 最近更新时间文本。
    """

    bangumi_id: int
    title: str
    weekday: int | None = None
    weekday_cn: str | None = None
    last_update: str | None = None
