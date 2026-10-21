"""发布变体求解：选出恰好覆盖每一集的最优变体组合。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from mikan_health.core.policy import Policy
from mikan_health.core.releases import contains_token
from mikan_health.core.taxonomy import DEFAULT_PACK_FILTER_PATTERNS, Taxonomy
from mikan_health.data.models import Release

BRACKET_TOKEN_RE = re.compile(r"[\[【]([^\]】]{1,80})[\]】]")

LANG_FAMILIES = ("simplified", "traditional", "dual_zh_jp")
SUBTYPE_FAMILIES = ("softsub", "hardsub")

MAX_VARIANTS = 16


@dataclass(frozen=True)
class Variant:
    """一个发布「变体」：语言 + 字幕类型 + 标记 token。

    Attributes:
        key: 变体标识，形如 "语言|字幕类型|token"。
        language: 语言标签。
        sub_type: 字幕类型；无则 None。
        token: 标题中的代表 token；无则 None。
        episodes: 该变体覆盖的集号（升序）。
    """

    key: str
    language: str
    sub_type: str | None
    token: str | None
    episodes: tuple[float, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        """变体的人类可读标签。"""
        base = self.language
        if self.sub_type:
            base = f"{base}·{self.sub_type}"
        return base if self.token else f"{base}·无标记"


@dataclass(frozen=True)
class ReleaseFilter:
    """发布筛选条件：must_contain / must_not_contain（可正则）。

    Attributes:
        must_contain: 必须命中的正则 / 子串；空串表示不限制。
        must_not_contain: 必须排除的正则 / 子串；空串表示不排除。
        use_regex: True 按正则匹配，否则按子串包含。
    """

    must_contain: str
    must_not_contain: str
    use_regex: bool = True

    def matches(self, title: str) -> bool:
        """判断标题是否满足筛选条件。"""
        if self.use_regex:
            if self.must_contain and re.search(self.must_contain, title) is None:
                return False
            return not (
                self.must_not_contain and re.search(self.must_not_contain, title) is not None
            )
        if self.must_contain and self.must_contain not in title:
            return False
        return not (self.must_not_contain and self.must_not_contain in title)


@dataclass
class EpisodeMatch:
    """某集在模拟中的命中情况。

    Attributes:
        episode: 集号。
        title: 命中的标题；未命中为空串。
        matches: 命中该集的发布条数。
        release: 恰好命中一条时的发布；多命中或未命中为 None。
    """

    episode: float
    title: str
    matches: int
    release: Release | None = None


@dataclass
class Simulation:
    """筛选方案套用到全部发布后的模拟结果。

    Attributes:
        episodes: 每个已出现集的命中情况。
        gaps: 漏集集号。
        multiple: 同集多命中的集号。
        extra_titles: 命中筛选条件的合集 / 特典标题。
    """

    episodes: list[EpisodeMatch] = field(default_factory=list)
    gaps: list[float] = field(default_factory=list)
    multiple: list[float] = field(default_factory=list)
    extra_titles: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """无缺集且无重复命中即为 True。"""
        return not self.gaps and not self.multiple

    def summary(self) -> dict:
        """把模拟结果汇总成可 JSON 化的字典。"""
        return {
            "episode_count": len(self.episodes),
            "gaps": self.gaps,
            "multiple": self.multiple,
            "extra_titles": self.extra_titles[:10],
            "ok": self.ok,
        }


@dataclass
class Solution:
    """一次求解结果：选中变体、筛选器与模拟。

    Attributes:
        variants: 选中的变体组合。
        all_variants: 全部候选变体。
        filter: 由选中变体构造的筛选器。
        simulation: 筛选器套用到全部发布后的模拟结果。
    """

    variants: tuple[Variant, ...]
    all_variants: tuple[Variant, ...]
    filter: ReleaseFilter
    simulation: Simulation


def extract_variants(
    releases: Iterable[Release], taxonomy: Taxonomy | None = None
) -> list[Variant]:
    """按 (语言, 字幕类型, token) 聚合发布，得到候选变体。

    Args:
        releases: 待聚合的发布记录。
        taxonomy: 语言/字幕类型词典；None 用默认。
    """
    tax = taxonomy or Taxonomy()
    groups: dict[str, dict] = {}

    for release in releases:
        if not release.is_episode or release.episode is None:
            continue
        key, language, sub_type, token = variant_identity(release, tax)
        bucket = groups.setdefault(
            key, {"language": language, "sub_type": sub_type, "token": token, "eps": set()}
        )
        bucket["eps"].add(release.episode)

    variants = [
        Variant(
            key=key,
            language=data["language"],
            sub_type=data["sub_type"],
            token=data["token"],
            episodes=tuple(sorted(data["eps"])),
        )
        for key, data in groups.items()
    ]
    variants.sort(key=lambda v: (-len(v.episodes), v.key))
    return variants


def variant_identity(
    release: Release, taxonomy: Taxonomy | None = None
) -> tuple[str, str, str | None, str | None]:
    """返回一条发布的变体标识 (key, language, sub_type, token)。

    Args:
        release: 单条发布记录。
        taxonomy: 语言/字幕类型词典；None 用默认。
    """
    tax = taxonomy or Taxonomy()
    token = pick_variant_token(release.title, tax, skip=release.subgroup_label)
    key = f"{release.language}|{release.sub_type or ''}|{token or ''}"
    return key, release.language, release.sub_type, token


def pick_variant_token(title: str, taxonomy: Taxonomy, skip: str | None = None) -> str | None:
    """从方括号 token 中挑出最能代表语言/字幕类型的那个。

    Args:
        title: 原始发布标题。
        taxonomy: 语言/字幕类型词典。
        skip: 需要跳过的 token（通常是字幕组名）；None 表示不跳过。
    """
    best: tuple[int, int, str] | None = None
    for token in bracket_tokens(title):
        if skip and token == skip:
            continue
        lang_hits = sum(
            1
            for family in LANG_FAMILIES
            for tag in getattr(taxonomy, family)
            if contains_token(token, tag)
        )
        sub_hits = sum(
            1
            for family in SUBTYPE_FAMILIES
            for tag in getattr(taxonomy, family)
            if contains_token(token, tag)
        )
        if not lang_hits and not sub_hits:
            continue
        tier = 2 if (lang_hits and sub_hits) else (1 if sub_hits else 0)
        candidate = (tier, len(token), token)
        if best is None or candidate > best:
            best = candidate
    return best[2] if best else None


def bracket_tokens(title: str) -> list[str]:
    """按出现顺序提取标题里所有方括号内的文本。"""
    return [match.group(1).strip() for match in BRACKET_TOKEN_RE.finditer(title)]


def solve(
    releases: Iterable[Release],
    policy: Policy,
    taxonomy: Taxonomy | None = None,
    episode_range: tuple[float, float] | None = None,
) -> Solution | None:
    """搜索能恰好覆盖每集的最优变体子集；无解返回 None。

    Args:
        releases: 候选发布记录。
        policy: 评估参数（用于变体评分）。
        taxonomy: 语言/字幕类型词典；None 用默认。
        episode_range: 只考虑的集号区间 (low, high)；None 表示全部。
    """
    tax = taxonomy or Taxonomy()
    all_releases = list(releases)
    episodes = [r for r in all_releases if r.is_episode and r.episode is not None]
    if episode_range is not None:
        low, high = episode_range
        episodes = [r for r in episodes if r.episode is not None and low <= r.episode <= high]
    if not episodes:
        return None

    variants = extract_variants(episodes, tax)
    if not variants or len(variants) > MAX_VARIANTS:
        return None

    presence: dict[float, set[str]] = {}
    for release in episodes:
        if release.episode is None:
            continue
        key = variant_identity(release, tax)[0]
        presence.setdefault(release.episode, set()).add(key)

    best: tuple[float, int, tuple[Variant, ...]] | None = None
    for subset in _subsets(variants):
        keys = {v.key for v in subset}
        if not _covers_exactly_once(presence, keys):
            continue
        score = sum(variant_score(v, policy) for v in subset) / len(subset)
        candidate = (score, -len(subset), subset)
        if best is None or candidate[:2] > best[:2]:
            best = candidate

    if best is None:
        return None

    chosen = best[2]
    release_filter = build_filter(chosen, variants)
    simulation = simulate(all_releases, release_filter)
    return Solution(
        variants=chosen,
        all_variants=tuple(variants),
        filter=release_filter,
        simulation=simulation,
    )


def variant_score(variant: Variant, policy: Policy) -> float:
    """变体的评分：语言分 + 内封/内嵌加分。

    Args:
        variant: 待评分的变体。
        policy: 评估参数（语言分与内封加分系数）。
    """
    score = policy.lang_score(variant.language)
    if variant.sub_type == "内封":
        score += policy.softsub_bonus
    elif variant.sub_type in ("内嵌", "内挂"):
        score += policy.softsub_bonus * policy.hardsub_bonus_ratio
    return score


def build_filter(chosen: tuple[Variant, ...], all_variants: list[Variant]) -> ReleaseFilter:
    """根据选中变体构造筛选器（含排除合集与其他 token）。

    Args:
        chosen: 选中的变体组合。
        all_variants: 全部候选变体，用于生成排除项。
    """
    chosen_tokens = [v.token for v in chosen if v.token]
    must_contain = "|".join(re.escape(token) for token in chosen_tokens)

    excluded: list[str] = []
    for variant in all_variants:
        if variant in chosen or not variant.token:
            continue
        if any(variant.token in token for token in chosen_tokens):
            continue
        excluded.append(re.escape(variant.token))

    excluded.extend(DEFAULT_PACK_FILTER_PATTERNS)

    return ReleaseFilter(
        must_contain=must_contain,
        must_not_contain="|".join(excluded),
        use_regex=bool(must_contain or excluded),
    )


def simulate(releases: Iterable[Release], release_filter: ReleaseFilter) -> Simulation:
    """把筛选器套用到全部发布，得到覆盖、缺集与重复命中情况。

    Args:
        releases: 全部发布记录（含合集/特典）。
        release_filter: 待套用的筛选器。
    """
    episode_set: set[float] = set()
    hits_by_episode: dict[float, list[Release]] = {}
    extras: list[str] = []

    for release in releases:
        if release.episode is None or release.is_pack or release.is_extra:
            if release_filter.matches(release.title):
                extras.append(release.title)
            continue
        episode_set.add(release.episode)
        if release_filter.matches(release.title):
            hits_by_episode.setdefault(release.episode, []).append(release)

    simulation = Simulation()
    for episode in sorted(episode_set):
        hits = hits_by_episode.get(episode, [])
        simulation.episodes.append(
            EpisodeMatch(
                episode=episode,
                title=hits[0].title if hits else "",
                matches=len(hits),
                release=hits[0] if len(hits) == 1 else None,
            )
        )
        if not hits:
            simulation.gaps.append(episode)
        elif len(hits) > 1:
            simulation.multiple.append(episode)

    simulation.extra_titles = extras
    return simulation


def _subsets(variants: list[Variant]):
    """枚举所有非空变体子集。"""
    total = 1 << len(variants)
    for mask in range(1, total):
        yield tuple(variants[i] for i in range(len(variants)) if mask & (1 << i))


def _covers_exactly_once(presence: dict[float, set[str]], keys: set[str]) -> bool:
    """每个已出现集都被恰好一个选中变体覆盖。

    Args:
        presence: 集号 -> 覆盖该集的变体 key 集合。
        keys: 当前选中变体的 key 集合。
    """
    return all(len(present & keys) == 1 for present in presence.values())
