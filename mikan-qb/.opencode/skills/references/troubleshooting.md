# 故障排查

## 退出码

| 码 | 含义 | 典型处理 |
| --- | --- | --- |
| `0` | 成功 | — |
| `1` | 参数 / 配置错误 | `mikan config validate` 后修正命令或 `config.toml` |
| `2` | 远端错误（Mikan / qB / Bangumi） | 见下方网络与代理 |
| `3` | 需人工决策 | 多候选、多托管记录、模拟未通过、已有进行中 job 等 |

## qB 连接

### HTTP 403（未认证 / 被拒）

- 检查 `.env` 的 `QB_HOST` / `QB_USERNAME` / `QB_PASSWORD` 是否正确。
- WebUI 必须开启 API，且**不能**勾选「Bypass authentication for clients on localhost」。
- 反向代理场景常见 **Host header** 问题：代理转发时的 Host 必须与 WebUI 预期一致，否则 403。

### 域名不可达 / 超时

- 直接探测：`uv run mikan qb check --json`，失败会带 `hint`。
- 确认本机能访问 `QB_HOST`（DNS / 端口 / 防火墙）。
- 注意分类：qB 的 HTTP 错误类名不以 `HTTP` 开头，且 `Conflict409Error` 继承自 `APIConnectionError`；判断错误类型要按 **HTTP 状态码**，否则 409 会被误报成「无法连接」。

### 409 Conflict（新增 feed）

同一个 RSS URL 出现在两个路径。qB 不允许 → 复用已有路径（`plan diff` 会显示 `reuse_existing`），不要新建。

## Mikan 抓取

- 失败信息会带出错 URL；先 `--refresh` 绕缓存重试，再 `mikan cache stats` / `mikan cache clear`。
- 站点改版会导致解析异常（集中在 `mikan_health/data/source.py`，用 fixtures 回归）。
- 限速 1 req/s + TTL 缓存，批量分析慢属正常。

## Bangumi（放送日程 / 覆盖率校验）

- Bangumi **必须走代理**。配置优先级：`.env` 的 `BGM_PROXY` → `HTTPS_PROXY` / `ALL_PROXY` 等 → Windows 注册表 → 直连。
- 注册表里的代理值可能没有 scheme，程序会自动补 `http://`（实测可用 `127.0.0.1:7890`）。
- Token：`.env` 的 `BGM_TOKEN`（可从 `../bangumi/token.txt` 复制），或 `config.toml` 的 `[bangumi] token_file`。
- 拿不到日程时会**自动退回**「开播日 + 7×集数」的估算口径（`--no-bangumi` 可显式关闭），不影响主流程。
- 番剧页无 `bgm.tv` 链接、或 subject 取不到时，`anime bangumi` 会以退出码 2 提示。

## 显示与调试

- 表格里正则含 `\[` 必须用 `rich.text.Text` 包一层，否则 rich 会把 `\[` 当标记吃掉、显示成 `[`（看起来像配置被破坏，实际落盘正确）。
- 想核对某个标题被判成什么：`uv run mikan tag parse "<title>" --json`。
- 想看完整 JSON 证据：任意命令加 `--json`。
