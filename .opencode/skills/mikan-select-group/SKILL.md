---
name: mikan-select-group
description: Use when the user wants to pick a fansub group (字幕组) for a new anime and subscribe it in qBittorrent — 选番 / 选组 / 订阅 / 挑字幕组 / 开季追番 / 生成 RSS 规则 / plan add / apply add. Covers the full W1+W2 flow from season list to a written qB feed+rule.
---

# 选番选组与订阅（W1 + W2）

> 所有命令需在仓库的 `mikan-qb/` 目录下执行（uv workspace 根；从仓库根进入时先 `cd mikan-qb`）。

把「某季新番 → 选字幕组 → 生成 mikan RSS → 写入 qB feed + 自动下载规则」这条链路走完。
所有确定性动作都由 `mikan` CLI 完成；本 Skill 只负责**选择、解释与向用户确认**。

## 铁律（必须遵守）

1. **不手写正则、不拼 URL**：`mustContain` / `mustNotContain` / `feed_url` 一律来自 CLI（`rule preview` / `plan add` 的 JSON），禁止自行编造。
2. **破坏性操作默认 dry-run**：`apply add` / `apply backfill` 不加 `--apply` 只预览；必须先把将执行的 `actions` 展示给用户并得到确认，再加 `--apply`。
3. **涉及选择时必须给用户选项**：候选组 ≥2、`decision_required=true`、出现 tier U 组、或只有 tier C 可选时，把选项和证据摆给用户，不要自行拍板。
4. **缺数据不下结论**：候选 `confidence=low` 时只陈述事实，不给风险判断。
5. **先查黑名单**：选组前必须读 `../references/blocked-subgroups.md`，命中的字幕组一律排除，不得作为候选或推荐。

## 前置检查

```bash
uv run mikan config validate            # 配置与标签词典
uv run mikan qb check --show-global     # qB 连通性与 rss_* 全局设置
```

若 `rss_download_repack_proper_episodes = True`，订阅前提示用户（会让同集 v2/repack 重复下载）：

```bash
uv run mikan qb rules repack-proper --set off --apply    # 默认 dry-run，确认后再 --apply
```

## 标准流程

### 1. 拿季度片单

```bash
uv run mikan season list --year 2026 --season 夏 --json
uv run mikan anime search "<关键词>" --json        # 片单里找不到时搜索
```

读 `bangumi_id` / `title` / `weekday_cn`。**把片单交用户确认要看哪几部**（本 Skill 不替用户决定追什么）。

### 2. 看候选字幕组

```bash
uv run mikan anime info <bangumi_id> --json
uv run mikan anime subgroups <bangumi_id> --json
```

`subgroups[].subtitle_group_id` 就是后续要用的 `sid`；同时给出各组 `languages` / `sub_types` / `last_update`。
注意 `subgroupid`（RSS，本工具用）**不等于** `publish_group_id`（年表页），不要混用。

### 3. 评估与排序（核心判断）

```bash
uv run mikan subgroup rank <bangumi_id> --mode normal --json
uv run mikan subgroup rank <bangumi_id> --mode fast   --json   # 只看速度
```

读 `candidates[]`：`tier`（A 低风险 / B 中 / U 未知 / C 高）、`risk`、`confidence`、`score`、`speed_score`、`lang_match`、`missing_vs_peer`、`peer_gap_h`、`languages`、`evidence`、`recommendation`、`decision_required`、`decision_options`、`warnings`。

- `recommendation` 存在且 `decision_required=false` → 可作为推荐给用户。
- `decision_required=true` → **必须**把 `recommendation` 与 `decision_options` 并排呈现（容忍窗裁决），让用户选。
- tier U（confidence=low）→ 只说「数据不足」，不报风险数值。
- tier C → 默认不可选；仅当 A/B/U 全空时才提出并要求人工确认。

### 4. 离线模拟过滤规则

```bash
uv run mikan rule preview <bangumi_id> --subgroup <sid> --mode normal --json
```

