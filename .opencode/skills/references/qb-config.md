# qBittorrent 约定与现状

操作方式：Python 包 `qbittorrent-api`（封装在 `mikan_qb.qb.QBClient`），不手写 HTTP。凭据在 `.env`（不入库），`mikan config show` 会打码。

## 连接与自检

```bash
uv run mikan qb check --show-global --json     # 版本 / WebAPI / feed 数 / 规则数 / rss_* 全局设置
uv run mikan qb feeds --json                   # RSS 目录与订阅树
uv run mikan qb rules list --json              # 现有规则
uv run mikan qb rules show "<name>" --json     # 单条规则完整定义
```

已知环境（P0 实测，会随部署变化，以 `qb check` 为准）：qBittorrent v5.0.3 / WebAPI 2.11.2；服务器 Linux（`savePath` 形如 `/downloads/...`）；全局默认保存路径 `/downloads`。

## RSS feed 两条硬性事实

1. **qB 不允许同一 RSS URL 出现在两个路径**（`addFeed` 返回 409 Conflict）。目标 URL 已存在于其他路径时必须**复用**，不要新建。
2. 规则的 `affectedFeeds` 存的是 URL，**feed 放在哪个路径不影响规则生效**。

新增 feed 时，`addFolder` 要建的是 **feed 路径的父目录**（如 `2026夏/`），不能把 feed 路径本身建成目录，否则 `addFeed` 因同名路径已存在而 409。

## feed 路径模板

- `feed_folder_template = "{season}"` → `2026夏`
- `feed_name_template = "{anime}@{subgroup}"` → `无职转生@桜都字幕组`
- **路径必须带字幕组**：换组时新旧两个 feed 要能共存，否则撞车 409。

现有 21 个存量 feed 全部**平铺、无文件夹**，路径名 = RSS 频道标题（如 `Mikan Project - 碧蓝之海 第三季`），与新的 `赛季/番名` 结构不一致。**不迁移，两代并存**（N5）；新增订阅不会被旧规则意外命中。

## category / 保存路径 / tag

| 项 | 取值 |
| --- | --- |
| category | 统一 `番剧`（存量种子无 category，新番才打） |
| save_root | `/downloads/anime` |
| save_path_template | `{root}/{anime}` → `/downloads/anime/<番名>`（**不分季度**，N4） |
| tag | 双 tag：`mikan-<bangumiId>` + 语言标签（`简体`/`繁体`/`简日`/`简繁`/`追更`） |

category 的 `savePath` 必须是通用的 `save_root`，不能写成每番路径；每番目录由规则的 `savePath` / `torrentParams.save_path` 负责。

## `rss_*` 全局设置（P0 实测，执行前先 `--show-global` 复核）

| 设置 | 实测值 | 说明 |
| --- | --- | --- |
| `rss_auto_downloading_enabled` | True | 自动下载总开关 |
| `rss_processing_enabled` | True | RSS 处理开关 |
| `rss_download_repack_proper_episodes` | True → 本项目**要关闭** | 开启会重复下载同集 v2/repack |
| `rss_refresh_interval` | 30 | feed 刷新间隔（分钟） |
| `rss_fetch_delay` | 2 | |
| `rss_max_articles_per_feed` | 50 | 24 集 ×3 版本 = 72 > 50，早期条目会被丢弃 → **回填不能依赖 RSS**，走番剧页逐集表 |
| `rss_smart_episode_filters` | `s(\d+)e(\d+)` 等 | **不含动漫命名**，对 `[12]` 无效，去重只能靠语言 token |

关闭重发下载：

```bash
uv run mikan qb rules repack-proper --set off          # dry-run
uv run mikan qb rules repack-proper --set off --apply  # 执行
```

## 存量规则与两代共存

存量是「少数几条全局语言规则 + 长 `affectedFeeds`」（如 `CR` / `all in` / `简体` / `简日` / `简繁`），`assignedCategory` 与 `tags` 多为空，`savePath` 为 `/downloads/anime`。新方案是**每番一条规则**，详见 `rule-templates.md`。两代可共存，但新增订阅不要并入旧全局规则。
