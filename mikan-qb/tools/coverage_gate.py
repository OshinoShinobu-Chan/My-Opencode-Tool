"""从 coverage.json 聚合分组覆盖率并强制阈值（替代多次 coverage hook）。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_JSON = Path("coverage.json")


def _ratio(files: dict, needle: str) -> tuple[int, int]:
    """统计路径含 needle 的文件命中与语句总数。

    Args:
        files: coverage.json 的 files 映射。
        needle: 归一化后的路径片段（如 "/mikan_health/core/"）。
    """
    covered = 0
    total = 0
    for name, info in files.items():
        normalized = "/" + str(name).replace("\\", "/")
        if needle not in normalized:
            continue
        summary = info.get("summary") or {}
        total += int(summary.get("num_statements", 0))
        covered += int(summary.get("covered_lines", 0))
    return covered, total


def _percent(covered: int, total: int) -> float:
    """覆盖率百分比；无语句时视为 100。"""
    return 100.0 if total == 0 else covered * 100.0 / total


def main(argv: list[str] | None = None) -> int:
    """读取覆盖率报告并按分组阈值返回退出码。

    Args:
        argv: 命令行参数；None 时取 sys.argv。
    """
    parser = argparse.ArgumentParser(description="分组覆盖率阈值校验")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON, help="coverage json 路径")
    parser.add_argument("--health", type=float, default=75.0, help="mikan-health 阈值")
    parser.add_argument("--core", type=float, default=90.0, help="mikan-health.core 阈值")
    parser.add_argument("--qb", type=float, default=75.0, help="mikan-qb 阈值")
    args = parser.parse_args(argv)

    if not args.json.is_file():
        print(f"找不到覆盖率报告 {args.json}；请先运行 pytest --cov-report=json:{args.json}")
        return 1

    files = json.loads(args.json.read_text(encoding="utf-8")).get("files", {})
    checks = [
        ("mikan-health", "/mikan_health/", args.health),
        ("mikan-health.core", "/mikan_health/core/", args.core),
        ("mikan-qb", "/mikan_qb/", args.qb),
    ]
    failed = False
    for label, needle, threshold in checks:
        covered, total = _ratio(files, needle)
        pct = _percent(covered, total)
        ok = pct >= threshold
        failed = failed or not ok
        print(f"{label:<18} {pct:6.2f}%  (>= {threshold:.0f}%)  {'OK' if ok else 'FAIL'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
