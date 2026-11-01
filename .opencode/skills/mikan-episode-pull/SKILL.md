---
name: mikan-episode-pull
description: Use when the user asks to 把某一集/某几集下载到本地 (下载到本地 / 拉到本机 / 传到这台电脑 / 把第 N 集弄下来). Assumes the anime is already subscribed in the remote qBittorrent and means "copy the finished video from the NAS into this machine's mikan-qb/tmp-watch" — pulls over SSH, never re-downloads.
---

# 把已订阅的剧集拉回本机

用户说「把 XX 第 N 集下载到本地」时的默认语义（不必再确认）：

- 该番**已经在远端 qBittorrent 订阅并下载完成**，不需要找种子、不需要重新下载。
- 「本地」= **当前这台 Windows 机器**，落盘到 `<repo>/mikan-qb/tmp-watch/<番剧名>/`。
- 只做「复制到本机」：**不删除远端文件**、不改 qB 的种子状态、不改订阅规则。

关键限制：**qB 的 WebUI/API 没有文件下载接口**，拿不到视频本体，所以文件必须走 **SSH/SFTP** 从 NAS 复制；qB API 只用来「定位是哪个文件、下完没有」。

## 环境事实

| 项 | 值 |
| --- | --- |
| qB WebUI | `mikan-qb/.env` 的 `QB_HOST`（当前 `http://www.crimmy.top:8000`），v5.0.3 / WebAPI 2.11.2 |
| SSH | `ssh homelab`（`~/.ssh/config`：`www.crimmy.top:22`，`root`，`id_ed25519` 免密） |
| 远端登录 shell | **nushell**，不是 bash |
| 容器内路径 | `/downloads/...`（qB 的 `save_path` / `content_path` 都是这个形式） |
| 宿主路径 | `/mnt/nas/qbittorrent/...`（容器的 `/downloads` 挂载在 NAS 的这个目录） |
| 本机目标目录 | `C:\data\My-Opencode-Tool\mikan-qb\tmp-watch\<番剧名>\` |
| 实测链路 | 约 2.9 Mbps（260 MB ≈ 12 分钟），**批量一定放后台** |

## 步骤

### 1. 在远端 qB 定位种子

```powershell
cd C:\data\My-Opencode-Tool\mikan-qb
uv run python ..\.opencode\skills\mikan-episode-pull\scripts\qb_locate.py "Tetsuryou"
```

脚本读 `.env` 登录，按名字（不区分大小写）匹配种子，输出每个命中的：

- `container_path`：qB 里的绝对路径（`/downloads/...`）
- `host_path`：换算后的 NAS 路径（`/mnt/nas/qbittorrent/...`）
- `progress` / `state` / `size`

匹配词用**文件名里的英文/罗马字部分**最稳（mikan 番名是中文，但文件名常是罗马字，如 `Tetsuryou`）。也可以直接给中文番名。

### 2. 确认已经下完

- `progress = 1.0` 且 `state` 属于 `stoppedUP` / `uploading` / `pausedUP` → 可直接复制。
- `progress < 1.0` → **不要复制半成品**，先告诉用户「远端还在下（xx%）」，问是等还是由你 `resume`。
- 找不到种子 → 说明订阅没命中或还没下，交回 `mikan status` / `mikan-select-group` 判断，**不要自己从 mikan 抓种子重下**。

### 3. 在远端核对文件（nushell 语法）

```powershell
ssh homelab "ls /mnt/nas/qbittorrent/anime/**/*Tetsuryou* | select name size"
```

- 交互 shell 是 nu：`ls -lah`、`find -iname`、`bash -lc "..."` 都可能直接报 `unknown flag` / 引号被吃掉。**优先用 `ls` + `**/*` glob + `| select`**。
- 想看精确路径就用 `qb_locate.py` 输出的 `host_path`，不要靠猜中文目录名。

### 4. 复制到 tmp-watch（后台）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  ..\.opencode\skills\mikan-episode-pull\scripts\pull.ps1 `
  -RemoteGlob "anime/铁旅！邂逅铁道少女/*Tetsuryou*0[23]*" `
  -Destination "C:\data\My-Opencode-Tool\mikan-qb\tmp-watch\铁旅！邂逅铁道少女"
```

脚本会：建目录 → `scp` 拉取 → 逐个比对远端 `md5sum` 与本地 `Get-FileHash` → 打印大小与校验结果。

`-DryRun` 只列远端命中文件、不传输，用于先确认选对了集。

> 传输耗时长时用后台执行（`background: true`），不要在前台干等；传完后按第 5 步复核。

### 5. 复核并汇报

- 大小与清单一致、md5 全部 `OK` 才算成功。
- 汇报：番名、集号、版本（Baha MP4 / ABEMA MKV 等）、文件路径、总大小。
- 传输中断留下的**半截文件必须删掉**（见「已知坑」），否则会伪装成完整视频。

## 已知坑

1. **scp 没有断点续传，长传输断线要用 sftp `reget`**：链路不稳时 `client_loop: send disconnect` 会中断，
   已下载的部分仍留在目标目录。先保留半截文件，用 sftp 续传（比删除重传省一半以上流量）：

   ```powershell
   # 先把命令写进 UTF-8 无 BOM 的批处理文件，避免 PowerShell 引号被吞
   'reget "<远端绝对路径>" "<本机绝对路径>"' | Set-Content -Encoding utf8 $env:TEMP\resume.txt
   sftp -b "$env:TEMP\resume.txt" homelab
   ```

   续传后必须比对 md5（见第 5 步）；不匹配再删掉重来。
2. **`-RemoteGlob` 里不要出现空格**：远端是 nushell，会把 `* - 05 *` 拆成四个参数喂给 `md5sum`，
   命令直接卡死（表现为 dry-run 长时间无输出）。用无空格模式，如 `anime/学生会也有洞！/*05*.mp4`。
3. **PowerShell 5.1 传参与编码**：中文路径实测可正常传给 `ssh`/`scp`；但不要用 `bash -lc "..."` 这类多层引号，`nu` 会超时或报错。需要远端精确路径时直接用双引号包一整条命令。
4. **`Get-FileHash` 遇到方括号会当成通配符**：文件名里有 `[Dynamis One]`、`[CA46A1B3]` 这类方括号时，必须用 `-LiteralPath`，否则返回 null。
5. **本地目录名含 `！` `：` 等全角符号**：PowerShell 里用单引号包路径；`Remove-Item`/`Get-ChildItem` 同样建议 `-LiteralPath`。
6. **`crimmy.top:8000` 需要登录**：未认证的 `/api/v2/app/version` 返回 403，不代表服务挂了。凭据只在 `mikan-qb/.env`，不要打印到对话里。
7. **远端 `md5sum` 可用**（外部命令），但 `find` / `head` 等要小心 nu 内置同名命令。

## 边界

- 不删除、不移动、不重命名 NAS 上的原始文件。
- 不为了「下载到本地」去改 qB 规则、feed 或种子状态。
- 用户要的是「本机能不能看」，不是归档；默认只复制指定集，不顺手拉整季。
