"""在服务器上实测 tracker 可用性：UDP 用 BEP15 connect 握手，HTTP(S) 用 TCP 连接。"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import socket
import struct
import sys
import time
from urllib.parse import urlsplit

MAGIC = 0x41727101980


def probe_udp(host: str, port: int, timeout: float) -> tuple[bool, float, str]:
    """发送 BEP15 connect 请求；收到 action=0 的响应即视为可用。"""
    start = time.time()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        txid = int(time.time()) & 0xFFFF
        sock.sendto(struct.pack("!QII", MAGIC, 0, txid), (host, port))
        data, _ = sock.recvfrom(2048)
        action, rid = struct.unpack("!II", data[:8])
        if action == 0 and rid == txid:
            return True, (time.time() - start) * 1000, ""
        return False, 0.0, f"action={action}"
    except TimeoutError:
        return False, 0.0, "timeout"
    except Exception as exc:  # noqa: BLE001
        return False, 0.0, str(exc)[:50]
    finally:
        sock.close()


def probe_tcp(host: str, port: int, timeout: float) -> tuple[bool, float, str]:
    """TCP 连接测试（http/https tracker）。"""
    start = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, (time.time() - start) * 1000, ""
    except Exception as exc:  # noqa: BLE001
        return False, 0.0, str(exc)[:50]


def check(item: str, timeout: float, retries: int) -> dict:
    """测试一条候选（url<TAB>来源<TAB>榜单排名），失败按 retries 重试。"""
    parts_line = item.lstrip("\ufeff").split("\t")
    url = parts_line[0].strip()
    src = parts_line[1].strip() if len(parts_line) > 1 else ""
    rank = int(parts_line[2]) if len(parts_line) > 2 and parts_line[2].isdigit() else 9999
    base = {"url": url, "src": src, "rank": rank, "dns": False, "ok": False, "ms": 0, "err": ""}

    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return {**base, "err": "bad-url"}

    try:
        socket.getaddrinfo(host, None)
    except Exception:  # noqa: BLE001
        return {**base, "err": "dns"}

    err = ""
    for _ in range(retries):
        if parts.scheme == "udp":
            ok, ms, err = probe_udp(host, port, timeout)
        else:
            ok, ms, err = probe_tcp(host, port, timeout)
        if ok:
            return {**base, "dns": True, "ok": True, "ms": round(ms)}
        time.sleep(2)
    return {**base, "dns": True, "err": err}


def main() -> int:
    """读取候选清单，并发探测，写出 CSV 并打印汇总。"""
    ap = argparse.ArgumentParser(description="实测 tracker 可用性（在 announce 所在机器上运行）")
    ap.add_argument("candidates", help="candidates.txt（url<TAB>来源<TAB>排名）")
    ap.add_argument("out", help="输出 CSV 路径")
    ap.add_argument("--timeout", type=float, default=4.0, help="单次超时秒数")
    ap.add_argument("--retries", type=int, default=3, help="失败重试次数")
    ap.add_argument("--workers", type=int, default=24, help="并发数")
    args = ap.parse_args()

    with open(args.candidates, encoding="utf-8-sig") as fh:
        items = [line.rstrip("\n") for line in fh if line.strip()]

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(check, item, args.timeout, args.retries) for item in items]
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda r: (not r["ok"], r["err"], r["ms"]))
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["url", "src", "rank", "dns", "ok", "ms", "err"])
        writer.writeheader()
        writer.writerows(results)

    ok = [r for r in results if r["ok"]]
    print(f"总计 {len(results)}，可用 {len(ok)}，不可用 {len(results) - len(ok)}")
    for r in ok:
        print(f"OK   {r['ms']:6}ms  {r['url']}")
    for r in results:
        if not r["ok"]:
            print(f"FAIL {r['err']:12} {r['url']}")
    print(f"\n结果: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
