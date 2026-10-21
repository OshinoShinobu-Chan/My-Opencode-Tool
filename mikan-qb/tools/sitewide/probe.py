"""快速验证全站评估计划的可行性（只读、约 20 请求，用于 SITEWIDE.md 第 9 节）。

用真实 Mikan / Bangumi 接口测量：季表可用性、番剧页截断程度、Expand 全量成本、
Bangumi 日程可用性与单请求耗时，并据此外推采集预算。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from mikan_health.core.airtime import build_schedule
from mikan_health.data.bangumi import subject_id_from_url
from mikan_qb.context import GlobalOptions, build_context
from mikan_qb.mikan import build_bangumi_client, build_source

PROBE_SEASONS = [(2017, "春"), (2017, "秋"), (2024, "春"), (2025, "夏"), (2026, "夏")]


def _timed(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> tuple[Any, float, str]:
    """调用 fn 并返回 (结果, 耗时秒, 错误文本)；异常时结果为 None。"""
    start = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        return result, time.perf_counter() - start, ""
    except Exception as exc:  # noqa: BLE001
        return None, time.perf_counter() - start, f"{type(exc).__name__}: {exc}"


def main() -> None:
    """跑一遍探测并把结论打印出来。"""
    ctx = build_context(GlobalOptions())
    source = build_source(ctx.config, ctx.env)
    bgm = build_bangumi_client(ctx.config, ctx.env)
    print(f"mikan={ctx.config.mikan.base_url}  rate={ctx.config.mikan.rate_limit_per_sec}/s")
    print(f"bgm.token={'yes' if bgm.has_token else 'no'}  proxy={bgm.proxy}\n")

    picks: list[tuple[int, str]] = []
    for year, season in PROBE_SEASONS:
        entries, elapsed, err = _timed(source.season, year, season)
        count = len(entries) if entries else 0
        print(f"[season] {year}{season}: n={count:>3}  {elapsed:5.1f}s  {err}")
        if entries:
            picks.append((entries[0].bangumi_id, f"{year}{season}"))

    print()
    total_seconds = 0.0
    requests = 0
    never_truncated = 0
    for bangumi_id, label in picks[:4]:
        detail, elapsed, err = _timed(source.bangumi, bangumi_id)
        total_seconds += elapsed
        requests += 1
        if not detail:
            print(f"[anime] {label} id={bangumi_id}: ERR {err}")
            continue
        per_group = [
            len([r for r in detail.releases if r.subtitle_group_id == s.subtitle_group_id])
            for s in detail.subgroups
        ]
        page_max = max(per_group) if per_group else 0
        print(
            f"[anime] {label} id={bangumi_id}: 组={len(detail.subgroups)} 本页每组行={per_group} "
            f"{elapsed:5.1f}s"
        )

        if not detail.subgroups:
            continue
        sid = detail.subgroups[0].subtitle_group_id
        full, elapsed, err = _timed(source.subgroup_releases, bangumi_id, sid)
        total_seconds += elapsed
        requests += 1
        full_n = len(full) if full else 0
        truncated = full_n > page_max
        never_truncated += 1 if not truncated else 0
        print(
            f"        expand sid={sid}: 全量行={full_n} 本页行={page_max} "
            f"截断={'是' if truncated else '否'}  {elapsed:5.1f}s  {err}"
        )

        subject_id = subject_id_from_url(detail.bangumi_tv_url)
        if subject_id is None:
            print("        bgm: 番剧页无 bgm.tv 链接")
            continue
        subject, elapsed, err = _timed(bgm.subject, subject_id)
        total_seconds += elapsed
        requests += 2
        if not subject:
            print(f"        bgm subject={subject_id}: ERR {err}")
            continue
        episodes, elapsed, err = _timed(bgm.episodes, subject_id)
        total_seconds += elapsed
        if not episodes:
            print(f"        bgm episodes={subject_id}: ERR {err}")
            continue
        schedule = build_schedule(subject, episodes)
        print(
            f"        bgm subject={subject_id}: 本篇日程={len(schedule)} 集 "
            f"total={schedule.total_episodes}  start={subject.air_date}"
        )

    if requests:
        avg = total_seconds / requests
        print(f"\n[耗时] 样本请求={requests}  平均={avg:.2f}s/请求（含限速 1 req/s）")
        for name, n in (("3年 S1+S2+S4", 700 + 700), ("10年 S1+S2+S4", 3000 + 3000)):
            print(f"  外推 {name}: ~{n * avg / 60:.0f} 分钟（不含 S3 深采）")


if __name__ == "__main__":
    main()
