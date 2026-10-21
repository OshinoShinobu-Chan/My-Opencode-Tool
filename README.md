# My-Opencode-Tool

个人使用的 opencode 自动化工具集合，目前包含两个工具：

| 工具 | 作用 | 技术栈 |
| --- | --- | --- |
| [bangumi](#bangumi) | Bangumi 番组计划管理（收藏 / 打分 / 单集 / 吐槽） | Python 3.8，仅标准库 |
| [mikan-qb](#mikan-qb) | Mikan 新番订阅与 qBittorrent RSS 自动化 | Python ≥3.12，uv workspace |

## bangumi

Bangumi（bgm.tv）番组计划管理 CLI 与 Agent Skill，单文件实现：`bangumi/scripts/bgm.py`。
基于官方 API `api.bgm.tv/v0`，单集吐槽通过网页解析（官方 API 无此接口）。

- **搜索**：按动画标题查条目，区分 TV / 剧场版 / 不同季
- **收藏**：标记想看 / 在看 / 看过 / 搁置 / 抛弃
- **打分与单集**：给条目评分、把某一集标记为看过
- **放送检查**：`airing` 列出收藏中正在放送的动画（日历 + 章节放送日 + 估算）
- **吐槽抓取**：抓取某一集的吐槽 / 楼中楼，支持 `--json`

```powershell
python bangumi/scripts/bgm.py search "葬送的芙莉莲" --type 2
python bangumi/scripts/bgm.py mark 400602 --status doing
python bangumi/scripts/bgm.py comments --subject 400602 --ep 1
```

写操作需要 Bangumi 个人 access token，放在 `bangumi/token.txt`（已排除入库）或环境变量 `BANGUMI_TOKEN`。
详见 [`.opencode/skills/bangumi/SKILL.md`](.opencode/skills/bangumi/SKILL.md)。

## mikan-qb

Mikan 新番订阅与 qBittorrent RSS 管理工具，uv workspace 包含两个包：

- **`packages/mikan-qb`**（CLI `mikan`）：抓取解析 Mikan 番剧页 / 年表 / RSS / 搜索 / 季表；
  生成精确覆盖正则的订阅规则并 `plan` / `apply`；字幕组健康度评分、停更风险排序；
  换组两阶段事务（`replace` / `status` / `finalize` / `abort`）；qB tag / state / cache / cleanup 维护。
- **`packages/mikan-health`**：独立的字幕组健康度评估算法包，零 qB 依赖，
  评分标准外置在 `profiles/*.toml`，改标准不用改代码。

另含 `tools/sitewide/`（全站字幕组长期画像评估，2016–2026 报告）与 4 个 Agent Skill（见 `.opencode/skills/`）。

```bash
uv run mikan config validate          # 配置与 qB 连通性自检
uv run mikan season list --year 2026 --season 夏
uv run mikan plan add <id> --subgroup <sid>
uv run mikan apply add --apply        # 写操作默认 dry-run
uv run mikan status
```

文档：能力摘要 [`mikan-qb/PLAN.md`](mikan-qb/PLAN.md)、指标与算法 [`mikan-qb/METRICS.md`](mikan-qb/METRICS.md)、
全站评估 [`mikan-qb/SITEWIDE.md`](mikan-qb/SITEWIDE.md)。

## 目录结构

```
.
├── .opencode/skills/   # Agent Skills（bangumi + mikan 系列 4 个 + 共享 references）
├── bangumi/            # Bangumi 番组计划 CLI（bgm.py）
├── mikan-qb/           # Mikan + qBittorrent 订阅管理（uv workspace）
└── .gitignore
```

## 说明

- `bangumi/token.txt`、`mikan-qb/.env` 等本地凭据已被 `.gitignore` 排除，仓库内只保留 `.env.example` 模板。
- 两个工具均可由 opencode agent 通过 `.opencode/skills/` 中的 Skill 调用（mikan-qb 另有 `AGENTS.md` 约定）。
