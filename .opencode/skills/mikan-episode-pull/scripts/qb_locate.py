"""定位远端 qBittorrent 中已订阅的种子，并换算成本机可 SSH 访问的 NAS 路径。"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_ENV = Path(r"C:\data\My-Opencode-Tool\mikan-qb\.env")
CONTAINER_PREFIX = "/downloads"
HOST_PREFIX = "/mnt/nas/qbittorrent"


def load_env(path: Path) -> dict[str, str]:
    """读取 .env 的键值对，忽略注释与空行。"""
    if not path.is_file():
        raise SystemExit(f"找不到 {path}（需要 QB_HOST / QB_USERNAME / QB_PASSWORD）")
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def login(host: str, username: str, password: str) -> urllib.request.OpenerDirector:
    """登录 WebUI，返回带 cookie 的 opener。"""
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        urllib.request.ProxyHandler({}),
    )
    body = urllib.parse.urlencode({"username": username, "password": password}).encode()
    resp = opener.open(
        urllib.request.Request(
            host + "/api/v2/auth/login",
            data=body,
            headers={"Referer": host, "User-Agent": "mikan-episode-pull/1.0"},
        ),
        timeout=30,
    )
    if resp.read().decode().strip() != "Ok.":
        raise SystemExit("qB 登录失败：检查 .env 里的 QB_USERNAME / QB_PASSWORD")
    return opener


def to_host_path(container_path: str) -> str:
    """把容器内 /downloads/... 换成宿主机 /mnt/nas/qbittorrent/...。"""
    if container_path.startswith(CONTAINER_PREFIX):
        return HOST_PREFIX + container_path[len(CONTAINER_PREFIX) :]
    return container_path


def main() -> int:
    """按关键词匹配种子并打印进度、容器路径与宿主路径。"""
    ap = argparse.ArgumentParser(description="定位远端 qB 中已订阅的种子")
    ap.add_argument("keyword", help="文件名关键词，不区分大小写，如 Tetsuryou / 铁旅")
    ap.add_argument("--env", default=str(DEFAULT_ENV), help=".env 路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    env = load_env(Path(os.path.expandvars(args.env)))
    host = env.get("QB_HOST", "").rstrip("/")
    if not host:
        raise SystemExit(".env 里缺少 QB_HOST")

    opener = login(host, env.get("QB_USERNAME", ""), env.get("QB_PASSWORD", ""))
    with opener.open(host + "/api/v2/torrents/info?filter=all", timeout=60) as resp:
        torrents = json.load(resp)

    needle = args.keyword.lower()
    hits = [
        t
        for t in torrents
        if needle in t["name"].lower() or needle in (t.get("content_path") or "").lower()
    ]
    hits.sort(key=lambda t: t["name"])

    rows = [
        {
            "name": t["name"],
            "progress": round(t["progress"] * 100, 2),
            "state": t["state"],
            "size_mib": round(t["size"] / 1048576, 1),
            "container_path": t.get("content_path") or t["save_path"],
            "host_path": to_host_path(t.get("content_path") or t["save_path"]),
        }
        for t in hits
    ]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0 if rows else 1

    if not rows:
        print(f"没有种子匹配 {args.keyword!r}（远端共 {len(torrents)} 个种子）")
        return 1

    print(f"匹配 {len(rows)} 个种子 / 远端共 {len(torrents)} 个")
    for r in rows:
        ready = "已下完" if r["progress"] >= 100 else f"仅 {r['progress']}%"
        print(f"\n{r['name']}")
        print(f"  状态: {r['state']}  {ready}  {r['size_mib']} MiB")
        print(f"  容器: {r['container_path']}")
        print(f"  宿主: {r['host_path']}")
    incomplete = [r for r in rows if r["progress"] < 100]
    if incomplete:
        print(f"\n注意: {len(incomplete)} 个种子未下完，不要复制半成品。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
