"""拉取 ngosang / XIU2 最新公共 tracker 榜单并合并去重（带榜单排名）。"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}

SOURCES = {
    "ngosang_best": "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt",
    "ngosang_all": "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_all.txt",
    "xiu2_best": "https://raw.githubusercontent.com/XIU2/TrackersListCollection/master/best.txt",
    "xiu2_all": "https://raw.githubusercontent.com/XIU2/TrackersListCollection/master/all.txt",
}

REPOS = ("ngosang/trackerslist", "XIU2/TrackersListCollection")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def fetch(url: str, use_proxy: bool) -> str:
    """下载文本；失败时按需走本机代理重试。"""
    handlers = (
        [urllib.request.ProxyHandler(PROXY)] if use_proxy else [urllib.request.ProxyHandler({})]
    )
    opener = urllib.request.build_opener(*handlers)
    with opener.open(
        urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=60
    ) as resp:
        return resp.read().decode("utf-8", "ignore")


def norm(url: str) -> str:
    """归一化 tracker URL 以便去重。"""
    parts = urlsplit(url)
    port = f":{parts.port}" if parts.port else ""
    return (
        f"{parts.scheme.lower()}://{(parts.hostname or '').lower()}{port}{parts.path.rstrip('/')}"
    )


def last_commit(repo: str) -> str:
    """读取仓库最后一次提交时间，判断榜单新鲜度。"""
    try:
        data = json.loads(fetch(f"https://api.github.com/repos/{repo}/commits?per_page=1", True))
        return data[0]["commit"]["committer"]["date"]
    except Exception:  # noqa: BLE001 - 拿不到时间不影响主流程
        return "unknown"


def main() -> int:
    """下载四份榜单，合并成 candidates.txt（url / 来源 / 最佳榜排名）。"""
    ap = argparse.ArgumentParser(description="拉取并合并最新公共 tracker 榜单")
    ap.add_argument("-o", "--out", required=True, help="输出目录")
    ap.add_argument("--proxy", action="store_true", help="强制走代理")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    seen: dict[str, set[str]] = {}
    rank: dict[str, int] = {}
    for name, url in SOURCES.items():
        try:
            text = fetch(url, use_proxy=args.proxy)
        except Exception:
            text = fetch(url, use_proxy=True)
        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip().startswith(("http://", "https://", "udp://", "ws://", "wss://"))
        ]
        (out_dir / f"{name}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{name:12} {len(lines):4} 条")
        for idx, line in enumerate(lines):
            key = norm(line)
            seen.setdefault(key, set()).add(name.split("_")[0])
            if name.endswith("_best"):
                rank[key] = min(rank.get(key, 10**6), idx + 1)

    both = sum(1 for src in seen.values() if len(src) == 2)
    out = out_dir / "candidates.txt"
    out.write_text(
        "\n".join(
            f"{url}\t{','.join(sorted(src))}\t{rank.get(url, 9999)}"
            for url, src in sorted(seen.items())
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\n合并去重: {len(seen)} 条（双榜共推 {both} 条）-> {out}")
    for repo in REPOS:
        print(f"  {repo} 最后更新: {last_commit(repo)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
