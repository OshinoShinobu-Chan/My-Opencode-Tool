"""字幕组评分与停更巡检的共享分析辅助。"""

from __future__ import annotations

from datetime import datetime

from mikan_health.data import BangumiDetail, MikanError, Release
from mikan_qb.analysis import build_report
from mikan_qb.commands._format import _fmt_num
from mikan_qb.context import AppContext


def _policy_for(app_ctx: AppContext, mode: str):
    """按模式加载分析 profile。

    Args:
        app_ctx: 应用上下文（配置与环境）。
        mode: 分析模式，fast 或 normal。
    """
    from mikan_qb.rules.plan import _load_policy

    return _load_policy(app_ctx.config, mode)


def _ranking_payload(ranking, detail: BangumiDetail) -> dict:
    """把 Ranking 与番剧详情转为可序列化 / 渲染的字典。

    Args:
        ranking: 字幕组评分排名结果。
        detail: 番剧详情。
    """

    def one(health) -> dict:
        """把单个候选字幕组的健康度转为可序列化字典。

        Args:
            health: 单个候选字幕组的健康度。
        """
        metrics = health.metrics
        return {
            "subgroup_id": health.subgroup_id,
            "name": health.name,
            "tier": health.tier,
            "risk": health.risk.level,
            "risk_label": (
                f"{health.risk.level}"
                + (f" {health.risk.risk:.2f}" if health.risk.risk is not None else "")
            ),
            "confidence": health.risk.confidence,
            "score": health.score,
            "speed_score": health.speed_score,
            "stability": health.stability,
            "lang_match": health.lang_match,
            "delay_p50_h": metrics.speed.delay_p50_h,
            "rel_p50_h": metrics.speed.rel_p50_h,
            "jitter_h": metrics.speed.jitter_h,
            "peer_gap_h": metrics.coverage.peer_gap_h,
            "missing_vs_peer": metrics.coverage.missing_vs_peer,
            "coverage_ratio": round(metrics.coverage.coverage_ratio, 3),
            "episode_count": metrics.episode_count,
            "languages": list(metrics.languages),
            "sub_types": list(metrics.sub_types),
            "evidence": health.risk.evidence + health.evidence,
            "line": f"{health.name}（sid {health.subgroup_id}，tier {health.tier}）",
        }

    return {
        "bangumi_id": detail.bangumi_id,
        "title": detail.title,
        "mode": ranking.mode,
        "candidates": [one(h) for h in ranking.eligible],
        "rejected": [one(h) for h in ranking.rejected],
        "recommendation": one(ranking.recommendation) if ranking.recommendation else None,
        "decision_required": ranking.decision_required,
        "decision_options": [one(h) for h in ranking.decision_options],
        "warnings": ranking.warnings,
    }


def _preview_one(
    app_ctx: AppContext,
    subgroup_id: int,
    name: str,
    releases: list[Release],
    mode: str,
) -> dict:
    """对单个字幕组求解过滤规则并返回模拟摘要。

    Args:
        app_ctx: 应用上下文（配置与环境）。
        subgroup_id: 字幕组 id。
        name: 字幕组名称。
        releases: 该组的发布记录列表。
        mode: 分析模式，fast 或 normal。
    """
    from mikan_health.core.variants import solve
    from mikan_qb.rules.plan import _load_policy

    policy = _load_policy(app_ctx.config, mode)
    solution = solve(releases, policy, app_ctx.config.taxonomy)
    base = {
        "subgroup_id": subgroup_id,
        "subgroup_name": name,
        "release_count": len(releases),
    }
    if solution is None:
        return {
            **base,
            "valid": False,
            "reason": "无解：该组发布格式无法用一条规则精确覆盖",
            "variant_label": None,
            "episode_count": 0,
            "gaps": [],
            "multiple": [],
            "must_contain": "",
            "must_not_contain": "",
            "extras": 0,
        }
    return {
        **base,
        "valid": solution.simulation.ok,
        "reason": "" if solution.simulation.ok else "存在漏集或多命中",
        "variant_label": " + ".join(v.label for v in solution.variants),
        "episode_count": len(solution.simulation.episodes),
        "gaps": solution.simulation.gaps,
        "multiple": solution.simulation.multiple,
        "must_contain": solution.filter.must_contain,
        "must_not_contain": solution.filter.must_not_contain,
        "extras": len(solution.simulation.extra_titles),
    }


