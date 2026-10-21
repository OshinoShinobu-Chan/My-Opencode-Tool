"""定期清理：陈旧订阅计划、终态换组任务与缓存数据的自动/手动清理。"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from mikan_qb.paths import data_dir, jobs_dir, plans_dir
from mikan_qb.replace.job import PHASE_ABORTED, PHASE_DONE, ReplaceError, load_job
from mikan_qb.state import State

PLAN_NAME = re.compile(r"^(\d+)-(\d+)\.json$")
DEFAULT_PLAN_DAYS = 30.0
DEFAULT_JOB_DAYS = 30.0
DEFAULT_CACHE_DAYS = 7.0


@dataclass
class CleanupPlan:
    """一次清理的候选集合（dry-run 与 --apply 共用）。

    Attributes:
        plans: 待删的陈旧订阅计划文件。
        jobs: 待删的终态换组任务文件。
        cache: 待删的缓存文件（data/ 下按 mtime 判定）。
        kept_plans: 因仍在用 / 未过期而保留的计划数（仅计数）。
    """

    plans: list[Path] = field(default_factory=list)
    jobs: list[Path] = field(default_factory=list)
    cache: list[Path] = field(default_factory=list)
    kept_plans: int = 0

    def all_files(self) -> list[Path]:
        """返回本计划涉及的全部待删文件。"""
        return [*self.plans, *self.jobs, *self.cache]


def _is_stale(path: Path, days: float, now: float) -> bool:
    """文件是否早于 days 天前修改（days <= 0 视为不按时间清理）。"""
    if days <= 0:
        return False
    return (now - path.stat().st_mtime) > days * 86400


def scan_cleanup(
    state: State,
    plan_days: float = DEFAULT_PLAN_DAYS,
    job_days: float = DEFAULT_JOB_DAYS,
    cache_days: float = DEFAULT_CACHE_DAYS,
    include_cache: bool = True,
    keep_managed: bool = True,
    now: float | None = None,
) -> CleanupPlan:
    """扫描 pans/jobs/data，返回可清理项（不删除）。

    Args:
        state: 托管清单，用于保护仍在用的计划。
        plan_days: 计划文件保留天数；<=0 表示不清计划。
        job_days: 终态 job 保留天数；<=0 表示不清 job。
        cache_days: 缓存文件保留天数；<=0 表示不清缓存。
        include_cache: 是否把 data/ 缓存纳入清理范围。
        keep_managed: 是否保护仍被托管清单引用的计划。
        now: 参考时间戳；None 用当前时间。
    """
    moment = now if now is not None else time.time()
    managed = {(e.bangumi_id, e.subgroup_id) for e in state.entries} if keep_managed else set()
    plan = CleanupPlan()

    plans_root = plans_dir()
    if plans_root.is_dir():
        for path in sorted(plans_root.glob("*.json")):
            match = PLAN_NAME.match(path.name)
            if keep_managed and match and (int(match[1]), int(match[2])) in managed:
                plan.kept_plans += 1
                continue
            if _is_stale(path, plan_days, moment):
                plan.plans.append(path)

    jobs_root = jobs_dir()
    if jobs_root.is_dir() and job_days > 0:
        for path in sorted(jobs_root.glob("*.json")):
            if path.stem == "state":
                continue
            if not _is_stale(path, job_days, moment):
                continue
            try:
                job = load_job(path.stem)
            except ReplaceError:
                continue
            if job.phase in (PHASE_DONE, PHASE_ABORTED):
                plan.jobs.append(path)

    data_root = data_dir()
    if include_cache and data_root.is_dir() and cache_days > 0:
        for path in sorted(data_root.rglob("*")):
            if path.is_file() and _is_stale(path, cache_days, moment):
                plan.cache.append(path)

    return plan


def apply_cleanup(plan: CleanupPlan) -> dict[str, int]:
    """删除计划中的文件，返回各分类实际删除数（已消失的忽略）。

    Args:
        plan: 待执行的清理计划。
    """
    counts = {"plans": 0, "jobs": 0, "cache": 0}
    for name, paths in (("plans", plan.plans), ("jobs", plan.jobs), ("cache", plan.cache)):
        for path in paths:
            try:
                path.unlink()
                counts[name] += 1
            except FileNotFoundError:
                continue
    return counts


def auto_cleanup(
    state: State,
    plan_days: float = DEFAULT_PLAN_DAYS,
    job_days: float = DEFAULT_JOB_DAYS,
) -> list[Path]:
    """写命令成功后的顺带清理：只删陈旧计划与终态 job，不碰缓存。

    Args:
        state: 当前托管清单，用于保护仍在用的计划。
        plan_days: 计划文件保留天数。
        job_days: 终态 job 保留天数。
    """
    plan = scan_cleanup(state, plan_days, job_days, include_cache=False)
    apply_cleanup(plan)
    return plan.all_files()


__all__ = [
    "CleanupPlan",
    "apply_cleanup",
    "auto_cleanup",
    "scan_cleanup",
]
