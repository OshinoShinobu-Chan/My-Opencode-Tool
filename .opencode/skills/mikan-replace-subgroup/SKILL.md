---
name: mikan-replace-subgroup
description: Use when a currently subscribed fansub group (字幕组) has stalled, dropped the anime, or the user wants to switch to another group — 换组 / 替换字幕组 / 这个组不更了 / 弃番换组 / replace / finalize / abort. Covers the two-phase W4 transaction: write the new group first, verify coverage, then delete the old group.
---

# 换组替换（W4，两阶段事务）

> 所有命令需在仓库的 `mikan-qb/` 目录下执行（uv workspace 根；从仓库根进入时先 `cd mikan-qb`）。

**先建后删**：阶段一只写新组（feed + rule + 回填），不删任何旧内容；阶段二校验新组已下完，才删旧组种子/文件/规则/feed。
中途不满意可以 `abort` 回滚。所有写操作默认 dry-run，必须显式 `--apply`。

## 前置检查

```bash
uv run mikan state list --json          # 该番必须在托管清单里，且旧记录带 tag
uv run mikan replace --bangumi <id> --to <新 sid>            # dry-run
```

`replace` 的旧组定位：优先 `--from <旧 sid>`；省略时要求该番在清单中**唯一**，否则退出码 3 要求指定。
仅支持**带 tag 的新番订阅**（存量无 tag 种子不纳入自动化）。

**换组前先查黑名单**：新组 `--to <新 sid>` 不得命中 `../references/blocked-subgroups.md`；若原组本身在黑名单内（如因内嵌广告被拉黑），换组时按用户要求删除其种子与文件。

## 阶段一：写入新组（不删旧内容）

```bash
uv run mikan replace --bangumi <id> --to <新 sid> [--from <旧 sid>] \
    [--mode normal] [--strategy strict|force] [--backfill/--no-backfill] \
    [--paused] [--bgm/--no-bgm]
```

dry-run 输出 `actions`（feed / rule / torrents / old-torrents snapshot）与 `plan_file`。**把 actions 展示给用户确认**。

- `--strategy strict`（默认）：新组 `valid` 且有漏集会**拒绝**（退出码 3）。不要为了绕过而默认加 `--force`；先 `mikan rule preview` 查原因或换组。
- `--bgm`（默认）用 Bangumi 已播集数校验覆盖率；取不到日程时自动退回（见 `--no-bgm`）。
- 确认后加 `--apply`：

```bash
uv run mikan replace --bangumi <id> --to <新 sid> --apply
```

成功后返回 `job=<job_id>`，并提示旧组内容尚未删除。此时 state 里新旧记录短暂并存。

## 阶段二前：查看进度

```bash
uv run mikan replace status <job_id> --json
```

读 `completed` / `expected` / `missing` / `complete` / `torrents[].{episode,progress,completed}`。
- `missing` 非空 → 等新组补齐，或 `mikan apply backfill --bangumi <id> --subgroup <新 sid> --apply`。
- 长时间补不齐 → 评估 `abort`。

## 阶段二：删旧组（破坏性，必须先给用户看清单）

删除对象在阶段一已快照到 `jobs/<job_id>.json`。**执行前读取该文件的 `old_torrents[]`，把 `hash` 与 `name` 完整列给用户确认**（行为准则要求）。

```bash
uv run mikan replace finalize <job_id>          # dry-run：旧组种子数 / 旧 rule / 旧 feed
uv run mikan replace finalize <job_id> --apply  # 校验通过才删
```

- `missing` 非空时 finalize 默认**拒绝**并列出缺失集；确需强制才 `--force`（要说明后果）。
- 只想停用旧组、保留其种子继续做种：`--keep-torrents`。
- 仅删本工具创建的旧 feed；非本工具创建的 feed 一律保留（可 `--delete-feed` 强制，但需谨慎）。

## 回滚

```bash
uv run mikan replace abort <job_id>             # dry-run
uv run mikan replace abort <job_id> --apply     # 删新组 rule/feed（feed 非本工具创建则保留）
uv run mikan replace abort <job_id> --purge-new --apply   # 连新组已下种子+文件一起删
```

## 常见拒绝与处理

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 退出码 3，提示有多条托管记录 | 该番有多个组记录 | 用 `--from <旧 sid>` 指定 |
| 退出码 3，提示旧订阅缺 tag | 存量无 tag 订阅 | 不纳入自动化，用 `apply remove` 手动清理 |
| 退出码 3，`已有进行中的替换任务` | 同番上一个 job 未结束 | 先 `replace status` 再 `finalize` 或 `abort` |
| strict 拒绝 | 新组漏集/覆盖率不足 | `rule preview` 换新组，勿默认 `--force` |

## 参考

- 字幕组黑名单（换组前必查）：`../references/blocked-subgroups.md`
- 规则字段与匹配：`../references/rule-templates.md`
- qB 约定（tag / category / 保存路径）：`../references/qb-config.md`
- 报错排查：`../references/troubleshooting.md`