读 `reports[0]`：`valid` 必须为 `true`；`variant_label` 是选用的版本，`gaps` 必须为空，`multiple` 必须为空，`must_contain` / `must_not_contain` 即最终正则。
`valid=false` 时按其 `reason` 向用户解释（同集多编码 / 无中文标记 / 只有合集等），换候选组。

### 5. 生成计划

```bash
uv run mikan plan add <bangumi_id> --subgroup <sid> --mode normal --season 2026夏 --json
```

- 退出码 `3` 表示模拟/覆盖率未通过（默认不落盘生效），不要加 `--force` 绕过后继续；先回到第 4 步。
- 读 `feed_path` / `feed_url` / `rule_name` / `rule_def` / `variants` / `matches` / `gaps` / `warnings` / `valid`。

### 6. 与 qB 现状对比

```bash
uv run mikan plan diff --bangumi <bangumi_id> --subgroup <sid> --json
```

`differences[].action`：`create` / `update` / `unchanged` / `reuse_existing` / `update_url`。
`reuse_existing` 说明该 RSS URL 已在别的路径（qB 不允许同一 URL 两个路径），规则按 URL 关联，**复用即可**。

### 7. 写入（先 dry-run，再确认，再 --apply）

```bash
uv run mikan apply add --bangumi <bangumi_id> --subgroup <sid>            # dry-run，展示 actions
uv run mikan apply add --bangumi <bangumi_id> --subgroup <sid> --backfill --apply
```

- **展示 dry-run 的 `actions` 给用户，确认后才加 `--apply`**。
- `--disabled` 可先以禁用状态写规则做安全演练（P2 用过），验证 `qb rules match` 后再启用。
- 已播出集数要补齐时用 `--backfill`，或单独：

```bash
uv run mikan apply backfill --bangumi <bangumi_id> --subgroup <sid> --apply
```

### 8. 线上交叉校验

```bash
uv run mikan qb rules list --json
uv run mikan qb rules match "<rule_name>" --json      # rule_name 形如 简日/番名@字幕组
```

命中标题应与第 4 步离线模拟的 `variant_label` 一致（无繁体、无打包）。不一致要报告用户，不要静默。

## 输出与交接

- 汇总：番剧 / 字幕组（sid）/ 模式 / 选用版本 / feed 路径 / 规则名 / 命中集数 / 落盘目录 / 警告。
- 订阅成功后托管清单会自动登记，可用 `uv run mikan state list --json` 复核。
- 若用户只想移除，用 `mikan apply remove --bangumi <id> [--subgroup <sid>] [--apply]`（默认保留种子与文件）。

## 下载剧集到本地待观看目录

当用户要求「把某些动画剧集下载到本地观看」时，统一存到 `mikan-qb/tmp-watch/`（`mikan-qb/.gitignore` 已忽略该目录，不入库）。

qB 在远程主机，文件不在本机；需经 SSH 从服务器取回：

- SSH 主机别名：`homelab`（见 `~/.ssh/config`，root，密钥认证）；远端 shell 是 **Nushell**（用 `;` 不用 `&&`，别直接用 `|` 过滤中文字符串，易被引号转义搞坏，优先一条命令一个 `ls`）。
- qB 容器 `qbittorrent` 把 `/downloads` 映射到宿主 `/mnt/nas/qbittorrent`；种子的 `save_path` 如 `/downloads/anime/<番名>` → 宿主 `/mnt/nas/qbittorrent/anime/<番名>`。
- 用种子名（`torrents_info` 的 `name`）在宿主目录定位文件，再 `scp` 到本机 `tmp-watch/`。

```bash
ssh homelab "ls /mnt/nas/qbittorrent/anime"          # 找到番名目录
scp "homelab:/mnt/nas/qbittorrent/anime/<番名>/<文件名>" "tmp-watch/"
```

不要改动 qB 里的种子状态；本操作只读服务器文件。

## 参考

- 字幕组黑名单（选组前必查）：`../references/blocked-subgroups.md`
- 标签与语言判定：`../references/taxonomy.md`
- 规则字段与生成逻辑：`../references/rule-templates.md`
- qB 约定与现状：`../references/qb-config.md`
- 报错排查：`../references/troubleshooting.md`
