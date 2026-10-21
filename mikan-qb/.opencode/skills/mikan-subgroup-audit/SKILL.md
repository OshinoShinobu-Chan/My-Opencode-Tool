---
name: mikan-subgroup-audit
description: Use when the user asks how reliable a fansub group is, what a 字幕组's health/risk numbers mean, or wants an explanation of 停更 / 弃番 / 速度 / 覆盖率 data — 字幕组口碑 / 健康度 / 风险 / 该不该选某组 / subgroup stats / subgroup rank / subgroup audit. Translates CLI metrics into plain conclusions without making the choice for the user.
---

# 字幕组健康度解读（W3）

把 `mikan` 输出的指标数字翻译成人能懂的结论与证据。**只解释，不擅自替用户在多个候选间做决定。**

## 数据来源

```bash
uv run mikan subgroup stats <sid|--name 名称> [--works N] --json    # 单组跨作品画像
uv run mikan subgroup rank <bangumi_id> --mode normal --json        # 单番候选排序
uv run mikan subgroup audit --season 2026夏 --json                  # 季度停更巡检
uv run mikan subgroup audit --scan 2026夏 --json                    # 扫全季（含未纳管，慢）
```

- `subgroup stats` 需要 `sid`；用 `--name` 模糊检索时若命中多个会以退出码 3 列出候选，**请用户选一个**。
- 详细指标定义见仓库根目录 `METRICS.md`；聚合与历史回看口径见 `METRICS.md` 与 `PLAN.md`（完成摘要）。

## 读什么字段

### `subgroup stats`

- `risk`：`{risk, level, tier, confidence, drop_rate, evidence}`。
- `works_total` / `works_judged`：回看作品数与**可判定**数；`works_judged=0` 时不能谈弃番率。
- `last_release_days`：最近一次发布距今；配合 `accepts_new_season` 判断是否整组停更。
- `median_rel_p50_h`：跨作品相对同番最快组的中位延迟（小时）。
- `records[].dropped`：`true` 弃番 / `false` 未弃 / `null` 无法判定（编号体系对不上或仍在连载）。

### `subgroup rank`

每候选：`tier`、`risk`、`confidence`、`score`、`speed_score`、`lang_match`、`stability`、`missing_vs_peer`、`peer_gap_h`、`coverage_ratio`、`languages`、`sub_types`、`evidence`，以及顶层 `recommendation` / `decision_required` / `decision_options` / `warnings`。

### `subgroup audit`

`findings[].status`：`正常` / `留意` / `需关注` / `未纳管` / `无可用字幕组` / `抓取失败`；`flags[]` 是原因（落后同番最快组 N 天、比最快组少 N 集、停更风险等）。

## 解释规则

1. **风险优先、门控而非计分**：tier A（低）/ B（中）/ U（未知）/ C（高）。速度再快也不能抵消高风险。
2. **置信度低就不给风险数值**：`confidence=low` 或 `works_judged` 太少时，只说「数据不足以判定」，可以补 `--works N` 或 `--deep -1` 深查。
3. **区分「弃番」与「整组停更」**：`group_silent`（全组 ≥21 天没发任何东西）与个别作品 `dropped` 是两回事，措辞要分开。
4. **区分「已完结落后」与「连载中落后」**：跨作品弃番判定只对整体已安静的历史作品成立；当前番剧用 `peer_gap_now` / `peer_gap_h`。
5. **`null`/缺失的指标不要补零**，如实说明「无法判定」。
6. **≥2 个可用候选 / 容忍窗裁决 / tier U / tier C** → 呈现证据让用户选，不要自行推荐其一。

## 输出模板

- 结论一句话（推荐 / 需要用户决定 / 数据不足）。
- 支撑证据 2–4 条，直接引用 `evidence` 与关键数字（带单位）。
- 如有 `warnings`，逐条列出。
- 需要进一步动作时，交接给 `mikan-select-group`（选组订阅）或 `mikan-replace-subgroup`（换组），不要在本 Skill 里执行写操作。

## 参考

- 标签与语言判定：`../references/taxonomy.md`
- 报错排查（抓取失败、Bangumi 代理）：`../references/troubleshooting.md`
