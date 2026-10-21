# 字幕组评估指标说明

本文档说明 `mikan-health` 里每个评估指标的含义、算法和参数。
与 `PLAN.md` 的关系：PLAN 记录**计划与决策**（现已精简为完成摘要），本文档记录**指标与算法本身**。

- 代码位置：`packages/mikan-health/src/mikan_health/`
- 纯计算层（无 IO）：`core/airtime.py`、`core/episodes.py`、`core/metrics.py`、`core/risk.py`、`core/score.py`
- 参数全部外置在 `profiles/*.toml`，改标准不需要改代码
- 测试：`packages/mikan-health/tests/test_health.py`、`test_bangumi.py`

---

## 1. 输入与时间基准

### 1.1 数据来源

| 数据 | 来源 | 说明 |
| --- | --- | --- |
| 该番各字幕组的逐集发布记录 | Mikan 番剧页 / `ExpandEpisodeTable` | 标题、发布时间、体积、磁力 |
| 已播集数与每集精确放送日 | Bangumi API（`/v0/subjects/{id}`、`/v0/episodes`） | 来自番剧页的 `bgm.tv/subject/<id>` 链接 |
| 该组历史作品列表 | `/Home/PublishGroup/<id>` | 用 `subgroup_id ↔ publish_group_id` 本地索引映射 |

Bangumi 不可用时（无 token / 无代理）自动退回「开播日 + 7 × 集号」的估算口径，
算法不变，只是基准日期精度下降。

### 1.2 集数事件：每集只取一个时间点

`core/episodes.py::episode_events`

```
t_release(ep) = min(pubDate) over 该字幕组该集的所有版本
```

```python
events[episode] = EpisodeEvent(
    episode=episode,
    released_at=min(stamps),      # 同集多版本取最早
    variant_count=len(items),
)
```

- 丢弃非正片条目（`NCOP/NCED/PV/Menu/字体/SP/OVA`）与合集
- 同集多版本（简繁内封 / 简体内嵌 / 繁体内嵌）只保留**最早**时间，
  避免把「组内发布顺序」当成更新速度

### 1.3 时间基准 A：名义播出日（估算口径）

`core/episodes.py::nominal_datetime`

```
nominal(ep) = 开播日期的 00:00 + 7 × (ep − start_ep) 天
```

`start_ep` 取该番**所有字幕组中出现过的最小集号**，用于兼容绝对编号
（芙莉莲第二季是 29–38，若按 `ep−1` 算会整体偏 28 周）。

```python
def nominal_datetime(start_date, start_ep, episode):
    offset_weeks = (episode - start_ep) * 7
    return datetime.combine(start_date, datetime.min.time()) + timedelta(days=offset_weeks)
```

**已知偏差**：Mikan 只有「放送星期 + 放送开始日」，没有播出时刻，所以基准是当天 00:00。
连播（第 1、2 集同日）也会让后续每集的名义日期整体偏 7 天——这正是引入基准 B 的原因。

### 1.4 时间基准 B：Bangumi 精确放送日（默认口径）

`core/airtime.py::Schedule` / `core/metrics.py::airdate_delays`

```
nominal(ordinal) = schedule.airdates[ordinal − 1] 的 00:00
```

```python
def airdate_delays(events, schedule):
    for ordinal, event in events.items():
        aired = schedule.date_of_ordinal(int(ordinal))
        nominal = datetime.combine(aired, datetime.min.time())
        result[ordinal] = (event.released_at - nominal).total_seconds() / 3600
```

保留了「00:00 基准」的系统偏移（对同番所有组是**同一个常数**，横向比较不受影响），
但消除了连播、停播周、分割季带来的漂移。

### 1.5 集数归一化：ordinal

`core/airtime.py::build_anchor`

不同字幕组对同一部番可能用不同编号体系（死神 7 vs 48、芙莉莲 29-38 vs 1-10）。
解决方式：**组内集号升序 → 第 1..n 集**。

```python
numbers = sorted({n for n in episode_numbers})
for index, number in enumerate(numbers):
    anchor.ordinal_of[number] = index + 1        # 29→1, 30→2, …
```

之后**所有跨组比较都在 ordinal 空间进行**（缺集、落后、相对延迟），
绝对值编号与相对编号因此被归一到同一套序号。

合理性检查：集号数量与日程集数相差超过 34% 时标记 `aligned=False`；
另可用 `pre_air_violations()` 检测「发布时间早于放送日」的错位。

### 1.6 语言识别（默认繁体）

`core/releases.py::_resolve_language`

```
1. 标题里有显式语言标记（简体/简繁/简日双语/CHT/JPSC…）      → explicit
2. 标题里有 raw / raws / 生肉 / 无字幕 / no sub / nosub      → 无字幕（raw）
3. 都没有                                                   → 推断为「繁体」（inferred）
```

