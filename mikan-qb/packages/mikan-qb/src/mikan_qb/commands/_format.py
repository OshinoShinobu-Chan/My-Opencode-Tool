"""CLI 输出的格式化工具与发布记录过滤。"""

from __future__ import annotations

import time
from datetime import date, datetime

from mikan_health.data import Release
from mikan_qb.qb import walk_feeds


def _fmt_dt(value: str | None) -> str:
    """把 ISO 时间串截成「YYYY-MM-DD HH:MM」，空值返回 '-'。"""
    if not value:
        return "-"
    return value.replace("T", " ")[:16]


def _fmt_days(value: float | None) -> str:
    """把天数格式化为「N 天前」，空值返回 '(未知)'。"""
    return f"{value:.1f} 天前" if value is not None else "(未知)"


def _fmt_hours(value: float | None, day: bool = False) -> str:
    """把小时数格式化为小时或天，空值返回 '-'。"""
    if value is None:
        return "-"
    return f"{value / 24:.1f} 天" if day else f"{value:.1f} 小时"


def _fmt_lag(missing_eps: int, peer_gap_h: float | None) -> str:
    """把漏集数与落后小时数格式化为「缺 N 集/M 天」。"""
    if not missing_eps:
        return "-"
    if peer_gap_h is not None and peer_gap_h < 30 * 24:
        return f"缺 {missing_eps} 集/{peer_gap_h / 24:.1f} 天"
    return f"缺 {missing_eps} 集"


def _fmt_num(value: float | None) -> str:
    """把数值去掉多余小数并转为字符串，空值返回 '-'。"""
    if value is None:
        return "-"
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _age_hours(timestamp: float | None) -> float | None:
    """把 Unix 时间戳换算为距今小时数（保留 2 位），空值原样返回。"""
    if timestamp is None:
        return None
    return round((time.time() - timestamp) / 3600, 2)


def _fmt_age(age: float | None) -> str:
    """把距今小时数格式化为「N 小时前」，空值返回 '(空)'。"""
    if age is None:
        return "(空)"
    return f"{age} 小时前"


def _filter_releases(
    releases: list[Release],
    since: str | None,
    ep: float | None,
    lang: str | None,
    include_pack: bool,
) -> list[Release]:
    """按日期、集数、语言与合集开关过滤并排序发布记录。

    Args:
        releases: 原始发布记录列表。
        since: 起始日期（YYYY-MM-DD）；None 表示不限。
        ep: 仅保留该集；None 表示不限。
        lang: 仅保留该语言；None 表示不限。
        include_pack: 是否包含合集与特典条目。
    """
    threshold = None
    if since:
        threshold = datetime.combine(date.fromisoformat(since), datetime.min.time())

    selected: list[Release] = []
    for release in releases:
        if not include_pack and (release.is_pack or release.is_extra):
            continue
        if ep is not None and release.episode != ep:
            continue
        if lang and release.language != lang:
            continue
        if (
            threshold is not None
            and release.published_at is not None
            and release.published_at < threshold
        ):
            continue
        selected.append(release)

    selected.sort(key=lambda r: (r.published_at or datetime.min, r.subtitle_group_id), reverse=True)
    return selected


def _walk_feeds(node: object, prefix: str = "") -> list[dict]:
    """遍历 qB RSS 树，返回扁平化的节点列表。

    Args:
        node: RSS 树节点。
        prefix: 当前节点的路径前缀。
    """
    return walk_feeds(node, prefix)
