# mikan-health

Mikan 字幕组健康度评估的独立算法包。零 qBittorrent 依赖，可单独运行、单独修改评估标准。

```bash
uv run mikan-health profiles
uv run mikan-health show personal-normal --json
```

评分标准全部外置在 `profiles/*.toml`，改标准不需要改代码。

- **每个指标的含义与算法**：[`../../METRICS.md`](../../METRICS.md)
- 整体计划与决策：仓库根目录 `PLAN.md`（完成摘要）
