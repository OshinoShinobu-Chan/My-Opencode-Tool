"""放送日程与集号锚定：把 Mikan 集号归一到 1-based ordinal。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from mikan_health.data.bangumi import BangumiEpisodes, BangumiSubject


@dataclass
class Schedule:
    """Bangumi 提供的精确放送日程；`airdates[k]` 是第 k+1 个「本篇」的放送日期（0-based）。

    与 Mikan 集号解耦：Mikan 可能是绝对编号（如 29-38），这里只关心「第几集」。

    Attributes:
        subject_id: Bangumi 条目 id。
        airdates: 本篇各集放送日期，按集序排列（0-based）。
        total_episodes: 总集数；未知为 None。
        weekday: 放送星期（来自 infobox 的中文）；未知为 None。
        name_cn: 中文名。
        name: 原名。
    """

    subject_id: int
    airdates: list[date] = field(default_factory=list)
    total_episodes: int | None = None
    weekday: str | None = None
    name_cn: str = ""
    name: str = ""

    def __len__(self) -> int:
        return len(self.airdates)

    def date_of_ordinal(self, ordinal: int) -> date | None:
        """返回第 ordinal 集（1-based）的放送日期；超出范围返回 None。

        Args:
            ordinal: 集号，1-based。
        """
        if 1 <= ordinal <= len(self.airdates):
            return self.airdates[ordinal - 1]
        return None

    def aired_count(self, now: datetime) -> int:
        """now 当天之前（含当天）已播出的集数。

        Args:
            now: 计算基准时间。
        """
        today = now.date()
        return sum(1 for aired in self.airdates if aired <= today)

    def next_airdate(self, now: datetime) -> date | None:
        """今天之后最近一次放送日期；没有则返回 None。

        Args:
            now: 计算基准时间。
        """
        today = now.date()
        for aired in self.airdates:
            if aired > today:
                return aired
        return None


def build_schedule(subject: BangumiSubject, episodes: BangumiEpisodes) -> Schedule:
    """由 Bangumi 条目与剧集列表构造 Schedule。

    Args:
        subject: Bangumi 条目。
        episodes: 该条目的剧集列表。
    """
    airdates = episodes.airdates()
    total = subject.total_episodes or subject.eps or (len(airdates) or None)
    return Schedule(
        subject_id=subject.id,
        airdates=airdates,
        total_episodes=total,
        weekday=subject.broadcast_weekday,
        name_cn=subject.name_cn,
        name=subject.name,
    )


@dataclass
class EpisodeAnchor:
    """把某个字幕组的 Mikan 集号映射到 1-based 的「第几集」（ordinal）。

    Attributes:
        subgroup_id: 字幕组 id。
        ordinal_of: Mikan 集号 -> 1-based ordinal（第几集）。
    """

    subgroup_id: int
    ordinal_of: dict[float, int] = field(default_factory=dict)

    def ordinals(self) -> list[int]:
        """已映射的 ordinal 升序列表。"""
        return sorted(self.ordinal_of.values())


def build_anchor(
    subgroup_id: int, episode_numbers: list[float], schedule: Schedule
) -> EpisodeAnchor:
    """按「集号升序 = 播出顺序」把集号映射到第 1..n 集（ordinal）。

    绝对值编号（29-38）与相对编号（1-10）归一到同一套 ordinal，避免跨组误报。

    Args:
        subgroup_id: 字幕组 id。
        episode_numbers: 该组的 Mikan 集号列表。
        schedule: 该番的精确放送日程。
    """
    numbers = sorted({number for number in episode_numbers})
    anchor = EpisodeAnchor(subgroup_id=subgroup_id)
    for index, number in enumerate(numbers):
        anchor.ordinal_of[number] = index + 1
    return anchor
