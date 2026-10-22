from __future__ import annotations

from pathlib import Path

from mikan_health.core.policy import load_profile
from mikan_health.core.releases import parse_title, subgroup_language_override
from mikan_health.core.taxonomy import Taxonomy
from mikan_health.core.variants import (
    ReleaseFilter,
    extract_variants,
    solve,
)
from mikan_health.data.models import Release
from mikan_health.data.parse import _release_from, parse_episode_table

FIXTURES = Path(__file__).parent / "fixtures"
TAX = Taxonomy()


def mk(title: str, subgroup_id: int = 1, bangumi_id: int = 1) -> Release:
    parsed = parse_title(title, TAX)
    return Release(
        bangumi_id=bangumi_id,
        subtitle_group_id=subgroup_id,
        title=title,
        episode=parsed.episode,
        episode_raw=parsed.episode_raw,
        is_pack=parsed.is_pack,
        is_extra=parsed.is_extra,
        language=parsed.language,
        sub_type=parsed.sub_type,
        is_dual_zh_jp=parsed.is_dual_zh_jp,
        subgroup_label=parsed.subgroup_label,
    )


def sakurato_releases() -> list[Release]:
    html = (FIXTURES / "expanded_203_full.html").read_text(encoding="utf-8")
    return parse_episode_table(html, 3995, 203, TAX, source="expand")


def test_extract_variants_from_real_releases() -> None:
    variants = extract_variants(sakurato_releases(), TAX)
    tokens = {v.token for v in variants}
    assert tokens == {"简繁内封", "简体内嵌", "繁体内嵌"}
    assert all(len(v.episodes) == 12 for v in variants)


def test_normal_mode_prefers_softsub_both_languages() -> None:
    policy = load_profile("personal-normal")
    solution = solve(sakurato_releases(), policy, TAX)
    assert solution is not None
    assert [v.token for v in solution.variants] == ["简繁内封"]
    assert solution.filter.must_contain == "简繁内封"
    assert "简体内嵌" in solution.filter.must_not_contain
    assert "繁体内嵌" in solution.filter.must_not_contain
    assert solution.simulation.ok
    assert len(solution.simulation.episodes) == 12
    assert solution.simulation.gaps == []
    assert solution.simulation.multiple == []
    assert all(m.matches == 1 for m in solution.simulation.episodes)


def test_simulation_returns_one_release_per_episode() -> None:
    policy = load_profile("personal-normal")
    solution = solve(sakurato_releases(), policy, TAX)
    assert solution is not None
    episodes = {m.episode: m.release for m in solution.simulation.episodes}
    assert set(episodes) == {float(n) for n in range(1, 13)}
    assert all(r is not None and r.torrent_url for r in episodes.values())


def test_midseason_format_change_uses_two_variants() -> None:
    releases = []
    for ep in range(1, 7):
        releases.append(mk(f"[某组] 番名 [0{ep}][1080p][简体内嵌]"))
    for ep in range(7, 13):
        releases.append(mk(f"[某组] 番名 [{ep}][1080p][简繁内封]"))

    solution = solve(releases, load_profile("personal-normal"), TAX)
    assert solution is not None
    assert {v.token for v in solution.variants} == {"简体内嵌", "简繁内封"}
    assert solution.simulation.ok
    assert len(solution.simulation.episodes) == 12


def test_pack_is_rejected_by_filter() -> None:
    pack_title = "[某组] 番名 [01-12][1080p][简体内嵌]"
    releases = [mk("[某组] 番名 [01][1080p][简体内嵌]"), mk(pack_title)]

    solution = solve(releases, load_profile("personal-normal"), TAX)
    assert solution is not None
    assert solution.simulation.ok
    assert len(solution.simulation.episodes) == 1
    assert solution.filter.matches(pack_title) is False
    assert "01-12" not in [m.title for m in solution.simulation.episodes]


def test_entry_without_parseable_episode_is_reported_as_extra() -> None:
    releases = [
        mk("[某组] 番名 [01][1080p][简体内嵌]"),
        mk("[某组] 番名 [SP01][1080p][简体内嵌]"),
    ]
    solution = solve(releases, load_profile("personal-normal"), TAX)
    assert solution is not None
    assert len(solution.simulation.episodes) == 1
    assert len(solution.simulation.extra_titles) == 1
    assert "SP01" in solution.simulation.extra_titles[0]


def test_pack_exclusion_works_without_subtype() -> None:
    releases = [mk("[某组] 番名 [03][1080p][CHT]")]
    releases.append(mk("[某组] 番名 [01-12][1080p][CHT]"))
    solution = solve(releases, load_profile("personal-normal"), TAX)
    assert solution is not None
    assert solution.simulation.ok
    assert solution.filter.must_contain == "CHT"


def test_unsolvable_when_variants_split_and_overlap() -> None:
    releases = [
        mk("[某组] 番名 [01][1080p][简体内嵌]"),
        mk("[某组] 番名 [02][1080p][繁体内嵌]"),
        mk("[某组] 番名 [03][1080p][简体内嵌]"),
        mk("[某组] 番名 [03][1080p][繁体内嵌]"),
    ]
    assert solve(releases, load_profile("personal-normal"), TAX) is None


