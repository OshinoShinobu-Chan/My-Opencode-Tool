"""字幕标题解析用的标签词典（语言、字幕类型、合集模式等）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

DEFAULT_SIMPLIFIED = ["简体", "简中", "简", "CHS", "GB"]
DEFAULT_TRADITIONAL = ["繁体", "繁中", "繁", "CHT", "BIG5"]
DEFAULT_JAPANESE = ["日语", "日文", "JP", "JPN"]
DEFAULT_DUAL_ZH_JP = ["简繁日", "简日繁", "简日", "繁日", "JPSC", "JPTC"]
DEFAULT_SOFTSUB = ["内封", "外挂"]
DEFAULT_HARDSUB = ["内嵌", "内挂"]
DEFAULT_RAW_MARKERS = [
    "raw",
    "raws",
    "生肉",
    "无字幕",
    "無字幕",
    "无字幕版",
    "no sub",
    "nosub",
    "无sub",
]
DEFAULT_EXCLUDE_TOKENS = [
    "合集",
    "BDRip",
    "全卷",
    "Fin",
    "NCOP",
    "NCED",
    "PV",
    "Menu",
    "Fonts",
    "字体",
    "SP",
    "OVA",
]
PACK_RANGE_RE = r"\[\d{1,3}\s*[-~—]\s*\d{1,3}\]"
PACK_FULL_RE = r"\[(?:TV)?全集\]"

DEFAULT_PACK_PATTERNS = [
    PACK_RANGE_RE,
    r"\b\d{1,3}\s*[-~—]\s*\d{1,3}\s*(?:TV)?全集\b",
    PACK_FULL_RE,
    r"\[(?:完)\]",
]
# 变体筛选用于 must_not_contain 的正则片段；额外含裸「合集/全卷」，以覆盖无集号范围的合集。
DEFAULT_PACK_FILTER_PATTERNS = (PACK_RANGE_RE, "合集", "全卷", PACK_FULL_RE)

DEFAULT_EDITION_TOKENS = [
    "无修",
    "Uncensored",
    "Baha",
    "ABEMA",
    "TV版",
]


class Taxonomy(BaseModel):
    """字幕标题的标签词典。所有解析都基于这里，改词典不需要改代码。

    Attributes:
        simplified: 简体标签。
        traditional: 繁体标签。
        japanese: 日语标签。
        dual_zh_jp: 简繁 / 中日双语标签。
        softsub: 软字幕标签（内封 / 外挂）。
        hardsub: 硬字幕标签（内嵌 / 内挂）。
        raw_markers: 生肉 / 无字幕标记。
        exclude_tokens: 需要排除的 token（合集、NCOP、PV 等）。
        pack_patterns: 合集标题匹配正则。
        default_language: 无明确标记时的默认语言（空串表示不默认）。
        edition_tokens: 版本/片源区分标签；多命中消歧时按顺序优先（越靠前越优先）。
        subgroup_languages: 无显式语言标注的字幕组白名单（sid 或组名 -> 语言）。
    """

    simplified: list[str] = Field(default_factory=lambda: list(DEFAULT_SIMPLIFIED))
    traditional: list[str] = Field(default_factory=lambda: list(DEFAULT_TRADITIONAL))
    japanese: list[str] = Field(default_factory=lambda: list(DEFAULT_JAPANESE))
    dual_zh_jp: list[str] = Field(default_factory=lambda: list(DEFAULT_DUAL_ZH_JP))
    softsub: list[str] = Field(default_factory=lambda: list(DEFAULT_SOFTSUB))
    hardsub: list[str] = Field(default_factory=lambda: list(DEFAULT_HARDSUB))
    raw_markers: list[str] = Field(default_factory=lambda: list(DEFAULT_RAW_MARKERS))
    exclude_tokens: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDE_TOKENS))
    pack_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_PACK_PATTERNS))
    # 没有明确字幕标记时的默认语言（空字符串表示不默认）
    default_language: str = "繁体"
    # 版本/片源区分标签：多命中时按顺序优先（越靠前越优先）
    edition_tokens: list[str] = Field(default_factory=lambda: list(DEFAULT_EDITION_TOKENS))
    # 无显式语言标注的字幕组白名单：键为 sid 字符串或标题里的字幕组名
    subgroup_languages: dict[str, str] = Field(default_factory=dict)