`ParsedTitle.language_source` 记录结论来源，下游区别对待：

| 消费方 | 用推断值吗 | 原因 |
| --- | --- | --- |
| 语言展示、`has_traditional` | 是 | 符合「没写就当繁体」的直觉 |
| **相对速度基线** `subtitle_baseline()` | **否** | 未标注条目里混着生肉（`[黒ネズミたち] … (ABEMA 1920x1080)`），算进基线会让正片组全变成「慢好几天」 |
| **生肉剔除**（资格过滤） | **否** | 真生肉往往毫无标记，只能靠「有无显式中文字幕」拦 |

---

## 2. 速度类指标

### 2.1 绝对延迟 `delay_p50_h` / `delay_p90_h`

**含义**：相对名义播出日的延迟，刻画该组跨作品的固有节奏。

```
delay(ep) = 发布时间 − nominal(ep)        # 小时，可为负（提前）
```

| 指标 | 算法 | 用途 |
| --- | --- | --- |
| `delay_p50_h` | `median(delay)` | 典型追更速度（进评分） |
| `delay_p90_h` | 第 90 百分位 | 偶发拖延有多严重 |

```python
delay_p50_h=median_or_none(delays),
delay_p90_h=quantile(delays, 0.9),
```

`quantile` **取序不插值**，避免小样本被插值平滑：

```python
def quantile(values, ratio):
    ordered = sorted(values)
    index = min(int(round(ratio * (len(ordered) - 1))), len(ordered) - 1)
    return ordered[index]
```

### 2.2 相对延迟 `rel_p50_h` / `rel_p90_h`

**含义**：相对**同番最快组**的延迟。抗噪主力——播出时刻未知、停播周、开播日登记错误
都会被同番其他组同步抵消。

```
t_best(ep) = min over 基线内所有字幕组 的 t_release(ep)
rel(ep)    = t_release(ep) − t_best(ep)      # 最快组恒为 0
```

```python
source = {sid: ev for sid, ev in events_by_group.items()
          if baseline is None or sid in baseline}
```

**基线只取「有显式中文字幕」的组**（`subtitle_baseline()`）。生肉组先出片但用户不会选，
纳入基线会把所有正片组算成慢好几天（实测桜都 83.5h → 修正后 LoliHouse 4.0h）。

### 2.3 稳定性 `jitter_h`

**含义**：延迟的离散程度，用 MAD 而不是标准差，抗离群值。

```
jitter = median(|delay − median(delay)|)
```

```python
def mad(values):
    center = median(values)
    return median([abs(v - center) for v in values])
```

### 2.4 更新间隔 `interval_med_h` 与攒集连发 `catchup_weeks`

```
interval_med = median(相邻两次发布的时间差)        # 周更应为 168h
catchup      = 间隔 < 48h 的次数
```

```python
catchup_weeks=sum(1 for gap in gaps if gap < catchup_hours),   # 默认 48h
```

`catchup_weeks ≥ 2` 时速度分乘 0.85——攒集连发对「追更」体感很差。

### 2.5 首集滞后 `startup_lag_h`

```
startup_lag = min(delay)
```

只作报告，不进评分：开播日登记不准会影响它，不应污染 `speed_p50`。

---

## 3. 覆盖率类指标

### 3.1 应播集数 `expected_last_episode`

- 有 Bangumi 日程：`schedule.aired_count(now)` = 已到放送日的集数（精确）
- 无日程：`start_ep + floor((now − 开播日) / 7)`

### 3.2 缺集 `missing_eps`

```
missing = [ep for ep in range(span_start, expected_last) if ep not in events]
```

注意口径：有日程时 `span_start = 1`（ordinal），否则为 `start_ep`（原始集号）。
`CoverageMetrics.space` 字段标明当前是 `ordinal` 还是 `episode`。

### 3.3 停滞 `stale_days` / `stale_ratio`

```
stale_days  = now − 该组本番最后一次发布        # 天
stale_ratio = stale_days × 24 / max(interval_med, 24h)
```

分母加 24 小时下限：`interval_med` 很小时（例如连发两集）比值会算出 1 万倍这种噪声。

```python
stale_ratio = (stale_days * 24 / max(interval, MIN_INTERVAL_HOURS)
               if stale_days is not None and interval and interval > 0 else None)
```

`stale_ratio > 1.5` 时速度分乘 0.9。

### 3.4 落后同番最快组 `peer_gap_h` / `missing_vs_peer`

