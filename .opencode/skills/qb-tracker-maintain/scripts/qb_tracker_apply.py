"""按方案调整 qBittorrent tracker：裁剪白名单、删除指定死链、写入自动添加列表。"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_ENV = Path(r"C:\data\My-Opencode-Tool\mikan-qb\.env")


def load_env(path: Path) -> dict[str, str]:
    """解析 .env 为字典。"""
    if not path.is_file():
        raise SystemExit(f"找不到 {path}")
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


class QB:
    """极简 qB WebAPI 客户端。"""

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
                self.host + "/api/v2/auth/login", data=body, headers={"Referer": self.host}
            ),
            timeout=30,
        ) as resp:
            if resp.read().decode().strip() != "Ok.":
                raise SystemExit("qB 登录失败")

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
        """GET JSON。"""
        url = self.host + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={"Referer": self.host})
        with self._open(req, 90) as resp:
            return json.loads(resp.read().decode())

    def post(self, path: str, data: dict, timeout: int = 180) -> tuple[int, str]:
        """POST 表单，返回 (状态码, 文本)。"""
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(
            self.host + path,
            data=body,
            headers={"Referer": self.host, "Content-Type": "application/x-www-form-urlencoded"},
        )
        with self._open(req, timeout) as resp:
            return resp.status, resp.read().decode(errors="ignore")


def norm(url: str) -> str:
    """归一化 tracker URL。"""
    parts = urlsplit(url)
    port = f":{parts.port}" if parts.port else ""
    return (
        f"{parts.scheme.lower()}://{(parts.hostname or '').lower()}{port}{parts.path.rstrip('/')}"
    )


def read_list(path: str) -> list[str]:
    """读取一行一条的清单文件。"""
    lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    return [x.strip() for x in lines if x.strip() and not x.strip().startswith("#")]


def main() -> int:
    """按参数执行 dry-run 或真实调整。"""
    ap = argparse.ArgumentParser(description="调整 qB tracker（默认 dry-run）")
    ap.add_argument("--env", default=str(DEFAULT_ENV))
    ap.add_argument(
        "--whitelist", default="", help="保留清单文件（配合 --trim 或 --set-add-trackers）"
    )
    ap.add_argument("--trim", action="store_true", help="每种子只保留与白名单的交集")
    ap.add_argument(
        "--set-add-trackers", metavar="FILE", default="", help="用该文件写 add_trackers 并启用"
    )
    ap.add_argument("--announce-to-all-tiers", choices=["true", "false"], default="")
    ap.add_argument("--dead-hosts", default="", help="按 hostname 删除，逗号分隔")
    ap.add_argument("--apply", action="store_true", help="真正执行（默认只打印）")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--backup-dir", default=str(Path.home() / "AppData/Local/Temp/opencode"))
    args = ap.parse_args()

    if not (args.trim or args.set_add_trackers or args.dead_hosts or args.announce_to_all_tiers):
        raise SystemExit("没有指定任何操作，至少给 --trim / --set-add-trackers / --dead-hosts")

    qb = QB(load_env(Path(args.env)))
    whitelist = read_list(args.whitelist) if args.whitelist else []
    want = {norm(u) for u in whitelist}
    dead_hosts = {h.strip().lower() for h in args.dead_hosts.split(",") if h.strip()}

    prefs = qb.get("/api/v2/app/preferences")
    torrents = qb.get("/api/v2/torrents/info", filter="all")

    def items_of(t: dict) -> list[dict]:
        """单颗种子的真实 tracker 条目。"""
        try:
            return [
                x
                for x in qb.get("/api/v2/torrents/trackers", hash=t["hash"])
                if x.get("url", "").startswith(("http", "udp", "ws"))
            ]
        except Exception:  # noqa: BLE001
            return []

    with ThreadPoolExecutor(max_workers=8) as pool:
        per_torrent = list(pool.map(items_of, torrents))

    plan: list[tuple[dict, list[str]]] = []
    before = after = 0
    for t, items in zip(torrents, per_torrent, strict=False):
        if not items:
            continue
        urls = list(dict.fromkeys(norm(x["url"]) for x in items))
        drop: list[str] = []
        keep = urls
        if whitelist:
            kept = [u for u in urls if u in want] or urls[:5]
            drop += [x["url"] for x in items if norm(x["url"]) not in set(kept)]
            keep = kept
        if dead_hosts:
            keep = [u for u in keep if (urlsplit(u).hostname or "").lower() not in dead_hosts]
            drop += [
                x["url"] for x in items if (urlsplit(x["url"]).hostname or "").lower() in dead_hosts
            ]
        drop = list(dict.fromkeys(drop))
        if not drop:
            continue
        before += len(urls)
        after += len(keep)
        plan.append((t, drop))

    total_drop = sum(len(d) for _, d in plan)
    print(f"涉及种子: {len(plan)} 颗，待删条目: {total_drop}")
    if whitelist:
        print(f"白名单: {len(whitelist)} 条（裁剪后平均 {after / max(1, len(plan)):.1f} 条/种子）")
    if dead_hosts:
        print(f"死链 host: {', '.join(sorted(dead_hosts))}")
    if args.set_add_trackers:
        print(f"add_trackers: {len(read_list(args.set_add_trackers))} 条，将启用")
    if args.announce_to_all_tiers:
        print(f"announce_to_all_tiers -> {args.announce_to_all_tiers}")

    if not args.apply:
        print("\n[dry-run] 未做任何修改；确认后加 --apply")
        return 0

    backup_dir = Path(args.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"qb_tracker_backup_{datetime.now():%Y%m%d_%H%M%S}.json"
    backup.write_text(
        json.dumps(
            {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "host": qb.host,
                "prefs": {
                    "add_trackers": prefs.get("add_trackers"),
                    "add_trackers_enabled": prefs.get("add_trackers_enabled"),
                    "announce_to_all_tiers": prefs.get("announce_to_all_tiers"),
                },
                "torrents": [
                    {"hash": t["hash"], "name": t["name"], "trackers": [x["url"] for x in items]}
                    for t, items in zip(torrents, per_torrent, strict=False)
                    if items
                ],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\n备份: {backup}")

    settings: dict[str, object] = {}
    if args.set_add_trackers:
        settings["add_trackers"] = "\n".join(read_list(args.set_add_trackers))
        settings["add_trackers_enabled"] = True
    if args.announce_to_all_tiers:
        settings["announce_to_all_tiers"] = args.announce_to_all_tiers == "true"
    if settings:
        code, _ = qb.post("/api/v2/app/setPreferences", {"json": json.dumps(settings)})
        print(f"设置已写入: HTTP {code}（{', '.join(settings)}）")

    def remove(job: tuple[dict, list[str]]) -> tuple[dict, int]:
        """删除单颗种子的 tracker。"""
        t, drop = job
        try:
            code, _ = qb.post(
                "/api/v2/torrents/removeTrackers", {"hash": t["hash"], "urls": "|".join(drop)}
            )
            return t, code
        except Exception:  # noqa: BLE001
            return t, 0

    start = time.time()
    failed: list[tuple[dict, int]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, (t, code) in enumerate(pool.map(remove, plan), 1):
            if code != 200:
                failed.append((t, code))
            if i % 100 == 0:
                print(f"  进度 {i}/{len(plan)}")
    print(
        f"完成 {len(plan) - len(failed)} 颗，失败 {len(failed)} 颗，耗时 {time.time() - start:.1f}s"
    )
    for t, code in failed[:5]:
        print(f"   FAIL {code} {t['name'][:50]}")

    time.sleep(3)
    after_prefs = qb.get("/api/v2/app/preferences")
    remaining = 0
    if dead_hosts:
        with ThreadPoolExecutor(max_workers=8) as pool:
            counts = list(pool.map(items_of, qb.get("/api/v2/torrents/info", filter="all")))
        remaining = sum(
            1
            for items in counts
            if any((urlsplit(x["url"]).hostname or "").lower() in dead_hosts for x in items)
        )
    tracker_total = sum(
        1 for x in (after_prefs.get("add_trackers") or "").splitlines() if x.strip()
    )
    print(
        f"复核: add_trackers_enabled={after_prefs.get('add_trackers_enabled')}, "
        f"条数={tracker_total}, "
        f"残留死链种子={remaining}"
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
