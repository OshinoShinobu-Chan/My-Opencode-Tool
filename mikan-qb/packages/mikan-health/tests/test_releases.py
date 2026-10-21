from __future__ import annotations

import pytest

from mikan_health.core.releases import ParsedTitle, parse_title

CASES: list[tuple[str, dict]] = [
    (
        "[桜都字幕组] 无职转生 第三季 ～到了异世界就拿出真本事～ / "
        "Mushoku Tensei III： Isekai Ittara Honki Dasu [12][1080P][简繁内封]",
        {
            "subgroup_label": "桜都字幕组",
            "episode": 12.0,
            "episode_raw": "12",
            "language": "简繁",
            "sub_type": "内封",
            "resolution": "1080P",
            "has_simplified": True,
            "has_traditional": True,
        },
    ),
    (
        "[ANi]  Re：从零开始的异世界生活 第四季 - 02 [1080P][Baha][WEB-DL][AAC AVC][CHT][MP4]",
        {
            "subgroup_label": "ANi",
            "episode": 2.0,
            "language": "繁体",
            "resolution": "1080P",
            "codec": "AVC",
            "source_tag": "Baha",
            "has_traditional": True,
            "has_simplified": False,
        },
    ),
    (
        "[LoliHouse] Grand Blue S3 - 11 [WebRip 1080p HEVC-10bit AAC ASSx2].mkv",
        {
            "subgroup_label": "LoliHouse",
            "episode": 11.0,
            "codec": "HEVC",
            "bit_depth": 10,
            "language": "繁体",
            "language_source": "inferred",
        },
    ),
    (
        "[Nekomoe kissaten][20 Seiki Denki Mokuroku][11][1080p][JPSC].mp4",
        {
            "subgroup_label": "Nekomoe kissaten",
            "episode": 11.0,
            "is_dual_zh_jp": True,
            "has_japanese": True,
            "language": "简日双语",
        },
    ),
    (
        "【喵萌奶茶屋】★07月新番★[二十世纪电气目录 / 20 Seiki Denki Mokuroku / "
        "Nijusseiki Denki Mokuroku][01][1080p][简日双语]",
        {
            "subgroup_label": "喵萌奶茶屋",
            "episode": 1.0,
            "is_dual_zh_jp": True,
            "language": "简日双语",
        },
    ),
    (
        "[DBD-Raws][碧蓝之海][01-12TV全集+特典映像][1080P][BDRip][HEVC-10bit][简繁外挂][FLAC][MKV]",
        {
            "subgroup_label": "DBD-Raws",
            "is_pack": True,
            "episode": None,
            "sub_type": "外挂",
        },
    ),
    (
        "[黒ネズミたち] 女仆小姐的贪吃日常 / Maid-san wa Taberu dake - 01 "
        "(CR 1920x1080 AVC AAC MKV)",
        {
            "subgroup_label": "黒ネズミたち",
            "episode": 1.0,
            "codec": "AVC",
            "source_tag": "CR",
            "language": "繁体",
            "language_source": "inferred",
        },
    ),
    (
        "[Sakurato] Onaji Zemi no Someya-san ga Sexy Joyuu Datta Hanashi. [08][AVC-8bit 1080p]",
        {"episode": 8.0, "codec": "AVC", "bit_depth": 8},
    ),
    (
        "[Nekomoe kissaten][某番][第05话][1080p][CHS]",
        {"episode": 5.0, "language": "简体"},
    ),
    (
        "[SweetSub] 某番 [E07][1080p][AVC][GB]",
        {"episode": 7.0, "language": "简体"},
    ),
    (
        "[SPY×FAMILY] 间谍过家家 [01][1080p][简体内嵌]",
        {"episode": 1.0, "is_extra": False, "sub_type": "内嵌"},
    ),
    (
        "[某组] 某某 [NCOP][1080p]",
        {"is_extra": True, "episode": None},
    ),
]


@pytest.mark.parametrize("title,expected", CASES)
def test_parse_title(title: str, expected: dict) -> None:
    parsed = parse_title(title)
    for field, value in expected.items():
        assert getattr(parsed, field) == value, f"{field}: {getattr(parsed, field)!r} != {value!r}"


def test_parse_title_returns_parsed_title() -> None:
    assert isinstance(parse_title("[某组] 番名 [01][1080p][简体内嵌]"), ParsedTitle)


def test_sp_is_not_matched_inside_words() -> None:
    parsed = parse_title("[SPY×FAMILY] 间谍过家家 [03][1080p][简体]")
    assert parsed.is_extra is False
    assert parsed.episode == 3.0


def test_fin_is_not_matched_inside_words() -> None:
    parsed = parse_title("[某组] Final Fantasy 番名 [04][1080p][简体]")
    assert parsed.is_extra is False
    assert parsed.episode == 4.0


def test_1080p_not_taken_as_episode() -> None:
    parsed = parse_title("[某组] 番名 [1080p][简体内嵌]")
    assert parsed.episode is None


def test_decimal_episode() -> None:
    parsed = parse_title("[某组] 番名 [12.5][1080p][简体]")
    assert parsed.episode == 12.5


RAW_CASES: list[tuple[str, dict]] = [
    (
        "[某组] 某番 [01][1080p][RAW]",
        {"language": "无中文", "language_source": "raw", "is_raw": True},
    ),
    (
        "[某组] 某番 [01][1080p][生肉]",
        {"language": "无中文", "language_source": "raw", "is_raw": True},
    ),
    (
        "[某组] 某番 [01][1080p][无字幕]",
        {"language": "无中文", "language_source": "raw", "is_raw": True},
    ),
    (
        "[LoliHouse] Grand Blue S3 - 11 [WebRip 1080p HEVC-10bit AAC ASSx2].mkv",
        {"language": "繁体", "language_source": "inferred", "has_explicit_chinese": False},
    ),
    (
        "[Skymoon-Raws] 某番 - 12 [ViuTV][WEB-DL][CHT][1080p][AVC AAC]",
        {"language": "繁体", "language_source": "explicit", "has_explicit_chinese": True},
    ),
    (
        "[Nekomoe kissaten][某番][11][1080p][JPSC].mp4",
        {"language": "简日双语", "language_source": "explicit"},
    ),
]


@pytest.mark.parametrize("title,expected", RAW_CASES)
def test_language_default_and_raw_markers(title: str, expected: dict) -> None:
    """没写语言时默认繁体；写了 raw/生肉 才算无字幕；显式标记永远优先。"""
    parsed = parse_title(title)
    for field, value in expected.items():
        assert getattr(parsed, field) == value, f"{field}: {getattr(parsed, field)!r} != {value!r}"


def test_default_language_can_be_disabled() -> None:
    from mikan_health.core.taxonomy import Taxonomy

    tax = Taxonomy(default_language="")
    parsed = parse_title("[某组] 某番 [01][1080p]", tax)
    assert parsed.language == "无中文"
    assert parsed.language_source == "none"


def test_raw_marker_does_not_override_explicit_language() -> None:
    parsed = parse_title("[Skymoon-Raws] 某番 - 12 [WEB-DL][CHT][1080p][AVC AAC]")
    assert parsed.language == "繁体"
    assert parsed.is_raw is False
