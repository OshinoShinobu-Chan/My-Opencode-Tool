# 全站字幕组评估（SITEWIDE）

> 状态：**计划已写，待快速验证**（验证结论回填到第 9 节）
> 目标：从 mikanani 获取近 10 年所有活跃字幕组数据，按 4 个维度评估比较，输出文字解读 + 图表
> 与 `PLAN.md` 的关系：把 PLAN 中标注为「范围外/暂缓」的全站评估落地（PLAN 现已精简为完成摘要）

## 0. 已确认决策（2026-09-19）

| 项 | 决策 |
| --- | --- |
| 时间范围 | 全 10 年（2017–2026），但**先跑近 3 年（2024–2026）验证效果**再补全量 |
| 活跃定义 | **历史发布 ≥ 3 部季番作品**的字幕组 |
| 深采深度 | **不采样：活跃组参与的每一部季番都全量深采**（`subgroup_releases`） |
| 绝对延迟口径 | **Bangumi 精确放送日程**，仅对**季番 × 深采作品**取 |
| 长番/非季番 | **完全排除**（起点早于窗口、或观测最大集号 > 60 的番，不参与任何维度） |
| 图表 | **PNG**（运行期用 `uv run --with matplotlib`，不写入依赖） |
| 停更风险 | **沿用现有 `assess_risk` 模型** |
| 输出 | **全部活跃组排行** + Top/Bottom 解读 |

**季番判定规则（用于排除长番）**：`start_date` 存在且落在采集窗口内，且该番所有组观测到的最大集号 ≤ 60。
柯南/哆啦A梦等起点久远或集号巨大的番直接排除，避免污染 D1–D4 与浪费 Bangumi 请求。

## 1. 四个评估维度与口径

| # | 维度 | 定义 | 数据来源 |
| --- | --- | --- | --- |
| D1 | 每季开番数量 | 每个 (字幕组, 季度) 参与的**去重作品数**；季度按番剧开播日（或年表 `season_date`）归属 | 季表 + 番剧页字幕组清单 |
| D2 | 绝对延迟 | `release - schedule.date_of_ordinal(ordinal)`，按 (组, 作品) 的集号升序锚定到第 1..n 集；组级取中位 / p90（小时） | 番剧页/Expand + Bangumi 日程 |
| D2b | 相对延迟 | 同番内 `release - 最快组`，基线**只认显式中文**（排除生肉，见 PLAN 8.2） | 同上，复用 `relative_delays_hours` |
| D3 | 语言倾向 | 该组发布标题的语言分布（简体/繁体/简繁/简日双语…）、内封占比、双语占比；区分 `explicit` 与 `inferred` | 发布标题解析 |
| D4 | 停更风险 | 复用 `assess_risk`：历史弃番率（Laplace 平滑）+ 整组停更 + 是否接新番 + peer_gap；输出 risk/tier/confidence | 每个作品组的 `WorkRecord` |

**D1 与 D4 的关系**：D1 只看「有没有参与」，D4 需要「完成到第几集 / 最后发布时间」，因此 D4 要求更深的发布数据。

## 2. 复用的项目接口（不重写算法）

```
MikanSource.season(year, season)                季表 → 番剧 id
MikanSource.bangumi(id)                          番剧页 → 组清单 + 最近窗口发布（约 12–15 行/组，会截断）
MikanSource.subgroup_releases(id, sid)           ExpandEpisodeTable(take=999) → 该组该番全量发布
MikanSource.publish_group(pgid)                  年表 → 该组按季度作品（补 D1 归属）
BangumiClient.subject / episodes + build_schedule   精确放送日程
core.episodes / core.airtime / core.risk          延迟、锚定、风险
HttpCache / SubgroupIndex                         缓存 + 限速 + 组索引
mikan_qb.mikan.build_source / build_bangumi_client 构建（含 .env、代理、token）
```

## 3. 架构与产物

