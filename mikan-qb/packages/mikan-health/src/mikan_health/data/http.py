"""通用 HTTP 客户端骨架：缓存、限速、重试与错误封装。"""

from __future__ import annotations

import time
from typing import Any, Self

import httpx

from mikan_health.data.cache import HttpCache


class HttpError(Exception):
    """HTTP 客户端的公共异常基类。"""

    def __init__(self, message: str, url: str | None = None, status: int | None = None) -> None:
        """构造异常并附带请求上下文。

        Args:
            message: 错误描述。
            url: 相关请求 URL；未知为 None。
            status: HTTP 状态码；非状态错误为 None。
        """
        super().__init__(message)
        self.url = url
        self.status = status


class BaseHttpClient:
    """带缓存、限速、重试的 HTTP 客户端骨架。

    子类负责服务名、请求头、代理、响应/缓存编解码，以及非 200 的错误消息。
    """

    service_name = "HTTP"
    error_cls: type[HttpError] = HttpError

    def __init__(
        self,
        *,
        base_url: str,
        cache: HttpCache | None,
        ttl_hours: float,
        rate_limit_per_sec: float,
        timeout: float,
        max_retries: int,
        user_agent: str,
    ) -> None:
        """初始化客户端参数（不发起请求）。

        Args:
            base_url: 服务根地址。
            cache: HTTP 缓存；None 表示不缓存。
            ttl_hours: 缓存有效期（小时）。
            rate_limit_per_sec: 每秒请求上限；<=0 表示不限速。
            timeout: 单次请求超时（秒）。
            max_retries: 最大重试次数。
            user_agent: 请求 User-Agent。
        """
        self.base_url = base_url.rstrip("/")
        self.cache = cache
        self.ttl_hours = ttl_hours
        self.interval = 1.0 / rate_limit_per_sec if rate_limit_per_sec > 0 else 0.0
        self.timeout = timeout
        self.max_retries = max_retries
        self.user_agent = user_agent
        self._client: httpx.Client | None = None
        self._last_request = 0.0

    # -- 生命周期 -------------------------------------------------------
    def close(self) -> None:
        """关闭底层 httpx 连接。"""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- 子类覆盖点 -----------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent}

    def _proxy(self) -> str | None:
        return None

    def _status_error(self, response: httpx.Response, url: str) -> HttpError:
        return self.error_cls(
            f"{self.service_name} 返回 {response.status_code}",
            url=url,
            status=response.status_code,
        )

    def _request_failed_error(self, last_error: Exception | None, url: str) -> HttpError:
        return self.error_cls(f"请求 {self.service_name} 失败：{last_error}", url=url)

    def _decode_response(self, response: httpx.Response) -> Any:
        raise NotImplementedError

    def _decode_cache(self, body: bytes) -> Any:
        raise NotImplementedError

    def _encode_cache(self, value: Any) -> bytes:
        raise NotImplementedError

    # -- 内部机制 -------------------------------------------------------
    def _get(self, path: str, params: dict | None = None, force: bool = False) -> Any:
        """GET 并优先读缓存；force=True 时跳过缓存。

        Args:
            path: 相对 base_url 的路径。
            params: 查询参数；None 表示无。
            force: 为真时跳过缓存强制拉取。
        """
        url = self._build_url(path, params)
        if self.cache is not None and not force:
            cached = self.cache.get(url, self.ttl_hours)
            if cached is not None:
                return self._decode_cache(cached)

        value = self._decode_response(self._request(url))
        if self.cache is not None:
            self.cache.put(url, self._encode_cache(value))
        return value

    def _build_url(self, path: str, params: dict | None) -> str:
        """拼接 base_url、path 与查询参数。

        Args:
            path: 相对 base_url 的路径。
            params: 查询参数；None/空表示无。
        """
        if not params:
            return f"{self.base_url}{path}"
        query = "&".join(f"{key}={_quote(value)}" for key, value in params.items())
        return f"{self.base_url}{path}?{query}"

    def _request(self, url: str) -> httpx.Response:
        """执行带限速与重试的请求；5xx/网络错误重试，其余 4xx 直接抛错。

        Args:
            url: 完整请求 URL。
        """
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                response = self._http().get(url)
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(min(2**attempt, 8))
                continue
            self._last_request = time.monotonic()
            if response.status_code == 200:
                return response
            error = self._status_error(response, url)
            if response.status_code >= 500:
                last_error = error
                time.sleep(min(2**attempt, 8))
                continue
            raise error
        raise self._request_failed_error(last_error, url)

    def _http(self) -> httpx.Client:
        """惰性创建并复用 httpx.Client。"""
        if self._client is None:
            self._client = httpx.Client(
                headers=self._headers(),
                timeout=self.timeout,
                follow_redirects=True,
                proxy=self._proxy(),
            )
        return self._client

    def _throttle(self) -> None:
        """按限速间隔休眠，保证请求频率。"""
        if self.interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)


def _quote(value: object) -> str:
    from urllib.parse import quote

    return quote(str(value), safe="")