```
own_max  = 该组最大 ordinal
peer_max = 基线组中的最大 ordinal
peer_gap = 若 own_max < peer_max：now − t_best(peer_max)，否则 0
missing_vs_peer = max(0, 基线组最多集数 − 该组集数)
```

- `peer_gap_h` 是**当前季唯一可靠的实时停更信号**：绝对视角无法区分「还没到更新日」和「弃坑」
- `missing_vs_peer` 在 ordinal 空间比较，因此不受编号体系影响

### 3.5 覆盖率折算 `coverage_ratio`

```
coverage_ratio = min(该组集数 / 基线组最多集数, 1.0)
```

速度分乘该系数：只发了 1 集的组（如只发合集或刚起步）速度分不应虚高
（实测 7³ACG 0.600 → 0.060）。

---

## 4. 跨作品守约度与风险

### 4.1 作品记录 `WorkRecord`

对某组最近若干**季度**的作品（默认 4 季 / 上限 16 部，**跳过当前季**——当前季多在连载中，
既无法判定弃番，又会把名额占满）逐部抓取：

| 字段 | 含义 |
| --- | --- |
| `group_final_episode` | 该组在这部作品上的最高集号 |
| `fact_final_episode` | 所有字幕组在这部作品上的最高集号（**事实总集数**） |
| `group_last_release` / `fact_last_release` | 该组 / 全体最后一次发布 |
| `rel_p50_h` | 该组在这部作品上的相对延迟中位数 |

### 4.2 弃番判定 `judge_work`

返回 `True`（疑似弃番）/ `False`（正常）/ `None`（无法判定）。

```python
if (now - record.fact_last_release).days < drop_gap_days:   # 作品整体仍在更新
    return None
if record.group_final_episode >= record.fact_final_episode: # 追平
    return False
if numbering_mismatch(group_final, fact_final):             # 编号体系不同
    return None
if quiet_days < drop_gap_days:                              # 只是暂时落后
    return None
return bool(subgroup_active)       # 组还在别处更新 → 才是真弃番
```

`numbering_mismatch`：`group_final < 0.6 × fact_final` 时判为编号体系不同。
（死神 千年血战篇 7 vs 48 就是这种情况，直接跳过而不是误报弃番。）

### 4.3 弃番率 `drop_rate`（Laplace 平滑）

```
drop_rate = (drops + 1) / (judged + 2)
```

分子分母各加 1/2，避免 0/0 与「1 部里弃 1 部 = 100%」的极端。

### 4.4 整组停更 `group_silent` 与接新番 `accepts_new_season`

```
quiet        = last_release_days ≥ silent_days(21)
group_silent = quiet 且 最近两季未接任何新番
```

两个条件缺一不可：只看发布间隔会把「正在追本番、只是历史作品安静」的组误判为停更
（实测 Nesuto 缺 1 集/0.0 天却被报「80 天没发布」）。

`accepts_new_season` 由该组作品年表里是否出现最近 5 个月内的季度判断。
`group_silent` 同时决定 `judge_work` 的 `subgroup_active` 参数——组都停了，
就不能把它没做完的作品算成弃番。

### 4.5 风险值与分级

```
risk = drop_rate
     × 2.0   若 peer_gap_h ≥ stall_lag_days(10) 且组未停更     ← 疑似弃坑
     × 1.5   若 group_silent                                   ← 整组停更
     × 1.3   若最近两季未接新番
     × 0.7   若已判定作品全部正常完结
risk = clamp(risk, 0, 1)

tier:  low < 0.15 ≤ mid ≤ 0.35 < high
```

```python
if suspect_stall:
    risk *= 2.0
if group_silent:
    risk *= 1.5
elif quiet:
    evidence.append("历史作品最近一次发布在 N 天前，但该组仍在接新番")
```

**置信度**：`judged ≥ 8 → high`，`≥ 3 → medium`，否则 `low`。
`low` 且落在低风险区时，按 `policy.unknown_tier_policy` 归入 `U`（未知）而不是 `A`。
样本不足且该组活跃并接新番时，给 `risk=None / level=low` 并显式说明「历史样本不足」。

---

## 5. 评分与决策

### 5.1 速度分 `speed_score`

```
abs_score = exp(−max(delay_p50_h, 0) / speed_T_hours)      # 默认 T=48h
rank_part = 1 − (比它慢的候选数 / (n−1))                    # 名次分：最快 1.0
decay_part= exp(−max(rel_p50_h, 0) / speed_T_hours)         # 量级分
rel_score = 0.5 × rank_part + 0.5 × decay_part
speed     = 0.4 × abs_score + 0.6 × rel_score
```

| delay_p50 | abs_score |
| --- | --- |
| ≤ 0（提前） | 1.00 |
| 24h | 0.61 |
| 48h | 0.37 |
| 168h | 0.03 |

