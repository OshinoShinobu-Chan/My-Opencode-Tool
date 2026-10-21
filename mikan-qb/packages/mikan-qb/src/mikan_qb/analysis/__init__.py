"""健康度分析子包，转发 health 模块的公开接口。"""

from mikan_qb.analysis.health import (
    DEEP_DEFAULT,
    WORKS_DEFAULT,
    HistoryResult,
    build_report,
    build_subgroup_report,
    collect_history,
    load_schedule,
)

__all__ = [
    "DEEP_DEFAULT",
    "WORKS_DEFAULT",
    "HistoryResult",
    "build_report",
    "build_subgroup_report",
    "collect_history",
    "load_schedule",
]
