---
name: mikan-health-monitor
description: Use during an ongoing season to audit already-subscribed anime for stalls — 巡检 / 季度中检查 / 有没有番停更 / 更新落后 / 追番健康度 / 需要换组吗 / status / subgroup audit. Produces a per-anime 继续等 or 换组 recommendation but never auto-executes the change.
---

# 季度中巡检（不自动执行）

> 所有命令需在仓库的 `mikan-qb/` 目录下执行（uv workspace 根；从仓库根进入时先 `cd mikan-qb`）。

定期（用户手动触发）检查托管清单里的番剧是否落后于自身节奏或同番最快组，输出「继续等 / 需关注 / 建议换组」，**不自动做任何写操作**。

## 当前可用命令

> 先用 `mikan status` 快速总览（feed/规则/最后命中/落后/停滞），再对可疑番用 `subgroup audit` 深查。

```bash
uv run mikan status --json                            # 托管清单总览：feed / 规则 / 最后命中 / 落后 / 停滞
uv run mikan status --season 2026夏 --json            # 只看该季已纳管番剧
uv run mikan subgroup audit --season 2026夏 --json    # 只看该季已纳管番剧（默认只查已纳管）
uv run mikan subgroup audit --json                    # 全部已纳管番剧
uv run mikan subgroup audit --scan 2026夏 --json      # 额外扫描该季未纳管番剧（慢，按需）
```

对个别可疑番再补：

```bash
uv run mikan subgroup rank <bangumi_id> --mode normal --json   # 看是否有更好的候选组
uv run mikan subgroup stats <sid> --json                       # 看当前组的跨作品守约度
uv run mikan qb rules match "<rule_name>" --json               # 线上规则最近命中
```

## 读法与判定

`status` 的 `entries[].status`：`正常` / `留意` / `需关注` / `规则禁用` / `feed 缺失` / `规则缺失` / `订阅缺失` / `抓取失败`。
其中 `feed`/规则缺失属 qB 侧问题（先修订阅，与换组无关），`落后`/`停滞` 才是内容侧信号。

`subgroup audit` 的 `findings[]`：

| `status` | 含义 | 建议 |
| --- | --- | --- |
| `正常` | 未见落后 | 继续等 |
| `留意` | 有轻微风险信号 | 记录并下次复查 |
| `需关注` | 落后 ≥ `--stale-days` 天、比同番最快组少集、或 tier C | 结合 `peer_gap_h` / `flags` 判断「继续等还是换组」 |
| `未纳管` | 该番不在托管清单（仅 `--scan` 出现） | 若用户在意，交 `mikan-select-group` |
| `无可用字幕组` | 所有组都不可用 | 报事实，询问用户 |
| `抓取失败` | 抓取异常 | 见 `../references/troubleshooting.md`（多为网络/代理） |

判断要点：

- **只是在连载中暂时落后** vs **真的要弃番**：看 `flags` 里是否同时有「比最快组少 N 集」与持续停滞；单次落后多为发布节奏差异。
- **整季都没人更**（`peer_gap_h` 也小）通常不是换组能解决的，先确认番剧是否停播/完结。
- **tier C 或 `confidence=low`**：C 要提示风险，low 只陈述事实，都不擅自下「换组」结论。

## 输出

逐番给出：`标题（bangumi_id）` / 当前组（sid）/ 状态 / 落后天数或集数 / 一句建议。
最后按「继续等 / 需关注 / 建议换组」分组的汇总计数。

## 交接

- 确认要换组 → 交给 `mikan-replace-subgroup`（它会先 dry-run 并让用户确认）。
- 需要重新评估候选 → 交给 `mikan-subgroup-audit` 或 `mikan-select-group`。
- **本 Skill 不执行 `replace` / `apply` / `finalize` 等写操作。**

## 参考

- 标签与语言判定：`../references/taxonomy.md`
- qB 约定与现状：`../references/qb-config.md`
- 报错排查：`../references/troubleshooting.md`
