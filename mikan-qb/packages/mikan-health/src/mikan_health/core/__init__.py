"""core 包对外导出的公共 API（评分、解析、风险与变体等）。"""

from mikan_health.core.health import HealthInputs, analyze
from mikan_health.core.policy import Policy, Weights, load_profile, profile_names
from mikan_health.core.releases import ParsedTitle, parse_title
from mikan_health.core.risk import RiskAssessment, WorkRecord, assess_risk
from mikan_health.core.taxonomy import Taxonomy
from mikan_health.core.variants import (
    EpisodeMatch,
    ReleaseFilter,
    Simulation,
    Solution,
    Variant,
    extract_variants,
    solve,
)

__all__ = [
    "EpisodeMatch",
    "HealthInputs",
    "ParsedTitle",
    "Policy",
    "ReleaseFilter",
    "RiskAssessment",
    "Simulation",
    "Solution",
    "Taxonomy",
    "Variant",
    "Weights",
    "WorkRecord",
    "analyze",
    "assess_risk",
    "extract_variants",
    "load_profile",
    "parse_title",
    "profile_names",
    "solve",
]
