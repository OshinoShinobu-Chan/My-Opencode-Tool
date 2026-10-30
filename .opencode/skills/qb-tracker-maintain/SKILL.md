---
name: qb-tracker-maintain
description: Use when the user wants to 整理/检查/清理 qBittorrent 的 tracker（tracker 太多太乱、种子没速度、想加公共 tracker、更新 tracker 列表）. Covers audit → probe from the real host → dry-run plan → backup → apply → verify.
---

# qBittorrent tracker 体检与整理

解决三类问题：**tracker 数量爆炸**（批量注入导致每种子几千条）、**死链拖慢 announce**、**公共 tracker 列表过期**。

铁律：**先探测再决定，先备份再动手，动之前给 dry-run**。

## 环境事实

| 项 | 值 |
| --- | --- |
| qB WebUI | `mikan-qb/.env` 的 `QB_HOST`（当前 `http://www.crimmy.top:8000`） |
| API | v5.0.3 / WebAPI 2.11.2，凭据同 `.env` 的 `QB_USERNAME` / `QB_PASSWORD` |
| 服务器 | `ssh homelab`（`www.crimmy.top:22`，root，密钥免密） |
| 远端 shell | **nushell**（`ls`/`which` 是内建；复杂脚本 scp 上去用 `python3` 跑） |
| 远端 python | `/usr/bin/python3`（3.10，标准库够用） |
| 容器路径 | `/downloads/...` ↔ 宿主 `/mnt/nas/qbittorrent/...` |
| 本地仓库 | `C:\data\My-Opencode-Tool\mikan-qb`（`uv run python` 有 requests） |

**探测必须在服务器上跑**：tracker 可用性取决于 announce 的那台机器。实测 `tr.bangumi.moe`、`t.acg.rip` 从本机看正常，从服务器 100% 超时。

## 脚本一览

| 脚本 | 运行位置 | 作用 |
| --- | --- | --- |
| `scripts/qb_audit.py` | 本地 | 只读体检：条目数、分桶、状态、全局设置；`--csv` 导出明细 |
| `scripts/fetch_lists.py` | 本地 | 拉 ngosang / XIU2 榜单，合并成 `candidates.txt`（url / 来源 / 榜单排名） |
| `scripts/qb_probe.py` | **服务器** | UDP 走 BEP15 握手、HTTP(S) 走 TCP，失败重试 3 次，输出 `probe.csv` |
| `scripts/plan_trackers.py` | 本地 | 按「双榜共推 > 榜单排名 > 延迟」挑 N 条，可叠加中文圈清单 |
| `scripts/qb_tracker_apply.py` | 本地 | 备份 → 改设置 → 裁剪/删死链 → 复核；**默认 dry-run** |
| `scripts/cn-extras.txt` | — | 中文圈优先保留清单（nyaa.cn / dler / zhuqiy / itzmx / nekomi / renfei） |

## 流程

### 1. 体检

```powershell
cd C:\data\My-Opencode-Tool\mikan-qb
uv run python ..\.opencode\skills\qb-tracker-maintain\scripts\qb_audit.py --csv "$env:TEMP\qb_trackers.csv"
```

判断标准：

- 单种子 **> 200 条**基本就是被批量注入过，属异常（正常磁力链自带 20–60 条）
- `add_trackers_enabled=True` 且列表很长 → 新种子会继续膨胀
- 失效（status=4）占比高 → announce 在空等超时

### 2. 取最新榜单

```powershell
python ..\.opencode\skills\qb-tracker-maintain\scripts\fetch_lists.py -o "$env:TEMP\tracker_lists"
```

会打印各仓库最后提交时间（两家都在日更），并把四份榜单（best/all）合并去重成 `candidates.txt`。

### 3. 在服务器实测

```powershell
scp "$env:TEMP\tracker_lists\candidates.txt" homelab:/tmp/candidates.txt
scp ..\.opencode\skills\qb-tracker-maintain\scripts\qb_probe.py homelab:/tmp/qb_probe.py
ssh homelab "python3 /tmp/qb_probe.py /tmp/candidates.txt /tmp/probe.csv"
scp homelab:/tmp/probe.csv "$env:TEMP\tracker_lists\"
```

- UDP 用 **BEP15 connect 握手**（magic `0x41727101980` + action=0），真正验证 tracker 活着
- HTTP/HTTPS 用 TCP 连接，4 s 超时
- 失败自动重试 3 次（间隔 2 s），避免 UDP 丢包误判
- 实测参考：127 条候选约 109 条可用

