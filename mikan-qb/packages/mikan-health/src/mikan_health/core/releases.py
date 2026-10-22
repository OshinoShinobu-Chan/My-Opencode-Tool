"""Mikan 发布标题解析：集号、语言、分辨率与字幕类型。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mikan_health.core.taxonomy import Taxonomy
from mikan_health.labels import (
    LANG_BOTH,
    LANG_DUAL,
    LANG_NONE,
    LANG_SIMPLE,
    LANG_TRAD,
    SIMPLIFIED_LABELS,
    SOURCE_EXPLICIT,
    SOURCE_INFERRED,
    SOURCE_NONE,
    SOURCE_RAW,
    TRADITIONAL_LABELS,
    is_explicit_chinese,
)

LEADING_GROUP_RE = re.compile(r"^\s*[\[【]([^\]】]{1,60})[\]】]")
BRACKET_NUMBER_RE = re.compile(r"\[(\d{1,4}(?:\.\d)?)(?:\s*[vV]\d)?\]")
EPISODE_CN_RE = re.compile(r"第\s*(\d{1,3})\s*[话話集]")
DASH_EPISODE_RE = re.compile(r"(?:^|[\s\-–—])\s*(\d{1,3})(?=\s|\[|【|\]|\.|$)")
EPISODE_EN_RE = re.compile(r"\b(?:E|EP|Ep)\s?(\d{1,3})\b")
FINAL_EPISODE_RE = re.compile(r"\b(\d{1,3})\s*(?:END|FIN)\b")
RESOLUTION_RE = re.compile(r"(?i)\b(2160p|1440p|1080p|720p|480p|4k)\b")
CODEC_RE = re.compile(r"(?i)\b(HEVC|x265|H\.?265|AVC|x264|H\.?264|VP9|AV1)\b")
BIT_DEPTH_RE = re.compile(r"(?i)\b(\d{1,2})\s?-?bit\b")
SOURCE_RE = re.compile(r"(?i)\b(Baha|Bahamut|WEB-?DL|WEB-?Rip|BDRip|TVRip|AMZN|Netflix|CR)\b")

SUBTYPE_MAP = {"内封": "内封", "外挂": "外挂", "内嵌": "内嵌", "内挂": "内挂"}


@dataclass(frozen=True)
class ParsedTitle:
    """一条发布标题的结构化解析结果。

    Attributes:
        raw: 原始标题。
        subgroup_label: 标题开头的字幕组标签；无则 None。
        episode: 解析出的集号（浮点，支持 12.5）；无法解析为 None。
        episode_raw: 集号的原始文本（如 "12"、"第12话"）。
        is_pack: 是否为合集。
        is_extra: 是否为特典 / OP / ED 等附加内容。
        resolution: 分辨率标签（如 1080p）。
        codec: 编码标签（如 HEVC / x264）。
        bit_depth: 位深（如 10）。
        source_tag: 片源标签（如 WEB-DL / BDRip）。
        language: 判定出的语言标签。
        languages: 命中的所有语言标签。
        sub_type: 字幕类型（内封 / 外挂 / 内嵌 / 内挂）。
        is_dual_zh_jp: 是否为简繁 / 中日双语。
        has_japanese: 是否含日语。
        language_source: 语言判定来源（explicit / inferred / raw / none）。
    """

    raw: str
    subgroup_label: str | None
    episode: float | None
    episode_raw: str | None
    is_pack: bool
    is_extra: bool
    resolution: str | None
    codec: str | None
    bit_depth: int | None
    source_tag: str | None
    language: str
    languages: tuple[str, ...]
    sub_type: str | None
    is_dual_zh_jp: bool
    has_japanese: bool
    language_source: str = SOURCE_NONE

    @property
    def has_simplified(self) -> bool:
        return self.language in SIMPLIFIED_LABELS

    @property
    def has_traditional(self) -> bool:
        return self.language in TRADITIONAL_LABELS

    @property
    def is_raw(self) -> bool:
        return self.language_source == SOURCE_RAW

    @property
    def has_explicit_chinese(self) -> bool:
        return is_explicit_chinese(self.language, self.language_source)


def parse_title(title: str, taxonomy: Taxonomy | None = None) -> ParsedTitle:
    """解析 Mikan 标题，抽取集号、语言、分辨率等结构化信息。

    Args:
        title: 原始发布标题。
        taxonomy: 语言/字幕类型词典；None 用默认。
    """
    tax = taxonomy or Taxonomy()
    normalized = title.strip()

    group_match = LEADING_GROUP_RE.match(normalized)
    subgroup_label = group_match.group(1).strip() if group_match else None

    is_pack = _is_pack(normalized, tax)
    is_extra = any(_contains(normalized, token) for token in tax.exclude_tokens)
    episode_raw, episode = (None, None) if is_pack else _extract_episode(normalized)
    languages, is_dual = _detect_languages(normalized, tax)
    has_japanese = is_dual or any(_contains(normalized, t) for t in tax.japanese)
    language, language_source = _resolve_language(normalized, languages, is_dual, tax)

    return ParsedTitle(
        raw=normalized,
        subgroup_label=subgroup_label,
        episode=episode,
        episode_raw=episode_raw,
        is_pack=is_pack,
        is_extra=is_extra,
        resolution=_first(RESOLUTION_RE, normalized),
        codec=_normalize_codec(_first(CODEC_RE, normalized)),
        bit_depth=_as_int(_first(BIT_DEPTH_RE, normalized)),
        source_tag=_first(SOURCE_RE, normalized),
        language=language,
        languages=tuple(languages),
        sub_type=_detect_sub_type(normalized, tax),
        is_dual_zh_jp=is_dual,
        has_japanese=has_japanese,
        language_source=language_source,
    )


def contains_token(text: str, token: str) -> bool:
    """对外暴露的分词包含判断（ASCII 短词走边界匹配）。

    Args:
        text: 待匹配文本（通常是标题）。
        token: 待查找的 token。
    """
    return _contains(text, token)


def _contains(text: str, token: str) -> bool:
    """判断 token 是否出现；短 ASCII 词要求边界匹配以避免误命中。

    Args:
        text: 待匹配文本（通常是标题）。
        token: 待查找的 token。
    """
    if token.isascii() and len(token) <= 4:
        pattern = rf"(?:^|[\[\(【\s])({re.escape(token)})(?:$|[\]\)】\s.\-_])"
        return re.search(pattern, text, re.IGNORECASE) is not None
    return token in text


def _is_pack(title: str, tax: Taxonomy) -> bool:
    """判断是否为合集/全卷标题。

    Args:
        title: 原始发布标题。
        tax: 语言/字幕类型词典。
    """
    if "合集" in title or "全卷" in title:
        return True
    return any(re.search(pattern, title) for pattern in tax.pack_patterns)


def _extract_episode(title: str) -> tuple[str | None, float | None]:
    """从方括号集号或 第N话/E01/END 等模式抽取集号原文与数值。

    Args:
        title: 原始发布标题。
    """
    bracketed = BRACKET_NUMBER_RE.findall(title)
    if bracketed:
        raw = bracketed[-1]
        return raw, _as_float(raw)

    for regex in (EPISODE_CN_RE, FINAL_EPISODE_RE, DASH_EPISODE_RE, EPISODE_EN_RE):
        matches = regex.findall(title)
        if matches:
            raw = matches[-1]
            return raw, _as_float(raw)

    return None, None


def _resolve_language(
    title: str, languages: list[str], is_dual: bool, tax: Taxonomy
) -> tuple[str, str]:
    """判定语言并标注来源（显式/无字幕/推断）。

    优先级：显式简繁或简日双语 → raw/生肉标记 → 按 default_language 推断（默认繁体）。

    Args:
        title: 原始发布标题。
        languages: 已命中的语言标签列表。
        is_dual: 是否判定为简日/中日双语。
        tax: 语言/字幕类型词典。
    """
    label = _language_label(languages, is_dual)
    if label != LANG_NONE:
        return label, SOURCE_EXPLICIT
    if any(_contains(title, marker) for marker in tax.raw_markers):
        return LANG_NONE, SOURCE_RAW
    if tax.default_language:
        return tax.default_language, SOURCE_INFERRED
    return LANG_NONE, SOURCE_NONE

def subgroup_language_override(
    tax: Taxonomy, subgroup_id: int, label: str | None
) -> str | None:
    """按字幕组 id 或标题中的组名查语言白名单。

    Args:
        tax: 语言/字幕类型词典（含 subgroup_languages）。
        subgroup_id: 字幕组 id（RSS 的 subgroupid）。
        label: 标题中的字幕组标签；无则为 None。
    """
    for key, language in tax.subgroup_languages.items():
        name = key.strip()
        if name == str(subgroup_id):
            return language
        if label and name.casefold() == label.casefold():
            return language
    return None


def _detect_languages(title: str, tax: Taxonomy) -> tuple[list[str], bool]:
    """扫描标题中的简/繁/双语标记，返回标签列表与是否简日双语。

    Args:
        title: 原始发布标题。
        tax: 语言/字幕类型词典。
    """
    languages: list[str] = []
    is_dual = any(_contains(title, token) for token in tax.dual_zh_jp)

    if is_dual:
        languages.append(LANG_DUAL)
    if any(_contains(title, token) for token in tax.simplified):
        languages.append(LANG_SIMPLE)
    if any(_contains(title, token) for token in tax.traditional):
        languages.append(LANG_TRAD)

    return languages, is_dual


def _detect_sub_type(title: str, tax: Taxonomy) -> str | None:
    """识别内封/外挂/内嵌/内挂等字幕类型；无则 None。

    Args:
        title: 原始发布标题。
        tax: 语言/字幕类型词典。
    """
    for token in (*tax.softsub, *tax.hardsub):
        if token in SUBTYPE_MAP and token in title:
            return SUBTYPE_MAP[token]
    return None


def _language_label(languages: list[str], is_dual: bool) -> str:
    """把语言标签集合归并成单一语言标签。

    Args:
        languages: 已命中的语言标签列表。
        is_dual: 是否判定为简日/中日双语。
    """
    if is_dual:
        return LANG_DUAL
    if LANG_SIMPLE in languages and LANG_TRAD in languages:
        return LANG_BOTH
    if LANG_SIMPLE in languages:
        return LANG_SIMPLE
    if LANG_TRAD in languages:
        return LANG_TRAD
    return LANG_NONE


def _normalize_codec(value: str | None) -> str | None:
    """把 x265/H.265 等编码名归一为 HEVC/AVC。"""
    if value is None:
        return None
    upper = value.upper().replace(".", "")
    if upper in ("X265", "H265"):
        return "HEVC"
    if upper in ("X264", "H264"):
        return "AVC"
    return upper


def _first(regex: re.Pattern[str], text: str) -> str | None:
    """取正则首个捕获组；未命中返回 None。

    Args:
        regex: 已编译的正则（含一个捕获组）。
        text: 待匹配文本。
    """
    match = regex.search(text)
    return match.group(1) if match else None


def _as_int(value: str | None) -> int | None:
    return int(value) if value is not None and value.isdigit() else None


def _as_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
