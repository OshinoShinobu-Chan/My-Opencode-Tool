"""评估参数 Policy：从 TOML profile 解析阈值、权重与惩罚系数。"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from mikan_health.paths import profiles_dir

Mode = Literal["fast", "normal"]
UnknownTierPolicy = Literal["conservative", "lenient"]


class RiskTierThresholds(BaseModel):
    """风险分级阈值：low/high 边界。

    Attributes:
        low: low 与 mid 的分界（风险值 < low 为 low）。
        high: mid 与 high 的分界（风险值 <= high 为 mid，否则 high）。
    """

    low: float = 0.15
    high: float = 0.35

    @field_validator("high")
    @classmethod
    def _ordered(cls, value: float, info) -> float:
        low = info.data.get("low", 0.15)
        if value <= low:
            raise ValueError("risk_tier.high 必须大于 risk_tier.low")
        return value


class Weights(BaseModel):
    """速度/语言/稳定性三项评分权重。

    Attributes:
        speed: 速度分权重。
        lang: 语言匹配分权重。
        stability: 稳定性分权重。
    """

    speed: float
    lang: float = 0.0
    stability: float

    @field_validator("speed", "stability")
    @classmethod
    def _non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("权重不能为负")
        return value

    def total(self) -> float:
        """三项权重之和。"""
        return self.speed + self.lang + self.stability


class ProfileMeta(BaseModel):
    """profile 的元信息（名称、模式、说明）。

    Attributes:
        name: profile 名称（默认取文件名）。
        mode: 运行模式 fast / normal。
        description: 人类可读说明。
    """

    name: str
    mode: Mode = "normal"
    description: str = ""


class Policy(BaseModel):
    """一次评估所用的全部阈值、权重与惩罚系数。

    Attributes:
        profile: profile 元信息（名称 / 模式 / 说明）。
        speed_T_hours: 速度衰减时间常数（小时），越大越宽容。
        stall_lag_days: 落后同番最快组多少天算「停滞」。
        silent_days: 全组多少天无发布视为沉默。
        drop_gap_days: 多少天无发布才纳入弃番判定。
        history_works: 历史深查回看的作品数。
        catchup_interval_hours: 相邻集间隔小于该值视为「攒集连发」。
        speed_gain_threshold: 挑战者评分需高出推荐分多少才触发人工确认。
        high_risk_veto: 是否直接剔除高风险（tier C）候选。
        unknown_tier_policy: 置信度低时的处理；conservative 降为 U，lenient 保留 A。
        risk_tier: 风险值到 low/mid/high 的分级阈值。
        weights: 总分三项权重。
        lang_scores: 语言标签 -> 匹配分。
        softsub_bonus: 内封字幕加分。
        hardsub_bonus_ratio: 内嵌相对内封的加分比例。
        speed_abs_weight: 速度分中绝对延迟部分的权重。
        speed_rel_weight: 速度分中相对延迟部分的权重。
        jitter_scale_h: 稳定性归一尺度（小时）。
        catchup_penalty_weeks: 触发攒集惩罚的攒集次数阈值。
        catchup_speed_penalty: 攒集时速度分乘数。
        stall_ratio_threshold: 停滞比阈值。
        stall_speed_penalty: 停滞时速度分乘数。
        min_interval_hours: 计算停滞比时发布间隔的下限（小时）。
        stall_penalty: 疑似弃坑时风险值乘数。
        silent_penalty: 整组停更时风险值乘数。
        inactive_penalty: 未接新番时风险值乘数。
        clean_history_bonus: 历史无弃番时风险值乘数。
        fast_tie_prefers_stability: fast 模式速度并列时是否由稳定性决定（见 13.3）。
    """

    profile: ProfileMeta
    speed_T_hours: float = 48
    stall_lag_days: float = 10
    silent_days: float = 21
    drop_gap_days: float = 14
    history_works: int = 8
    catchup_interval_hours: float = 48
    speed_gain_threshold: float = 0.15
    high_risk_veto: bool = True
    unknown_tier_policy: UnknownTierPolicy = "conservative"
    risk_tier: RiskTierThresholds = Field(default_factory=RiskTierThresholds)
    weights: Weights
    lang_scores: dict[str, float] = Field(default_factory=dict)
    softsub_bonus: float = 0.05
    hardsub_bonus_ratio: float = 0.25
    # 速度分公式与惩罚系数
    speed_abs_weight: float = 0.4
    speed_rel_weight: float = 0.6
    jitter_scale_h: float = 48.0
    catchup_penalty_weeks: int = 2
    catchup_speed_penalty: float = 0.85
    stall_ratio_threshold: float = 1.5
    stall_speed_penalty: float = 0.9
    min_interval_hours: float = 24.0
    # 停更风险倍率
    stall_penalty: float = 2.0
    silent_penalty: float = 1.5
    inactive_penalty: float = 1.3
    clean_history_bonus: float = 0.7
    # fast 模式并列裁决：True 时稳定性优先（唯一权重来源），False 时完全按速度
    fast_tie_prefers_stability: bool = True

    @property
    def mode(self) -> Mode:
        """profile 的运行模式（fast/normal）。"""
        return self.profile.mode

    def lang_score(self, label: str | None) -> float:
        """查询某语言标签的分值，未知标签为 0。"""
        if label is None:
            return 0.0
        return self.lang_scores.get(label, 0.0)

    def risk_tier_of(self, risk: float) -> Literal["low", "mid", "high"]:
        """把 0-1 的风险值映射为 low/mid/high。

        Args:
            risk: 0-1 风险值。
        """
        if risk < self.risk_tier.low:
            return "low"
        if risk <= self.risk_tier.high:
            return "mid"
        return "high"


def load_profile(name_or_path: str, search_dir: Path | None = None) -> Policy:
    """从名称或 .toml 路径加载并校验 Policy。

    Args:
        name_or_path: profile 名称或 .toml 路径。
        search_dir: 搜索目录；None 用默认 profiles 目录。
    """
    path = _resolve_profile_path(name_or_path, search_dir)
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return _parse(raw, path)


def profile_names(search_dir: Path | None = None) -> list[str]:
    """返回搜索目录下所有可用 profile 的名称（按字母序）。

    Args:
        search_dir: 搜索目录；None 用默认 profiles 目录。
    """
    directory = search_dir or profiles_dir()
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.toml"))


def _resolve_profile_path(name_or_path: str, search_dir: Path | None) -> Path:
    """把 profile 名称解析为存在的 .toml 路径，找不到则抛 FileNotFoundError。

    Args:
        name_or_path: profile 名称或 .toml 路径。
        search_dir: 搜索目录；None 用默认 profiles 目录。
    """
    candidate = Path(name_or_path)
    if candidate.suffix == ".toml" and candidate.is_file():
        return candidate
    directory = search_dir or profiles_dir()
    path = directory / f"{name_or_path}.toml"
    if not path.is_file():
        available = ", ".join(profile_names(search_dir)) or "(无)"
        raise FileNotFoundError(f"找不到 profile：{name_or_path}（可用：{available}）")
    return path


def _parse(raw: dict, path: Path) -> Policy:
    """把 TOML 原始字典补全 profile.name 后校验为 Policy。

    Args:
        raw: TOML 解析出的原始字典。
        path: profile 文件路径，用于取默认名称。
    """
    profile_raw = dict(raw.get("profile", {}))
    profile_raw.setdefault("name", path.stem)
    merged = {
        "profile": profile_raw,
        **{k: v for k, v in raw.items() if k != "profile"},
    }
    return Policy.model_validate(merged)
