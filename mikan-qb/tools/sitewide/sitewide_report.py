"""由 groups.csv 生成全站字幕组评估的文字报告与 PNG 图表。

需要在能 import matplotlib 的环境下运行（`uv run --with matplotlib ...`）。
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

FONT_CANDIDATES = [
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "Noto Sans CJK JP",
    "Source Han Sans SC",
    "DejaVu Sans",
]
TIER_COLOR = {"A": "#2e8b57", "B": "#e0a100", "U": "#8a8a8a", "C": "#c0392b"}


def _setup_font() -> None:
    """挑选可用的中文字体，避免图表出现方块。"""
    from matplotlib import font_manager

    available = {f.name for f in font_manager.fontManager.ttflist}
    chosen = next((name for name in FONT_CANDIDATES if name in available), "DejaVu Sans")
    plt.rcParams["font.sans-serif"] = [chosen]
    plt.rcParams["axes.unicode_minus"] = False
    print(f"[report] 字体：{chosen}")


def _label(row: dict[str, str]) -> str:
    """图表用的短标签，生肉组加 [raw] 后缀。"""
    suffix = "[raw]" if str(row.get("is_raw")) == "1" else ""
    return row["name"][:12] + suffix


def _num(row: dict[str, str], key: str) -> float | None:
    """读取 CSV 中的可空数值。"""
    value = row.get(key, "")
    if value in ("", "None", "nan"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _season_columns(rows: list[dict[str, str]]) -> list[str]:
    """汇总所有出现过的季度并按时间排序。"""
    labels: set[str] = set()
    for row in rows:
        labels.update(json.loads(row.get("season_counts") or "{}").keys())
    return sorted(labels)


def _chart_heatmap(rows: list[dict[str, str]], out: Path, top_n: int = 30) -> None:
    """Top 组 × 季度 开番数热力图（D1）。"""
    picked = sorted(rows, key=lambda r: -int(r["works"]))[:top_n]
    seasons = _season_columns(picked)
    if not seasons:
        return
    matrix = [
        [json.loads(r.get("season_counts") or "{}").get(s, 0) for s in seasons] for r in picked
    ]
    fig, ax = plt.subplots(figsize=(max(8, len(seasons) * 0.7), max(6, len(picked) * 0.32)))
    im = ax.imshow(matrix, aspect="auto", cmap="YlGnBu")
    ax.set_xticks(range(len(seasons)), seasons, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(picked)), [_label(r) for r in picked], fontsize=7)
    ax.set_title(f"每季开番数（Top {len(picked)} 活跃组）")
    fig.colorbar(im, ax=ax, label="作品数")
    fig.tight_layout()
    fig.savefig(out / "01_season_heatmap.png", dpi=130)
    plt.close(fig)


def _chart_delay_scatter(rows: list[dict[str, str]], out: Path) -> None:
    """绝对延迟 vs 相对延迟散点（D2）。"""
    fig, ax = plt.subplots(figsize=(9, 7))
    for tier in ("A", "B", "U", "C"):
        xs, ys, sizes = [], [], []
        for row in rows:
            if row["tier"] != tier:
                continue
            x, y = _num(row, "abs_delay_p50_h"), _num(row, "rel_delay_p50_h")
            if x is None or y is None:
                continue
            xs.append(x / 24)
            ys.append(y / 24)
            sizes.append(20 + 6 * int(row["works"]))
        if xs:
            ax.scatter(xs, ys, s=sizes, alpha=0.6, label=f"tier {tier}", color=TIER_COLOR[tier])
    ax.set_xlabel("绝对延迟中位数（天）")
    ax.set_ylabel("相对延迟中位数（天）")
    ax.set_title("绝对 vs 相对延迟（点大小=作品数）")
    ax.legend(title="停更风险")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "02_delay_scatter.png", dpi=130)
    plt.close(fig)


def _chart_delay_bars(rows: list[dict[str, str]], out: Path, top_n: int = 20) -> None:
    """绝对延迟最快的 Top 组（p50/p90 误差条）。"""
    picked = [
        r
        for r in rows
        if _num(r, "abs_delay_p50_h") is not None and int(r["abs_delay_n"] or 0) >= 20
    ]
    picked.sort(key=lambda r: _num(r, "abs_delay_p50_h") or 1e9)
    picked = picked[:top_n]
    if not picked:
        return
    p50 = [(_num(r, "abs_delay_p50_h") or 0) / 24 for r in picked]
    p90 = [(_num(r, "abs_delay_p90_h") or 0) / 24 for r in picked]
    fig, ax = plt.subplots(figsize=(9, max(5, len(picked) * 0.35)))
    ax.barh([_label(r) for r in picked], p50, color="#3b7dd8", label="p50")
    ax.errorbar(
        p50,
        range(len(picked)),
        xerr=[[0] * len(picked), [max(0, p - q) for p, q in zip(p90, p50, strict=False)]],
        fmt="none",
        ecolor="#888",
        capsize=3,
    )
    ax.invert_yaxis()
    ax.set_xlabel("绝对延迟（天）")
    ax.set_title("更新最快 Top 组（p50，误差条到 p90）")
    fig.tight_layout()
    fig.savefig(out / "03_delay_top.png", dpi=130)
    plt.close(fig)


def _chart_language(rows: list[dict[str, str]], out: Path, top_n: int = 20) -> None:
    """语言倾向堆叠条形图（D3）。"""
    picked = sorted(rows, key=lambda r: -int(r["works"]))[:top_n]
    if not picked:
        return
    totals = Counter()
    for row in picked:
        for part in (row.get("lang_mix") or "").split("|"):
            if ":" in part:
                totals[part.split(":", 1)[0]] += 1
    labels = [label for label, _ in totals.most_common(5)]
    fig, ax = plt.subplots(figsize=(10, max(5, len(picked) * 0.35)))
    left = [0.0] * len(picked)
    for label in labels:
        vals = []
        for row in picked:
            mix = dict(p.split(":", 1) for p in (row.get("lang_mix") or "").split("|") if ":" in p)
            vals.append(int(mix.get(label, 0)))
        ax.barh([_label(r) for r in picked], vals, left=left, label=label)
        left = [a + b for a, b in zip(left, vals, strict=False)]
    ax.invert_yaxis()
    ax.set_xlabel("发布条目数（去重标题）")
    ax.set_title(f"语言倾向 Top {len(picked)} 组")
    ax.legend(ncol=3, fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "04_language.png", dpi=130)
    plt.close(fig)


def _chart_risk(rows: list[dict[str, str]], out: Path) -> None:
    """停更风险：弃番率 vs 作品数（D4）。"""
    fig, ax = plt.subplots(figsize=(9, 7))
    for tier in ("A", "B", "U", "C"):
        xs, ys = [], []
        for row in rows:
            if row["tier"] != tier:
                continue
            risk, works = _num(row, "risk"), _num(row, "works")
            if risk is None:
                continue
            xs.append(works or 0)
            ys.append(risk)
        if xs:
            ax.scatter(xs, ys, alpha=0.6, label=f"tier {tier}", color=TIER_COLOR[tier])
    ax.axhline(0.15, color="#2e8b57", ls="--", lw=0.8)
    ax.axhline(0.35, color="#c0392b", ls="--", lw=0.8)
    ax.set_xlabel("参与季番作品数")
    ax.set_ylabel("停更风险值（0–1）")
    ax.set_title("停更风险 vs 作品数（虚线=low/mid、mid/high 阈值）")
    ax.legend(title="tier")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "05_risk.png", dpi=130)
    plt.close(fig)


def _md_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    """把若干行渲染成 Markdown 表格。"""
    header = "| " + " | ".join(title for _, title in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    lines = [header, sep]
    for row in rows:
        cells = []
        for key, _ in columns:
            value = row.get(key)
            cells.append("" if value is None else str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _summary(rows: list[dict[str, str]], from_year: int, to_year: int) -> str:
    """生成 Markdown 文字报告。"""
    rows = [{**r, "kind": "raw" if str(r.get("is_raw")) == "1" else "字幕"} for r in rows]
    total = len(rows)
    raw_count = sum(1 for r in rows if r["kind"] == "raw")
    fast = sorted(
        [
            r
            for r in rows
            if _num(r, "abs_delay_p50_h") is not None and int(r["abs_delay_n"] or 0) >= 20
        ],
        key=lambda r: _num(r, "abs_delay_p50_h") or 1e9,
    )
    slow = list(reversed(fast))[:10]
    risky = sorted(
        [r for r in rows if _num(r, "risk") is not None],
        key=lambda r: -(_num(r, "risk") or 0),
    )[:10]
    lang_total = Counter()
    for row in rows:
        for part in (row.get("lang_mix") or "").split("|"):
            if ":" in part:
                label, count = part.split(":", 1)
                lang_total[label] += int(count)

    pairs = [
        (abs_v, rel_v)
        for row in rows
        if (abs_v := _num(row, "abs_delay_p50_h")) is not None
        and (rel_v := _num(row, "rel_delay_p50_h")) is not None
    ]
    corr = None
    if len(pairs) >= 3:
        try:
            corr = statistics.correlation([p[0] for p in pairs], [p[1] for p in pairs])
        except statistics.StatisticsError:
            corr = None
    raw_abs = [
        v
        for row in rows
        if row["kind"] == "raw" and (v := _num(row, "abs_delay_p50_h")) is not None
    ]
    sub_abs = [
        v
        for row in rows
        if row["kind"] == "字幕" and (v := _num(row, "abs_delay_p50_h")) is not None
    ]
    raw_avg = round(sum(raw_abs) / len(raw_abs), 1) if raw_abs else None
    sub_avg = round(sum(sub_abs) / len(sub_abs), 1) if sub_abs else None
    corr_text = f"绝对与相对延迟的中位数相关系数 **r={corr:.2f}**" if corr is not None else ""
    speed_text = (
        f"生肉/未标注组平均绝对延迟 **{raw_avg} h**，中文字幕组 **{sub_avg} h**"
        if raw_avg is not None and sub_avg is not None
        else ""
    )
    top_prod = max(rows, key=lambda r: int(r["works"]), default=None)
    steady = sorted(
        [
            r
            for r in rows
            if r["kind"] == "字幕"
            and r["tier"] == "A"
            and int(r["works"]) >= 20
            and _num(r, "abs_delay_p50_h") is not None
        ],
        key=lambda r: _num(r, "abs_delay_p50_h") or 1e9,
    )[:3]
    top_text = (
        f"{top_prod['name']}（{top_prod['works']} 部，{top_prod['works_per_season']} 部/季）"
        if top_prod
        else "—"
    )
    fast_text = "—"
    if fast:
        fast_text = f"{fast[0]['name']}（{fast[0]['abs_delay_p50_h']}h，tier {fast[0]['tier']}）"
    risk_text = "—"
    if risky:
        risk_text = f"{risky[0]['name']}（risk {risky[0]['risk']}，{risky[0]['risk_level']}）"
    steady_text = (
        "、".join(f"{r['name']}（{r['abs_delay_p50_h']}h）" for r in steady) or "（样本不足）"
    )
    lang_text = "、".join(f"{k} {v}" for k, v in lang_total.most_common(3))
    top3 = sorted(rows, key=lambda r: -int(r["works"]))[:3]
    prod_text = "、".join(f"{r['name']}（{r['works_per_season']} 部/季）" for r in top3) or "—"

    lines = [
        f"# 全站字幕组评估 {from_year}–{to_year}",
        "",
        f"- 活跃组（≥3 部季番）：**{total}** 个"
        f"（其中生肉/未标注 **{raw_count}** 个，已标注 `raw` 但保留在排行）",
        f"- 覆盖发布条目：**{sum(int(r['abs_delay_n'] or 0) for r in rows)}** 个可用于延迟计算的集",
        "- 全站语言分布（按发布条目）："
        + "，".join(f"{k} {v}" for k, v in lang_total.most_common(8)),
        "",
        "> 口径：绝对延迟=发布时刻 − Bangumi 放送日；相对延迟=同番最快显式中文组；"
        "风险沿用现有 `assess_risk`（弃番率 + 整组停更 + 接番意愿）。长番/非季番已排除。",
        "",
        "## 摘要",
        "",
        f"- **产量最高**：{top_text}。",
        f"- **更新最快**（绝对延迟中位数，≥20 集样本）：{fast_text}。",
        f"- **中文字幕组里的稳健选择**（tier A 且 ≥20 部，按绝对延迟升序）：{steady_text}。",
        f"- **风险最高**：{risk_text}；注意「速度快」与「不弃番」不等价。",
        f"- **语言盘面**（按发布条目）：{lang_text}；头部组普遍提供内封（软字幕）。",
        "",
        "## 更新速度最快（绝对延迟中位数，需 ≥20 集样本）",
        "",
        "![](03_delay_top.png)",
        "",
        _md_table(
            fast[:15],
            [
                ("name", "字幕组"),
                ("kind", "类型"),
                ("works", "作品数"),
                ("abs_delay_p50_h", "绝对p50(h)"),
                ("abs_delay_p90_h", "绝对p90(h)"),
                ("rel_delay_p50_h", "相对p50(h)"),
                ("lang_primary", "主语言"),
                ("tier", "tier"),
            ],
        ),
        "",
        "## 更新最慢",
        "",
        _md_table(
            slow,
            [
                ("name", "字幕组"),
                ("works", "作品数"),
                ("abs_delay_p50_h", "绝对p50(h)"),
                ("rel_delay_p50_h", "相对p50(h)"),
                ("lang_primary", "主语言"),
                ("tier", "tier"),
            ],
        ),
        "",
        "![](02_delay_scatter.png)",
        "",
        f"解读：{corr_text}；{speed_text}。绝对延迟=发布到官方放送日，"
        "相对延迟=同番里比最快组慢多少；两者都低说明既快又稳，"
        "绝对高而相对低多是分割季/平台源整体延后所致。",
        "",
        "## 停更风险最高",
        "",
        _md_table(
            risky,
            [
                ("name", "字幕组"),
                ("works", "作品数"),
                ("risk", "risk"),
                ("risk_level", "等级"),
                ("tier", "tier"),
                ("confidence", "置信度"),
                ("last_release_days", "最近发布(天)"),
                ("evidence", "依据"),
            ],
        ),
        "",
        "![](05_risk.png)",
        "",
        "解读：tier C 为高风险，且风险优先于速度——「更新快」不等于「不弃番」；"
        "高产又低风险的 tier A 组才是追更首选。",
        "",
        "## 产量最高",
        "",
        _md_table(
            sorted(rows, key=lambda r: -int(r["works"]))[:15],
            [
                ("name", "字幕组"),
                ("kind", "类型"),
                ("works", "作品数"),
                ("works_per_season", "部/季"),
                ("peak_season_count", "单季峰值"),
                ("lang_primary", "主语言"),
                ("softsub_pct", "内封%"),
                ("tier", "tier"),
            ],
        ),
        "",
        "![](01_season_heatmap.png)",
        "",
        f"解读：产量头部为 {prod_text}，多为 raw/批量组，靠「全季多开 + 当日出片」"
        "同时拿下速度与产量；普通中文字幕组多为 1–12 部/季。",
        "",
        "## 语言倾向",
        "",
        "![](04_language.png)",
        "",
        "解读：全站以繁体为最大盘，简体与简日双语次之；头部组普遍提供内封（软字幕），"
        "语言偏好分化明显（简繁内封 / 简日双语 / 繁体）。",
        "",
        "## 已知限制",
        "",
        "- 集号体系不同者已用 ordinal 锚定；仍可能有极端长番残余误差。",
        "- 语言判定默认繁体（inferred），与显式标注分开统计，概览仍可能高估繁体。",
        "- 活跃定义为窗口内 ≥3 部季番；低频组样本少，confidence=low 时不下风险结论。",
        "- 生肉/未标注组保留在排行但标注 `raw`；相对延迟基线只取显式中文字幕组，raw 不进基线。",
        "- 绝对延迟仅统计覆盖该番过半集数的作品，已排除合集/单集噪声；"
        "以合集收尾的组会被记作「已完成」，但每周只更到中途再转批量的组仍可能被判疑似弃番。",
        f"- 本报告样本：窗口 {from_year}–{to_year}；"
        "补全更长窗口只需重跑 seasons→anime→deep→schedule。",
    ]
    return "\n".join(lines)


def build_report(rows: list[dict[str, str]], out_dir: Path, from_year: int, to_year: int) -> None:
    """生成全部图表与 summary.md。

    Args:
        rows: groups.csv 读出的行。
        out_dir: 输出目录。
        from_year: 窗口起始年。
        to_year: 窗口结束年。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    _setup_font()
    _chart_heatmap(rows, out_dir)
    _chart_delay_scatter(rows, out_dir)
    _chart_delay_bars(rows, out_dir)
    _chart_language(rows, out_dir)
    _chart_risk(rows, out_dir)
    summary = _summary(rows, from_year, to_year)
    (out_dir / "summary.md").write_text(summary + "\n", encoding="utf-8", newline="\n")
    print(f"[report] 已写入 {out_dir}")