- 采集器与报告器分离；落 SQLite `data/sitewide.sqlite3`（`data/` 已 gitignore）。
- 运行期依赖只用现有包；绘图用 `uv run --with matplotlib python tools/sitewide/report.py`。
- 代码放在 `tools/sitewide/`（ruff 覆盖；mypy / coverage 不覆盖，避免拉低包覆盖率门槛）。

**SQLite 表（草案）**

```
season_anime(season, bangumi_id)          季表归属
anime(bangumi_id PK, title, start_date, weekday, bgm_subject_id)
subgroup(subgroup_id PK, name, publish_group_id, first_seen, last_seen)
release(bangumi_id, subgroup_id, episode, episode_raw, title, language,
        language_source, sub_type, is_pack, is_extra, published_at, size_bytes,
        PRIMARY KEY(bangumi_id, subgroup_id, title))
schedule(bangumi_id PK, airdates_json, total_episodes, fetched_at)
work(subgroup_id, bangumi_id, season, group_final, fact_final,
     group_last, fact_last, rel_p50_h)      聚合结果（可重建）
```

**分阶段（可中断续采，已完成部分不重抓）**

```
S1 季表      40 季（10 年 × 4）              每季 1 请求
S2 番剧页    去重后的全部番剧                每番 1 请求
S3 深采      仅「活跃组」的最近 K 部作品     每 (组, 作品) 1 请求（Expand）
S4 日程      深采涉及的唯一番剧              每番 2 请求（Bangumi subject+episodes）
S5 聚合      由 release/schedule 计算 D1–D4（本地，无网络）
S6 报告      summary.md + groups.csv + *.png（本地）
```

## 4. 采集预算（**待快速验证**）

> 验证结论：番剧页**确实截断**（每组约 15 行），S3 不可省；实测 2.7 s/请求。
> 定稿参数：先跑 3 年、K=全部作品（不采样）、长番完全排除、日程只对季番×深采取。

| 阶段 | 3 年（粗估） | 10 年（粗估） | 说明 |
| --- | --- | --- | --- |
| S1 | 12 | 40 | 很便宜 |
| S2 | ~700 | ~2000–3000 | 季表去重 |
| S3 | N×K（N=活跃组数，K=每部作品采样数，拟 K=8） | 同上 | **最大不确定项** |
| S4 | 深采唯一番 ×2 | 同 | Bangumi 必须走代理 |

- 限速 Mikan 1 req/s、Bangumi 1 req/s；HTTP 缓存 TTL 6h/12h，**分年多次运行可续采**。
- 预估：3 年约 **40–70 分钟**；10 年约 **2.5–4 小时**。

## 5. 聚合算法要点

- **D2 锚定**：先用 `build_anchor(sid, 该组集号, schedule)` 把绝对编号（如 29–38）与相对编号（1–10）都归一到 ordinal，再取 `schedule.airdates[ordinal-1]` 为名义日；避免「编号体系不同」造成假延迟/假弃番（同 PLAN 0.2 / 0.3）。
- **D2b 基线**：`subtitle_baseline(releases)` 只收 `explicit` 中文；否则未标注生肉会把正片组算成「慢好几天」。
- **D3**：`language_source == "inferred"` 的默认「繁体」单独统计，不与显式繁体混为一谈（PLAN 8.2 已知代价）。
- **D4**：为每组收集其参与作品的 `WorkRecord`（group_final/fact_final/最后发布/rel_p50），喂给 `assess_risk`；`group_silent`（整组停更）与个别 `dropped` 分开陈述。
- **活跃过滤**：`≥3 部作品` 在聚合阶段按去重 `bangumi_id` 计数。

## 6. 报告与图表