def _audit_one(
    app_ctx: AppContext,
    source,
    policy,
    bangumi_id: int,
    subgroup_id: int | None,
    title: str,
    stale_days: float,
    deep: int,
    works: int | None,
    page_only: bool,
    refresh: bool,
    tracked: bool,
    bgm=None,
) -> dict:
    """对单部番剧生成一条巡检结论。

    Args:
        app_ctx: 应用上下文（配置与环境）。
        source: Mikan 数据源。
        policy: 分析策略 profile。
        bangumi_id: 番剧 id。
        subgroup_id: 指定字幕组 id；None 表示用推荐组。
        title: 番剧标题；空串表示稍后回填。
        stale_days: 落后多少天算异常。
        deep: 历史深查前 N 名；0 表示不深查。
        works: 历史深查回看的作品数；None 用 profile 默认。
        page_only: 是否只用番剧页窗口数据。
        refresh: 是否忽略缓存强制重新抓取。
        tracked: 该番剧是否在托管清单中。
        bgm: Bangumi 客户端；None 表示不启用。
    """
    base = {"bangumi_id": bangumi_id, "title": title, "flags": []}
    try:
        ranking, detail = build_report(
            source,
            bangumi_id,
            policy,
            app_ctx.config.taxonomy,
            deep=deep,
            works=works,
            page_only=page_only,
            force=refresh,
            force_deep={subgroup_id} if subgroup_id is not None else None,
            bgm=bgm,
        )
    except MikanError as exc:
        return {
            **base,
            "title": title or str(bangumi_id),
            "status": "抓取失败",
            "flags": [str(exc)],
        }

    flags: list[str] = []
    target = None
    if subgroup_id is not None:
        target = next((h for h in ranking.candidates if h.subgroup_id == subgroup_id), None)
        if target is None:
            flags.append("当前字幕组已无可判定数据（可能撤档或改名）")
    if target is None:
        target = ranking.recommendation

    if target is None:
        return {
            **base,
            "title": detail.title,
            "status": "无可用字幕组",
            "flags": flags or ["所有字幕组都不可用"],
        }

    gap = target.metrics.coverage.peer_gap_h
    missing = target.metrics.coverage.missing_vs_peer
    stamps = [
        event.released_at
        for event in target.metrics.events.values()
        if event.released_at != datetime.min
    ]
    stale = (datetime.now() - max(stamps)).total_seconds() / 86400 if stamps else None
    lags = bool(missing) or (gap is not None and gap >= stale_days * 24)
    if gap is not None and gap >= stale_days * 24:
        flags.append(f"落后同番最快组 {gap / 24:.1f} 天")
    if missing:
        flags.append(f"比最快组少 {missing} 集")
    if target.risk.tier == "C":
        flags.append(f"停更风险高（{target.risk.risk:.2f}）")
    elif target.risk.tier in ("B", "U") and target.risk.risk is not None:
        flags.append(f"停更风险 {target.risk.level}（{target.risk.risk:.2f}）")

    if not tracked:
        status = "未纳管"
        flags = []
    elif target.risk.tier == "C" or lags:
        status = "需关注"
    elif flags:
        status = "留意"
    else:
        status = "正常"

    return {
        **base,
        "title": title if title else detail.title,
        "subgroup_id": target.subgroup_id,
        "subgroup_name": target.name,
        "tier": target.tier,
        "risk": target.risk.level,
        "peer_gap_days": round(gap / 24, 2) if gap is not None else None,
        "missing_vs_peer": missing,
        "stale_days": round(stale, 2) if stale is not None else None,
        "last_episode": target.metrics.coverage.last_episode,
        "peer_max_episode": target.metrics.coverage.peer_max_episode,
        "progress": (
            f"{_fmt_num(target.metrics.coverage.last_episode)}"
            f"/{_fmt_num(target.metrics.coverage.peer_max_episode)}"
        ),
        "status": status,
        "flags": flags,
    }
