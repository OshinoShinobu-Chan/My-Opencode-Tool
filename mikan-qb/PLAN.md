# mikan-qb 项目完成摘要

> 状态：**P0–P6 全部完成，项目验收通过**
> 最后更新：2026-10-04

本文档原为实施计划，现项目已全部完成，故精简为完成摘要。详细指标与算法见
[`METRICS.md`](METRICS.md)，全站评估见 [`SITEWIDE.md`](SITEWIDE.md)。

## 已交付能力

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| P0 | uv workspace 骨架、配置解析、qB 连通性自检 | 已完成 |
| P1 | data 层：Mikan 抓取与解析（番剧页/年表页/RSS/搜索/季表） | 已完成 |
| P2 | W2 订阅与筛选：精确覆盖正则生成、`plan` / `apply` / `backfill` | 已完成 |
| P3 | W3 健康度：指标 + 停更风险 + 评分排序（`subgroup stats/rank/audit`） | 已完成 |
| P3+ | Bangumi 精确放送日程接入（`anime bangumi --schedule`） | 已完成 |
| P4 | W4 换组替换两阶段事务（`replace` / `status` / `finalize` / `abort`） | 已完成 |
| P5 | 4 个 Agent Skill + `references/` | 已完成 |
| P6 | 加固、`mikan status` 视图、`mikan cleanup` 定期清理 | 已完成 |

## 日常命令

```bash
# 配置与连通
mikan config validate
mikan qb check --show-global

# W1 发现与选组
mikan season list --year 2026 --season 夏
mikan anime subgroups <id>
mikan subgroup rank <id> --mode normal

# W2 订阅
mikan rule preview <id> --subgroup <sid>
mikan plan add <id> --subgroup <sid>
mikan apply add --apply

# W3 健康 / P6 状态
mikan subgroup audit --season 2026夏
mikan status

# W4 换组
mikan replace --bangumi <id> --to <sid>
mikan replace status <job>
mikan replace finalize <job> --apply

# 维护
mikan cleanup run            # dry-run
mikan cleanup run --apply
```

## 关键约定（沿用至今）

- **每番一条规则 + 双 tag**（`mikan-<bangumiId>` + 语言标签），换组靠 tag 精确定位。
- **所有写操作默认 dry-run**，必须显式 `--apply`。
- qB 保存路径 `/downloads/anime/<番名>`（不分季度）；feed 路径 `赛季/番名@字幕组`。
- 停止自动下载 repack/proper：`mikan qb rules repack-proper --set off --apply`。
- 停更风险在任何模式下都是**门控**（并非计分项）；风险优先于速度与语言。
- 算法口径已固定到 `profiles/*.toml`（含攒集惩罚、fast 并列稳定性优先、U 保守策略、容忍窗）。

## 测试与质量

- 全量 `pytest` 通过；覆盖率门槛：mikan-health ≥75%（含 core ≥90%）、mikan-qb ≥75%。
- pre-commit：hygiene / ruff / vulture / xenon / mypy / pytest / coverage-gate。

## 范围外

- 全站字幕组长期画像评估已由 `tools/sitewide/` 与 [`SITEWIDE.md`](SITEWIDE.md) 落地，
  不属于个人追番工作流。