def test_resolution_duplicates_keep_highest_quality() -> None:
    releases = [
        mk("[某组] 番名 [01][1080p][简体内嵌]"),
        mk("[某组] 番名 [01][720p][简体内嵌]"),
    ]
    solution = solve(releases, load_profile("personal-normal"), TAX)
    assert solution is not None
    assert solution.simulation.ok is True
    assert solution.simulation.multiple == []
    assert "1080p" in solution.filter.must_contain
    assert "720p" in solution.filter.must_not_contain


def test_version_duplicates_keep_latest_version() -> None:
    releases = [
        mk("[某组] 番名 [01v2][1080p][简日内嵌]"),
        mk("[某组] 番名 [01v3][1080p][简日内嵌]"),
        mk("[某组] 番名 [02][1080p][简日内嵌]"),
        mk("[某组] 番名 [03][1080p][简日内嵌]"),
    ]
    solution = solve(releases, load_profile("personal-normal"), TAX)
    assert solution is not None
    assert solution.simulation.ok is True
    assert solution.simulation.multiple == []
    assert "v(?:2)" in solution.filter.must_not_contain


def test_edition_duplicates_prefer_uncensored() -> None:
    releases = []
    for ep in range(1, 4):
        releases.append(mk(f"[某组] 番名 [{ep:02d}][1080p][简／繁][TV版]"))
        releases.append(mk(f"[某组] 番名 [{ep:02d}][1080p][简／繁][完全无修版]"))
    solution = solve(releases, load_profile("personal-normal"), TAX)
    assert solution is not None
    assert solution.simulation.ok is True
    assert "无修" in solution.filter.must_contain
    assert "TV版" in solution.filter.must_not_contain


def test_source_duplicates_prefer_baha() -> None:
    releases = []
    for ep in range(1, 4):
        releases.append(mk(f"[黒ネズミたち] 番名 - {ep:02d} (ABEMA 1920x1080 AVC AAC MKV)"))
        releases.append(mk(f"[黒ネズミたち] 番名 - {ep:02d} (Baha 1920x1080 AVC AAC MP4)"))
    solution = solve(releases, load_profile("personal-normal"), TAX)
    assert solution is not None
    assert solution.simulation.ok is True
    assert "Baha" in solution.filter.must_contain
    assert "ABEMA" in solution.filter.must_not_contain


def test_whitelist_overrides_unmarked_subgroup_language() -> None:
    tax = Taxonomy(subgroup_languages={"615": "繁体", "ani": "繁体"})
    assert subgroup_language_override(tax, 615, "黒ネズミたち") == "繁体"
    assert subgroup_language_override(tax, 999, "Ani") == "繁体"
    assert subgroup_language_override(tax, 999, "某组") is None

    title = "[黒ネズミたち] 铁旅！meet with 铁道少女 - 02 (ABEMA 1920x1080 AVC AAC MKV)"
    parsed = parse_title(title, tax)
    release = _release_from(
        parsed,
        bangumi_id=1,
        subtitle_group_id=615,
        title=title,
        source="rss",
        taxonomy=tax,
    )
    assert release.language == "繁体"
    assert release.language_source == "override"
    assert release.has_explicit_chinese is True


def test_language_preference_chain() -> None:
    policy = load_profile("personal-normal")
    releases = []
    for ep in range(1, 4):
        releases.append(mk(f"[某组] 番名 [0{ep}][1080p][简日双语]"))
        releases.append(mk(f"[某组] 番名 [0{ep}][1080p][简体内嵌]"))
        releases.append(mk(f"[某组] 番名 [0{ep}][1080p][繁体内嵌]"))
    solution = solve(releases, policy, TAX)
    assert solution is not None
    assert solution.variants[0].token == "简日双语"


def test_fast_mode_ignores_language_but_keeps_softsub() -> None:
    policy = load_profile("personal-fast")
    releases = []
    for ep in range(1, 4):
        releases.append(mk(f"[某组] 番名 [0{ep}][1080p][繁体内嵌]"))
        releases.append(mk(f"[某组] 番名 [0{ep}][1080p][简体内嵌]"))
        releases.append(mk(f"[某组] 番名 [0{ep}][1080p][简繁内封]"))
    solution = solve(releases, policy, TAX)
    assert solution is not None
    assert solution.variants[0].sub_type == "内封"


def test_no_episode_releases_returns_none() -> None:
    releases = [mk("[某组] 番名 [01-12][1080p][简体内嵌]")]
    assert solve(releases, load_profile("personal-normal"), TAX) is None


def test_filter_regex_does_not_break_on_special_chars() -> None:
    release_filter = ReleaseFilter(must_contain=r"简繁内封", must_not_contain=r"简体内嵌|繁体内嵌")
    assert release_filter.matches("[桜都字幕组] 番名 [12][1080P][简繁内封]") is True
    assert release_filter.matches("[桜都字幕组] 番名 [12][1080P][简体内嵌]") is False


def test_filter_without_positive_token_matches_all_then_negates() -> None:
    release_filter = ReleaseFilter(must_contain="", must_not_contain=r"简体内嵌|繁体内嵌")
    assert release_filter.matches("[某组] 番名 [01] (CR 1920x1080 AVC AAC MKV)") is True
    assert release_filter.matches("[某组] 番名 [01][简体内嵌]") is False
