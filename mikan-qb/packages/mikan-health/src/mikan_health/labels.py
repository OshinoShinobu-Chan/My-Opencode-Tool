"""字幕语言/来源标签与判定。

放在顶层叶子模块（无内部依赖），供 `core` 与 `data` 两层共用，避免循环导入。
"""

from __future__ import annotations

LANG_DUAL = "简日双语"
LANG_SIMPLE = "简体"
LANG_TRAD = "繁体"
LANG_BOTH = "简繁"
LANG_NONE = "无中文"

SOURCE_EXPLICIT = "explicit"
SOURCE_INFERRED = "inferred"
SOURCE_OVERRIDE = "override"
SOURCE_RAW = "raw"
SOURCE_NONE = "none"

SIMPLIFIED_LABELS = (LANG_SIMPLE, LANG_BOTH, LANG_DUAL)
TRADITIONAL_LABELS = (LANG_TRAD, LANG_BOTH, LANG_DUAL)


def is_explicit_chinese(language: str, language_source: str) -> bool:
    """标题里明确写了简/繁，或被字幕组白名单覆盖（不含推断值）。

    Args:
        language: 语言标签。
        language_source: 语言判定来源（explicit / inferred / raw / none）。
    """
    return language_source in (SOURCE_EXPLICIT, SOURCE_OVERRIDE) and language != LANG_NONE
