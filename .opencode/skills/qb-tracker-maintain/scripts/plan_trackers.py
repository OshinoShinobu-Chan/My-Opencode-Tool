"""根据探测结果挑选推荐 tracker：榜单排名优先，其次实测延迟，同域限量。"""

from __future__ import annotations

import argparse
import collections
import csv
from pathlib import Path
from urllib.parse import urlsplit


def base_domain(host: str) -> str:
    """取后两段作为同源判断（example.com）。"""
    return ".".join((host or "").split(".")[-2:])


def main() -> int:
    """读探测 CSV，按榜单排名 + 可达性输出推荐清单。"""
    ap = argparse.ArgumentParser(description="从探测结果挑选推荐 tracker")
    ap.add_argument("--probe", required=True, help="qb_probe.py 输出的 CSV")
    ap.add_argument("--target", type=int, default=14, help="目标条数")
    ap.add_argument("--per-domain", type=int, default=2, help="同一主域最多保留几条")
    ap.add_argument("--max-ms", type=int, default=1500, help="延迟上限（毫秒）")
    ap.add_argument("--extra", default="", help="额外优先保留的清单（如中文圈常用），须实测可达")
    ap.add_argument("--out", required=True, help="输出清单文件（一行一条）")
    args = ap.parse_args()

    rows = []
    with Path(args.probe).open(encoding="utf-8-sig") as fh:
        probe_rows = list(csv.DictReader(fh))
    for r in probe_rows:
        if r["ok"] != "True" or r["url"].startswith("wss://"):
            continue
        ms = int(r["ms"] or 0)
        if ms > args.max_ms:
            continue
        rows.append(
            {
                "url": r["url"].strip(),
                "ms": ms,
                "rank": int(r.get("rank") or 9999),
                "both": r["src"] == "ngosang,xiu2",
            }
        )

    if not rows:
        raise SystemExit("没有可用条目，可能是探测全部失败或 --max-ms 太小")

    by_url = {r["url"]: r for r in rows}

    picked: list[dict] = []
    seen: set[str] = set()

    if args.extra:
        for line in Path(args.extra).read_text(encoding="utf-8", errors="ignore").splitlines():
            url = line.strip()
            if not url or url.startswith("#") or url not in by_url or url in seen:
                continue
            picked.append(by_url[url])
            seen.add(url)

    # 排序：双榜共推 > 榜单排名（越靠前越主流）> 实测延迟
    rows.sort(key=lambda r: (not r["both"], r["rank"], r["ms"]))

    per_domain: collections.Counter[str] = collections.Counter(
        base_domain((urlsplit(r["url"]).hostname or "").lower()) for r in picked
    )
    for r in rows:
        if len(picked) >= args.target:
            break
        if r["url"] in seen:
            continue
        host = (urlsplit(r["url"]).hostname or "").lower()
        if per_domain[base_domain(host)] >= args.per_domain:
            continue
        picked.append(r)
        seen.add(r["url"])
        per_domain[base_domain(host)] += 1

    Path(args.out).write_text("\n".join(r["url"] for r in picked) + "\n", encoding="utf-8")

    print(
        f"达标 {len(rows)} 条，选中 {len(picked)} 条"
        f"（双榜共推 {sum(1 for r in picked if r['both'])} 条）\n"
    )
    print(f"{'排名':>5} {'延迟':>7}  {'双榜':<4} 地址")
    for r in picked:
        print(f"{r['rank']:5} {r['ms']:6}ms  {'✔' if r['both'] else ' ':<4} {r['url']}")
    print(f"\n清单写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
