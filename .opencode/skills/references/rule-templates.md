# 规则模板与生成逻辑

规则由 `mikan plan add` / `mikan rule preview` 从**该字幕组实际发布过的全部变体**反推生成，**不要手写**。

## 核心思路：精确覆盖，而不是「挑一个版本」

```
变体 = (语言, 内封/内嵌, 标题里的括号标记)     例：简体|内嵌|简体内嵌
约束 = ∀ 集 e：|T ∩ S(e)| == 1                S(e) = 该集实际存在的变体集合
目标 = 在可行解中取偏好分最高者（语言分 + 内封加分），并列时取变体数最少
过滤 = mustContain = 各变体 token 的正则择一
     + mustNotContain = 其余 token + 打包正则
```

这同时覆盖三种现实情况：

- 组内多版本（简繁内封 / 简体内嵌 / 繁体内嵌）→ 只选一个；
- **季中换格式**（前 6 集简体内嵌、后 6 集简繁内封）→ 自动选**两个**变体组成 T；
- 同集多编码（720p + 1080p 同标记）→ 无解，**拒绝出计划**而不是静默下两份。

模拟必须满足 **无漏集、无双命中**，否则 `valid=false`、`plan add` 退出码 3。

## `rule_def` 结构（由 `Plan.rule_def` 生成）

```json
{
  "enabled": true,
  "mustContain": "简日双语",
  "mustNotContain": "繁日双语|\\[\\d{1,3}\\s*[-~—]\\s*\\d{1,3}\\]|合集|全卷|\\[(?:TV)?全集\\]",
  "useRegex": true,
  "episodeFilter": "",
  "smartFilter": false,
  "previouslyMatchedEpisodes": [],
  "affectedFeeds": ["https://<mikan>/RSS/Bangumi?bangumiId=3995&subgroupid=203"],
  "ignoreDays": 0,
  "lastMatch": "",
  "addPaused": null,
  "assignedCategory": "番剧",
  "priority": 0,
  "savePath": "/downloads/anime/无职转生",
  "torrentContentLayout": null,
  "torrentParams": {
    "category": "番剧",
    "save_path": "/downloads/anime/无职转生",
    "tags": ["mikan-3995", "简日双语"],
    "operating_mode": "AutoManaged"
  }
}
```

- 规则名（`ruleName`）来自 `rule_name_template = "{lang}/{anime}@{subgroup}"`，如 `简日/无职转生@喵萌奶茶屋`；`fast` 模式语言标签固定为 `追更`。**必须带字幕组**：否则换组时新旧规则同名，`set_rule` 会覆盖旧规则，`finalize`/`abort` 又会误删同名规则。
- `affectedFeeds` 必须与 `rss_add_feed` 的 url **逐字符一致**；规则按 URL 关联，feed 放哪个路径不影响生效。
- `torrentParams.tags` 是 W4 换组定位旧种子的**唯一可靠手段**（N2/N7）：至少含 `mikan-<bangumiId>` 与语言标签两个。
- `assignedCategory` / `savePath` / `torrentParams` 需同时设置，避免落盘路径不一致。

## 为什么是「每番一条规则」

现有存量是「少数几条全局语言规则 + 长 `affectedFeeds`」。新增订阅**不并入**旧规则，因为：

- `savePath` 与 `torrentParams.tags` 是整条规则一套，全局规则做不到每番独立目录与 `mikan-<id>` tag；
- `简繁内封` 不含「简体」子串，同一 feed 同时挂「简体」与「简繁」会**同集下两份**。

qB 规则没有「优先 A、缺失退 B」的表达能力，「内封优先」只能靠每番一条、正则从该组实际标签反推实现。旧规则的 `affectedFeeds` 是显式白名单，新增 feed 不会被误命中，两代可安全并存。

## 打包与重发

- 打包/合集由 `mustNotContain` 里的 `pack_patterns` 排除，只清单集。
- `v2/repack/proper` 由 qB **全局**设置 `rss_download_repack_proper_episodes` 控制，本项目默认关闭（`download_repack_proper = false`），以免同集重复下载。
- 去重靠「语言 token 唯一化」，**不要依赖 `smartFilter`**——qB 的 Smart Episode Filter 对 `[12]` 这类动漫命名不可靠。

## 相关命令

```bash
uv run mikan rule preview <bangumi_id> --subgroup <sid> --mode normal --json  # 离线模拟
uv run mikan plan add <bangumi_id> --subgroup <sid> --mode normal --json      # 生成计划
uv run mikan plan diff --bangumi <id> --subgroup <sid> --json                 # 与 qB 对比
uv run mikan qb rules show "<rule_name>" --json                               # 查看线上规则
uv run mikan qb rules match "<rule_name>" --json                              # 线上命中交叉校验
```