- 文字 `summary.md`：方法/口径、样本量、限制与已知偏差；总览；Top/Bottom；按语言与规模分群。
- `groups.csv`：每组的 D1–D4 指标原始值，便于二次分析。
- 图表（PNG，需处理中文字体，优先级 Microsoft YaHei → SimHei → Noto Sans CJK → DejaVu）：
  1. 组 × 季度 开番数热力图（Top N）
  2. 绝对延迟 vs 相对延迟散点（点大小 = 参与作品数）
  3. 各组延迟分布箱线图（Top N）
  4. 语言倾向堆叠条形图（Top N）
  5. 风险：弃番率 vs 完成率的散点/条形（按 tier 着色）

## 7. 快速验证清单（本轮先做，成本 ~30 请求）

1. 季表对早期/近期年份（2017/2024/2025/2026）是否可用，每季番剧数。
2. `bangumi()` 单页给出多少字幕组、每组多少条发布、是否被截断（与 `subgroup_releases` 全量对比）。
3. `subgroup_releases()` 能拿到全量、单次耗时。
4. Bangumi `subject`/`episodes` 经代理是否可用、每番请求数与耗时。
5. 实测平均请求耗时 → 外推 S1/S2/S3/S4 总时长。
6. 用近 3 年样本估算活跃组数 N，检验 S3 预算。

> 若验证显示预算、口径或可行性需要修改，将用 question 工具征求用户意见后再定稿。

## 8. 风险与限制

- 站点改版导致解析失效（解析集中在 `data/source.py`，可用 fixtures 回归）。
- 早期年份数据缺失 / 番剧页截断 → 需 S3 深采，成本上升；必要时缩小到近 N 年或降低 K。
- Bangumi 代理不可用 → 绝对延迟退回估算口径（需用户确认）。
- 集号体系不同 → 用 EpisodeAnchor + `numbering_mismatch` 守卫。
- 生肉/未标注条目 → 相对基线与资格过滤只认 `explicit`。
- 全站排行会混入「联合发布」「个人发布」「一次组」等噪声，需在报告中说明分组与别名问题。

## 9. 快速验证结论（2026-09-19，`tools/sitewide/probe.py`）

**实测事实**

| 项 | 结果 |
| --- | --- |
| 季表可用性 | 2017/2024/2025/2026 均可用；每季 **84–99 部**（多于原估的 ~60） |
| 单请求耗时 | 冷请求均值 **2.7 s**（限速 1/s + 解析）；命中缓存 ~0.1 s |
| 番剧页截断 | **确认截断**：`bangumi()` 每组固定约 15 行；`subgroup_releases` 可回到全量（如 id 681 某组 15 → **233** 行） |
| Bangumi | token + 代理 `127.0.0.1:7890` 可用；`subject`+`episodes` 各 1 请求 |
| 长番污染 | 首条命中常是长番：id 247 日程 **214 集/起点 2014**、id 681 **846 集/2005**，其 ordinal→放送日 对「季番延迟」无意义 |

**结论：计划需要修订**

1. **S2 必走、S3 不可省**：番剧页会截断，D2/D4 必须用 `subgroup_releases` 深采，请求量远超初估。
2. **时长被显著低估**：按 2.7 s/请求外推——
   - 3 年：S1+S2+S4 ≈ 1.5–2 h，加 S3（N 组 × K 部）后约 **2.5–3.5 h**；
   - 10 年：约 **6–8 h**（原 PLAN 14 的「55 分钟」未计入 Bangumi 且高估了并发/低估了季番数）。
3. **Bangumi 不应覆盖全部番剧**：长番/合集会造成大量无意义请求与错误 ordinal 映射，应只对**季番 × 深采样作品**取日程。
4. 需要把「非季番（总集数过大 / 起点早于当季）」排除或单独处理。
5. 活跃组数 N 与深采样 K 直接决定 S3 预算，需先定 K 再估总时长。

**待用户确认后**按新参数定稿（见下方提问）。已确认的参数见第 0 节。

## 10. 执行记录（2026-09-19）

**已实现**（`tools/sitewide/`，ruff 通过；mypy/coverage 不覆盖 tools）