### 4. 生成方案（dry-run）

```powershell
python ..\.opencode\skills\qb-tracker-maintain\scripts\plan_trackers.py `
  --probe "$env:TEMP\tracker_lists\probe.csv" `
  --target 18 `
  --extra ..\.opencode\skills\qb-tracker-maintain\scripts\cn-extras.txt `
  --out "$env:TEMP\tracker_lists\recommended.txt"
```

排序规则：**双榜共推 > best 榜单排名（越前越主流）> 实测延迟**，同主域最多 2 条；`--extra` 里的条目只要实测可达就优先保留（用于中文圈）。

把清单和「白名单条数 / 预计每种子保留几条」给用户确认，**不要自己拍板**。

### 5. 备份并执行

```powershell
# 默认 dry-run，只打印将要做什么
uv run python ..\.opencode\skills\qb-tracker-maintain\scripts\qb_tracker_apply.py `
  --whitelist "$env:TEMP\tracker_lists\recommended.txt" `
  --trim --dead-hosts "tr.bangumi.moe,t.acg.rip" `
  --set-add-trackers "$env:TEMP\tracker_lists\recommended.txt" `
  --announce-to-all-tiers false

# 确认后加 --apply
```

脚本行为：

1. **备份**全部种子的 tracker 列表 + 三个相关设置到 `qb_tracker_backup_<时间>.json`（`--apply` 时强制）
2. `--set-add-trackers <file>`：写 `add_trackers` 并 `add_trackers_enabled=True`
3. `--trim`：每种子只保留与白名单的交集，交集为空时保留原前 5 条兜底
4. `--dead-hosts`：按 **hostname** 删除（比完整 URL 稳，能覆盖 http/udp 多入口）
5. `--apply` 后自动复核：`add_trackers` 状态、条数、残留死链种子数

### 6. 复核

```powershell
uv run python ..\.opencode\skills\qb-tracker-maintain\scripts\qb_audit.py --json
```

确认条目数大幅下降、无残留死链、自动添加列表符合预期、正在下载的种子速度和连接数没异常。

## 经验值

| 场景 | 结论 |
| --- | --- |
| 正常磁力链 | 自带 20–60 条 |
| 批量注入的坏种子 | 2,800 条左右（ngosang all + 私有列表叠加） |
| 每种子保留 | **9–25 条**足够；14 条核心 + 磁力自带即可 |
| 自动添加列表 | ≤14 条；142 条会把新种子直接拖成 160+ 条 |
| `announce_to_all_tiers` | 建议 **false**（按层轮询）；`announce_to_all_trackers` 可留 true |
| 死链判定 | 探测 3 次全失败，或同一 host 在服务器侧稳定超时 |

## 已知坑

1. **PowerShell 5.1 的 `python -c "..."` 会被吃引号**：复杂逻辑写成 `.py` 再执行。
2. **`.ps1` 必须 UTF-8 with BOM**，否则中文注释让 PS 5.1 解析失败。
3. **`Get-FileHash` 遇 `[xxx]` 当通配符**：用 `-LiteralPath`。
4. **远端是 nushell**：`bash -lc "..."` 常被吃引号；`find -iname`、`ls -lah` 报 `unknown flag`。脚本 scp 上去跑 python3 最稳。
5. **UDP 探测会假失败**：单次 timeout 不代表死，必须重试；连续 3 次超时基本可判死。
6. **`removeTrackers` 偶发 `Remote end closed connection`**：单颗重试即可，不要当整体失败。
7. **别动 DHT/PeX/LSD 伪条目**：不是真 tracker，也删不掉；统计时按 `http|https|udp|ws` 过滤。
8. **删 tracker 不影响已下载文件**，但小众老种若只靠私有 tracker 找 peer 可能变慢 —— 必须给 dry-run，并保留 nyaa / 中文圈主力。
9. **PowerShell 写文件默认带 BOM**：给 `qb_probe.py` 的 `candidates.txt` 手工生成时要留意，脚本已按 `utf-8-sig` 兼容。

## 边界

- 不改种子的分类、保存路径、队列、限速。
- 不删种子、不删文件。
- 白名单必须来自**实测**，不要凭记忆手写“著名 tracker”。
- 每次调整后保留备份，用户明确说删再删。