相对分「名次 + 量级」各半：只用名次会把「快 0.1 小时」和「快 3 天」等同看待。

之后依次乘惩罚：覆盖率折算 → 攒集连发 ×0.85 → 停滞 ×0.9。

### 5.2 稳定性分 `stability`

```
stability = max(0, 1 − jitter_h / 48)
```

### 5.3 语言分 `lang_match`

```
lang_match = max(该组各语言在 profile.lang_scores 中的得分) + 内封加分
```

`lang_scores` 默认：`简日双语 1.00 > 简繁日 0.90 > 简体 0.70 = 简繁 0.70 > 繁体 0.30 > 无中文 0`；
`softsub_bonus = 0.05`（限幅到 1.0）。

### 5.4 综合评分与模式权重

```
score = w_speed × speed_score + w_lang × lang_match + w_stability × stability
```

| profile | speed | lang | stability |
| --- | --- | --- | --- |
| `personal-fast` | 0.70 | 0.00 | 0.30 |
| `personal-normal` | 0.35 | 0.45 | 0.20 |

### 5.5 风险门控（lexicographic，不是加权）

```
Step 0 资格过滤（硬性）
  ✗ 没有任何「显式」中文字幕标记（生肉/未标注）→ 剔除
  ✗ normal 模式：没有任何简体 → 剔除
  ✗ 没有可用单集（只有合集或空）→ 剔除
  ✗ tier C（高风险，且 `high_risk_veto = true`）→ 剔除

Step 1 分级   A 低 / B 中 / U 未知 / C 高（C 默认剔除，由 high_risk_veto 控制）
Step 2 在 A 内按 score 排序
Step 3 容忍窗
  best(B/U).score − best(A).score > speed_gain_threshold(0.15)
  → 不自动选，输出二选一要求确认
```

风险是**门控**而非计分项：更快的候选只要风险到 B 就降档，不可能靠速度抵消。
只有「优势超过容忍窗」时才把决策交回给用户（`decision_required=True`）。

风险门控只对**评分前 `--deep` 名**做历史深查（默认 6），其余标 tier U。
原因：19 个组 × 16 部作品 = 300+ 次请求不可接受。

---

## 6. 已知偏差与取舍

| 偏差 | 现状 | 缓解 |
| --- | --- | --- |
| 无播出时刻 | 基准是当天 00:00，含 ≤24h 系统偏移 | 对同番所有组是同一常数，横向比较不受影响；相对视角是主力 |
| 无官方总集数 | 有 Bangumi 时用其 `total_episodes`；否则用他组最高集数 | — |
| 跨组编号体系不同 | ordinal 归一化 + `numbering_mismatch` 守卫 | 数量相差 >34% 时标记 `aligned=False` 并提示 |
| 未标注的生肉被判成繁体 | 语言列显示不准 | 生肉剔除要求 `explicit`；可往 `taxonomy.raw_markers` 补词 |
| 历史回看用估算口径 | 每个组深查 16 部作品若也拉 Bangumi 会多 16 次请求 | 历史多为完结番、编号一致，`numbering_mismatch` 已足够 |
| 小样本 | 集数/作品数少时统计量不稳 | `confidence` 分级 + tier U + 覆盖率折算 |

---

## 7. 参数速查（`profiles/*.toml`）

| 键 | 默认 | 作用 |
| --- | --- | --- |
| `speed_T_hours` | 48 | 绝对/相对速度的衰减时间常数 |
| `stall_lag_days` | 10 | 落后多少天算「疑似弃坑」 |
| `silent_days` | 21 | 多久无发布算「安静」 |
| `drop_gap_days` | 14 | 判定弃番/作品是否安静的阈值 |
| `catchup_interval_hours` | 48 | 小于此间隔算攒集连发 |
| `speed_gain_threshold` | 0.15 | 容忍窗：风险更高的候选要快多少才值得确认 |
| `high_risk_veto` | true | tier C 是否直接剔除（设为 false 则保留但排在最后） |
| `unknown_tier_policy` | conservative | 小样本低风险组归 U 还是 A |
| `risk_tier.low` / `.high` | 0.15 / 0.35 | 风险分级边界 |
| `weights.{speed,lang,stability}` | 见 5.4 | 模式权重 |
| `lang_scores` / `softsub_bonus` | 见 5.3 | 语言偏好 |
| `history_works` | 16 | 历史回看的作品上限（CLI 的 `--works` 可覆盖） |
| `taxonomy.default_language` | 繁体 | 无语言标记时的默认值；设空串可关闭 |
| `taxonomy.raw_markers` | raw/raws/生肉/无字幕… | 生肉标记词 |