```
sitewide.py         采集(stages) + 聚合 + 报告入口；SQLite data/sitewide.sqlite3
sitewide_report.py  groups.csv -> summary.md + 5 张 PNG（matplotlib，Microsoft YaHei）
probe.py            本文件第 9 节的可行性探测脚本
```

`uv run --all-packages python tools/sitewide/sitewide.py {seasons|anime|deep|schedule|status|aggregate}`
`uv run --with matplotlib ... report`（本机默认 PyPI 极慢，已改用清华镜像 `uv pip install matplotlib` 后
`uv run --no-sync ...` 运行）。

**最终进度（2024–2026 全量，已完成）**

| 计数 | 值 |
| --- | --- |
| season_anime（10 季汇总） | 979 |
| anime 已抓 | 770 / 770 |
| seasonal（排除长番后） | 716 |
| subgroup / active（≥3 部季番） | 164 / 77 |
| release 行 | 64440 |
| deep 完成 | 3557 / 3557 |
| schedule 完成（Bangumi） | 716 |
| 可用于延迟计算的集 | 29017 |

最终报告：`reports/sitewide/2024-2026/`
- `summary.md`：**图文结合**（摘要 + 各维度内嵌 PNG + 表格 + 解读 + 限制）
- `groups.csv`：77 组全量指标；`01..05*.png`：5 张图表（中文渲染正常）

**采集过程中发现并修复的问题**

1. **合集/批量组被误判**：7³ACG、Lilith-Raws 等以 BDRip 合集或「每周更到中途再转批量」发布。
   合集不计入单集导致 `group_final < fact_final` → 假弃番；只存到单集还会被锚定到错误放送日 → 延迟爆炸。
   已修：有合集即视为完成；绝对延迟仅统计**覆盖该番过半集数**的作品，并剔除 <-7 天 / >60 天的异常值。
2. **Bangumi 日期字段不规范**：实测出现空串 `""`、未补零 `2026-4-26`、纯年份 `2099`、
   带时间的 datetime。已在 `BangumiClient` 加 `_coerce_date` 归一（空/占位→None，未补零→补零，纯年份→None）。
3. Mikan 伪字幕组「生肉/不明字幕」：按用户要求**保留在排行但标注 `raw`**
   （`groups.csv` 有 `is_raw`/`raw_reason`，表格有「类型」列，图表标签加 `[raw]`，摘要注明数量）。

**扩展到 10 年**：只需把 `--from-year` 调到 2017 并重跑 seasons→anime→deep→schedule→aggregate→report；
数据库按 (番剧, 字幕组) 去重，已完成的 3 年不会重抓。预计额外 3–5 小时。

## 11. 扩展到 11 年（2016–2026，已完成）

| 计数 | 值 |
| --- | --- |
| season_anime | 3825 |
| anime（2 部 404 无法抓取） | 2729 |
| seasonal（排除长番后） | 2556 |
| subgroup / active | 455 / 215 |
| release 行 | 219198 |
| deep 完成 | 12594 |
| schedule 完成（Bangumi） | 2544 |
| 可用于延迟计算的集 | 93386 |

报告：`reports/sitewide/2016-2026/{summary.md,groups.csv,01..05*.png}`。

**本轮新增修复**

1. Bangumi 日期进一步容错：`原定2021-07-28`、`2022-10-02（…先行配信）`、`201710-15`、`2018-09`、
   `016-08-05` 等；改为「在字符串中搜索任意合法 `YYYY-M-D`，否则视为未知」。
2. 控制台为 GBK 时，含日文标题（如 `・`）的进度打印会崩溃 → 入口调用 `force_pipe_utf8()`。
3. 摘要与解读改为**数据驱动**（产量/最快/稳健/风险/语言均从 `groups.csv` 现算），
   避免换了时间窗后文字与数据不符。

**11 年尺度观察**：语言分布更均衡（繁体 103235、简体 45455、简日双语 30396、简繁 17786）；
产量头部为 LoliHouse 1038 部、生肉/不明字幕 989 部、Kirara Fantasia 881 部、ANi 746 部。
