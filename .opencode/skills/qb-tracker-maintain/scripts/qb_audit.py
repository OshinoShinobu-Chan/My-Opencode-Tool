"""qBittorrent tracker 体检：统计条目数、分布、状态与全局设置（只读）。"""

from __future__ import annotations

import argparse
import collections
import csv
import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_ENV = Path(r"C:\data\My-Opencode-Tool\mikan-qb\.env")
STATUS_CN = {0: "禁用", 1: "未联系", 2: "工作中", 3: "更新中", 4: "失效"}


def load_env(path: Path) -> dict[str, str]:
    """解析 .env 为字典，跳过注释与空行。"""
    if not path.is_file():
        raise SystemExit(f"找不到 {path}（需要 QB_HOST / QB_USERNAME / QB_PASSWORD）")
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


class QB:
    """极简 qB WebAPI 客户端（标准库实现）。"""

    def __init__(self, env: dict[str, str]) -> None:
        self.host = env["QB_HOST"].rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            urllib.request.ProxyHandler({}),
        )
        body = urllib.parse.urlencode(
            {"username": env["QB_USERNAME"], "password": env["QB_PASSWORD"]}
        ).encode()
        with self.opener.open(
            urllib.request.Request(
                self.host + "/api/v2/auth/login",
                data=body,
                headers={"Referer": self.host},
            ),
            timeout=30,
        ) as resp:
            if resp.read().decode().strip() != "Ok.":
                raise SystemExit("qB 登录失败，检查 QB_USERNAME / QB_PASSWORD")

    def _open(self, req: urllib.request.Request, timeout: int):
        """带重试地发起请求：本机 DNS 偶发只返回 IPv6，会抛 gaierror。"""
        last: Exception | None = None
        for attempt in range(4):
            try:
                return self.opener.open(req, timeout=timeout)
            except (urllib.error.URLError, OSError) as exc:
                last = exc
                time.sleep(2 * (attempt + 1))
        raise last if last else RuntimeError("request failed")

    def get(self, path: str, **params):
        """GET 并返回 JSON。"""
        url = self.host + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={"Referer": self.host})
        with self._open(req, 90) as resp:
            return json.loads(resp.read().decode())


def norm(url: str) -> str:
    """归一化 tracker URL（保留 scheme）。"""
    parts = urlsplit(url)
    port = f":{parts.port}" if parts.port else ""
    return (
        f"{parts.scheme.lower()}://{(parts.hostname or '').lower()}{port}{parts.path.rstrip('/')}"
    )


def tracker_items(qb: QB, torrent_hash: str) -> list[dict]:
    """取一颗种子的真实 tracker 条目（过滤 DHT/PeX/LSD 伪条目）。"""
    try:
        items = qb.get("/api/v2/torrents/trackers", hash=torrent_hash)
    except Exception:  # noqa: BLE001 - 单颗失败不影响整体
        return []
    return [x for x in items if x.get("url", "").startswith(("http", "udp", "ws"))]


def main() -> int:
    """跑完整体检并输出摘要。"""
    ap = argparse.ArgumentParser(description="qBittorrent tracker 体检")
    ap.add_argument("--env", default=str(DEFAULT_ENV), help=".env 路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--csv", default="", help="把每 tracker 的明细写到该 CSV")
    ap.add_argument("--workers", type=int, default=8, help="并发数")
    args = ap.parse_args()

    qb = QB(load_env(Path(args.env)))
    prefs = qb.get("/api/v2/app/preferences")
    torrents = qb.get("/api/v2/torrents/info", filter="all")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        per_torrent = list(pool.map(lambda t: tracker_items(qb, t["hash"]), torrents))

    usage: collections.Counter[str] = collections.Counter()
    status: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    counts: list[int] = []
    for items in per_torrent:
        counts.append(len(items))
        for url in {norm(x["url"]) for x in items}:
            usage[url] += 1
        for x in items:
            status[norm(x["url"])][x.get("status")] += 1

    buckets = collections.Counter(
        "0"
        if n == 0
        else "1-50"
        if n <= 50
        else "51-200"
        if n <= 200
        else "201-500"
        if n <= 500
        else "500+"
        for n in counts
    )
    summary = {
        "torrents": len(torrents),
        "tracker_entries": sum(counts),
        "unique_trackers": len(usage),
        "per_torrent_avg": round(sum(counts) / max(1, len(counts)), 1),
        "per_torrent_max": max(counts) if counts else 0,
        "buckets": dict(buckets),
        "status": {
            STATUS_CN[k]: sum(v.get(k, 0) for v in status.values()) for k in (0, 1, 2, 3, 4)
        },
        "add_trackers_enabled": prefs.get("add_trackers_enabled"),
        "add_trackers_count": len(
            [x for x in (prefs.get("add_trackers") or "").splitlines() if x.strip()]
        ),
        "announce_to_all_tiers": prefs.get("announce_to_all_tiers"),
        "announce_to_all_trackers": prefs.get("announce_to_all_trackers"),
        "top": [{"url": u, "torrents": c} for u, c in usage.most_common(15)],
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"种子数            : {summary['torrents']}")
        print(
            f"tracker 条目      : {summary['tracker_entries']}（去重 {summary['unique_trackers']}）"
        )
        print(
            f"单种子            : 平均 {summary['per_torrent_avg']}，"
            f"最多 {summary['per_torrent_max']}"
        )
        print(f"数量分布          : {summary['buckets']}")
        print(f"状态              : {summary['status']}")
        print(
            "自动添加          : "
            f"enabled={summary['add_trackers_enabled']}, "
            f"{summary['add_trackers_count']} 条"
        )
        print(
            f"announce_all_tiers: {summary['announce_to_all_tiers']} / "
            f"all_trackers: {summary['announce_to_all_trackers']}"
        )
        print("\n使用最多的 tracker:")
        for row in summary["top"]:
            print(f"  {row['torrents']:4} 颗  {row['url'][:88]}")

    if args.csv:
        with Path(args.csv).open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["tracker", "种子数", "工作中", "未联系", "失效"])
            for url, cnt in usage.most_common():
                st = status[url]
                writer.writerow([url, cnt, st.get(2, 0), st.get(1, 0), st.get(4, 0)])
        print(f"\n明细: {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
