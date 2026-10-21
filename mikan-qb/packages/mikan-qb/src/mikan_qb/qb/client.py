"""qBittorrent WebUI 客户端：登录、RSS feed / 规则与种子操作。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

import qbittorrentapi

from mikan_qb.config import EnvSettings


class QBError(Exception):
    """qBittorrent 调用失败，可携带提示与 HTTP 状态码。"""

    def __init__(self, message: str, hint: str | None = None, status: int | None = None) -> None:
        super().__init__(message)
        self.hint = hint
        self.status = status


@dataclass
class QBStatus:
    """连接成功后的 qB 状态摘要。

    Attributes:
        host: qB WebUI 地址。
        version: qB 版本。
        webapi_version: WebAPI 版本。
        rss_settings: 所有 rss_* 全局偏好。
        feed_count: RSS feed 数量。
        rule_count: RSS 规则数量。
    """

    host: str
    version: str = ""
    webapi_version: str = ""
    rss_settings: dict[str, Any] = field(default_factory=dict)
    feed_count: int = 0
    rule_count: int = 0


class QBClient:
    """封装 qbittorrentapi 的高层客户端。"""

    def __init__(self, env: EnvSettings) -> None:
        if not env.qb_host:
            raise QBError("未配置 QB_HOST", hint="在 .env 中填写 QB_HOST=http://host:port")
        self._env = env
        self._login_retries = 3
        self._client = qbittorrentapi.Client(
            host=env.qb_host,
            username=env.qb_username or None,
            password=env.qb_password or None,
        )

    @property
    def raw(self) -> qbittorrentapi.Client:
        """底层的 qbittorrentapi.Client 实例。"""
        return self._client

    def connect(self) -> QBStatus:
        """登录并返回状态摘要；失败时抛 QBError（含有限次重试）。"""
        last_error: Exception | None = None
        for attempt in range(self._login_retries):
            try:
                self._client.auth_log_in()
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001
                if isinstance(
                    exc,
                    (
                        qbittorrentapi.LoginFailed,
                        qbittorrentapi.Forbidden403Error,
                        qbittorrentapi.Unauthorized401Error,
                    ),
                ):
                    raise self._translate(exc) from exc
                last_error = exc
                if attempt + 1 < self._login_retries:
                    time.sleep(1.5 * (attempt + 1))
        if last_error is not None:
            raise self._translate(last_error) from last_error

        try:
            status = QBStatus(
                host=self._env.qb_host,
                version=self._client.app_version(),
                webapi_version=str(self._client.app_web_api_version()),
            )
            status.rss_settings = self._rss_settings()
            status.feed_count = self._count_feeds()
            status.rule_count = len(self._client.rss_rules())
            return status
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc

    def rss_settings(self) -> dict[str, Any]:
        """返回所有 rss_* 全局偏好。"""
        return self._rss_settings()

    def feeds(self, with_data: bool = False) -> list[dict[str, str]]:
        """以扁平列表返回 RSS 树（含目录与 feed）。

        Args:
            with_data: 是否附带每个 feed 的详细数据。
        """
        return walk_feeds(self._client.rss_items(with_data=with_data))

    def add_folder(self, path: str) -> None:
        """创建 RSS 目录；已存在时视为成功（幂等）。

        Args:
            path: 要创建的目录路径。
        """
        try:
            self._call(self._client.rss_add_folder, folder_path=path)
        except QBError as exc:
            if exc.status == 409:
                return
            raise

    def ensure_feed_parent(self, feed_path: str) -> None:
        """确保 feed 路径的**父目录**存在。

        注意不能把 feed_path 本身建成目录——那样 addFeed 会因为同名路径已存在而 409。

        Args:
            feed_path: feed 的完整路径，仅取其父目录。
        """
        parent = PurePath(feed_path).parent
        if str(parent) in ("", "."):
            return
        self.add_folder(str(parent).replace("\\", "/"))

    def add_feed(self, url: str, item_path: str, refresh_interval: int) -> None:
        """新建 RSS 订阅。

        Args:
            url: 订阅源 URL。
            item_path: feed 在 RSS 树中的路径。
            refresh_interval: 刷新间隔（分钟）。
        """
        self._call(
            self._client.rss_add_feed,
            url=url,
            item_path=item_path,
            refresh_interval=refresh_interval,
        )

    def remove_item(self, path: str) -> None:
        """删除指定路径的 RSS feed 或目录。

        Args:
            path: feed 或目录的路径。
        """
        self._call(self._client.rss_remove_item, item_path=path)

    def rules(self) -> dict[str, dict]:
        """返回 {规则名: 规则定义} 映射。"""
        return self._call(self._client.rss_rules)

    def rule(self, name: str) -> dict | None:
        """取单条规则，不存在返回 None。

        Args:
            name: 规则名。
        """
        return self.rules().get(name)

    def set_rule(self, name: str, rule_def: dict) -> None:
        """写入或覆盖一条 RSS 自动下载规则。

        Args:
            name: 规则名。
            rule_def: qB 规则定义字典。
        """
        payload = dict(rule_def)
        payload.setdefault("priority", 0)
        self._call(self._client.rss_set_rule, rule_name=name, rule_def=payload)

    def remove_rule(self, name: str) -> None:
        """删除指定规则。

        Args:
            name: 规则名。
        """
        self._call(self._client.rss_remove_rule, rule_name=name)

    def matching_articles(self, rule_name: str) -> dict[str, list[str]]:
        """返回规则当前命中的 feed → 标题列表。

        Args:
            rule_name: 规则名。
        """
        return self._call(self._client.rss_matching_articles, rule_name=rule_name)

    def ensure_category(self, name: str, save_path: str = "") -> bool:
        """确保分类存在且 savePath 正确；实际新建时返回 True。

        Args:
            name: 分类名。
            save_path: 期望的保存路径；空串表示不校验路径。
        """
        categories = self._call(self._client.torrents_categories)
        if name in categories:
            current = str((categories[name] or {}).get("savePath") or "")
            if save_path and current != save_path:
                self._call(self._client.torrents_edit_category, name=name, save_path=save_path)
            return False
        self._call(self._client.torrents_create_category, name=name, save_path=save_path or None)
        return True

    def add_torrents(
        self,
        urls: list[str],
        save_path: str = "",
        category: str = "",
        tags: list[str] | None = None,
        paused: bool = False,
    ) -> None:
        """逐个添加种子链接，可指定保存路径、分类、标签与暂停状态。

        Args:
            urls: 种子链接（URL 或磁力）列表。
            save_path: 保存路径；空串表示用默认值。
            category: 分类名；空串表示不指定。
            tags: 附加标签列表；None 表示不附加。
            paused: 是否以暂停状态添加。
        """
        for url in urls:
            self._call(
                self._client.torrents_add,
                urls=url,
                save_path=save_path or None,
                category=category or None,
                tags=",".join(tags) if tags else None,
                is_paused=paused,
            )

    def torrents_by_tag(self, tag: str) -> list[Any]:
        """按标签查询种子信息列表。

        Args:
            tag: 标签名。
        """
        return self._call(self._client.torrents_info, tag=tag)

    def delete_torrents(self, hashes: list[str], delete_files: bool) -> None:
        """删除指定哈希的种子，可选择同时删除文件。

        Args:
            hashes: 种子哈希列表；为空时直接返回。
            delete_files: 是否同时删除已下载文件。
        """
        if not hashes:
            return
        self._call(self._client.torrents_delete, delete_files=delete_files, torrent_hashes=hashes)

    def set_preferences(self, values: dict[str, Any]) -> None:
        """批量修改 qB 全局偏好。

        Args:
            values: 偏好名到新值的映射。
        """
        self._call(self._client.app_set_preferences, prefs=values)

    def repack_proper_enabled(self) -> bool:
        """是否开启了重发 / 修正版（repack/proper）自动下载。"""
        return bool(self._rss_settings().get("rss_download_repack_proper_episodes", False))

    def _call(self, func: Any, **kwargs: Any) -> Any:
        """调用 API 并把异常翻译为 QBError。

        Args:
            func: 待调用的底层 API 函数。
            **kwargs: 透传给该函数的参数。
        """
        try:
            return func(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc

    def _rss_settings(self) -> dict[str, Any]:
        """筛选 app_preferences 中的 rss_* 项。"""
        prefs = self._client.app_preferences()
        return {k: v for k, v in prefs.items() if k.startswith("rss_")}

    def _count_feeds(self) -> int:
        """统计 RSS 树中的 feed 数量。"""
        items = self._client.rss_items(with_data=False)
        return _count_feed_items(items)

    def _translate(self, exc: Exception) -> QBError:
        """把各类底层异常翻译成带中文提示的 QBError。

        Args:
            exc: 底层抛出的异常。
        """
        name = type(exc).__name__
        host = self._env.qb_host
        if isinstance(exc, qbittorrentapi.LoginFailed):
            return QBError(
                f"qBittorrent 登录失败（{host}）",
                hint="检查 .env 中的 QB_USERNAME / QB_PASSWORD",
            )

        # qB 的 HTTP 错误类名形如 Conflict409Error / Forbidden403Error，
        # 并不以 HTTP 开头，所以按响应状态码判定最可靠。
        code = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(code, int):
            return self._http_error(exc, code)

        if (
            isinstance(exc, qbittorrentapi.APIConnectionError)
            or "Connection" in name
            or "Timeout" in name
        ):
            return QBError(
                f"无法连接 qBittorrent（{host}）",
                hint=(
                    "确认服务在运行、端口可达、地址含 scheme（http://）；"
                    "若是瞬时失败，重跑一次通常即可"
                ),
            )
        return QBError(f"qBittorrent API 调用失败：{name}: {exc}")

    @staticmethod
    def _http_error(exc: Exception, code: int) -> QBError:
        """按 HTTP 状态码生成对应的 QBError。

        Args:
            exc: 底层异常，用于提取响应文本。
            code: HTTP 状态码。
        """
        text = _response_text(exc)
        if code in (401, 403):
            return QBError(
                f"qBittorrent 拒绝访问（{code}）",
                hint=(
                    "可能原因：WebUI 未开启或凭据未提供；"
                    "启用了 Host header 校验而访问域名与配置不符。"
                    "若使用反向代理，请在 WebUI 中把该域名加入白名单，或关闭 Host header 校验。"
                ),
                status=code,
            )
        if code == 404:
            return QBError(f"qBittorrent 资源不存在（404）：{text}", status=code)
        if code == 409:
            return QBError(
                f"qBittorrent 冲突（409）：{text}",
                hint="通常是同名 RSS 目录/订阅或同名规则已存在",
                status=code,
            )
        if code in (400, 415):
            return QBError(f"qBittorrent 参数错误（{code}）：{text}", status=code)
        return QBError(f"qBittorrent 返回 {code}：{text}", status=code)


def _response_text(exc: Exception) -> str:
    """取异常的可读文本，为空时回退为异常类名。

    Args:
        exc: 待取文本的异常。
    """
    text = str(exc).strip()
    return text or type(exc).__name__


def _count_feed_items(node: Any) -> int:
    """递归统计节点下的 feed 条目数。

    Args:
        node: RSS 树节点；非字典按 0 计。
    """
    if not isinstance(node, dict):
        return 0
    total = 0
    for key, value in node.items():
        if key == "children":
            continue
        if isinstance(value, dict) and value.get("url"):
            total += 1
        if isinstance(value, dict) and isinstance(value.get("children"), dict):
            total += _count_feed_items(value["children"])
    return total


def walk_feeds(node: Any, prefix: str = "") -> list[dict[str, str]]:
    """把 RSS 树拍平成含 path/url/type/refresh_interval 的行列表。

    Args:
        node: RSS 树节点；非字典返回空列表。
        prefix: 当前节点已累积的路径前缀。
    """
    rows: list[dict[str, str]] = []
    if not isinstance(node, dict):
        return rows
    for key, value in node.items():
        if key == "children" or not isinstance(value, dict):
            continue
        path = f"{prefix}/{key}" if prefix else key
        url = value.get("url") or ""
        rows.append(
            {
                "path": path,
                "url": url,
                "type": "feed" if url else "dir",
                "refresh_interval": str(value.get("refreshInterval", "")),
            }
        )
        rows.extend(walk_feeds(value.get("children"), path))
    return rows
