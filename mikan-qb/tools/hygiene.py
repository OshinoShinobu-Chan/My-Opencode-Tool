"""离线基础卫生检查（供 pre-commit 的 local hook 使用）。

等价于 pre-commit-hooks 中常用几项的本地实现，避免依赖 GitHub 拉取：
    trailing-whitespace / end-of-file / mixed-line-ending /
    check-toml / check-merge-conflict / check-added-large-files

`all` 子命令一次跑完全部检查，减少 pre-commit 反复拉起进程的开销。
未显式传入文件时，默认扫描 `git ls-files` 的全部被跟踪文件。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path

TEXT_SUFFIXES = {
    ".py",
    ".toml",
    ".md",
    ".txt",
    ".cfg",
    ".ini",
    ".json",
    ".yaml",
    ".yml",
    ".html",
    ".xml",
    ".example",
    ".gitignore",
    ".gitattributes",
}
DEFAULT_MAXKB = 500


def _tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True)
    return [Path(p) for p in out.stdout.decode("utf-8").split("\0") if p]


def _is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in (".gitignore", ".gitattributes")


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def _check_trailing(files: list[Path]) -> list[str]:
    problems: list[str] = []
    for path in files:
        if not _is_text(path) or not path.is_file():
            continue
        out: list[str] = []
        changed = False
        for line in _read(path).splitlines(keepends=True):
            body = line.rstrip("\r\n")
            ending = line[len(body) :]
            new = body.rstrip(" \t") + ending
            changed = changed or new != line
            out.append(new)
        if changed:
            path.write_text("".join(out), encoding="utf-8", newline="")
            problems.append(str(path))
    return problems


def _check_eof(files: list[Path]) -> list[str]:
    problems: list[str] = []
    for path in files:
        if not _is_text(path) or not path.is_file():
            continue
        text = _read(path)
        if text and not text.endswith("\n"):
            path.write_text(text + "\n", encoding="utf-8", newline="")
            problems.append(str(path))
    return problems


def _check_mixed(files: list[Path], fix: str | None) -> list[str]:
    problems: list[str] = []
    for path in files:
        if not _is_text(path) or not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\r" not in raw:
            continue
        if fix == "lf":
            text = raw.decode("utf-8", errors="replace")
            path.write_text(
                text.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8", newline=""
            )
        problems.append(str(path))
    return problems


def _check_toml(files: list[Path]) -> list[str]:
    problems: list[str] = []
    for path in files:
        if path.suffix.lower() != ".toml" or not path.is_file():
            continue
        try:
            tomllib.loads(_read(path))
        except tomllib.TOMLDecodeError as exc:
            problems.append(f"{path}: {exc}")
    return problems


def _check_conflict(files: list[Path]) -> list[str]:
    problems: list[str] = []
    for path in files:
        if not _is_text(path) or not path.is_file():
            continue
        for i, line in enumerate(_read(path).splitlines(), 1):
            if line.startswith(("<<<<<<<", ">>>>>>>", "=======")) and len(line.strip()) >= 7:
                problems.append(f"{path}:{i}")
    return problems


def _check_large(files: list[Path], maxkb: int) -> list[str]:
    problems: list[str] = []
    for path in files:
        if path.is_file() and path.stat().st_size > maxkb * 1024:
            problems.append(f"{path} ({path.stat().st_size / 1024:.1f} KB)")
    return problems


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="离线基础卫生检查")
    sub = parser.add_subparsers(dest="check", required=True)

    def add(name: str) -> argparse.ArgumentParser:
        child = sub.add_parser(name)
        child.add_argument("paths", nargs="*")
        return child

    all_check = add("all")
    all_check.add_argument("--maxkb", type=int, default=DEFAULT_MAXKB)
    add("trailing-whitespace")
    add("end-of-file")
    mixed = add("mixed-line-ending")
    mixed.add_argument("--fix", choices=["lf"], default=None)
    add("check-toml")
    add("check-merge-conflict")
    large = add("check-added-large-files")
    large.add_argument("--maxkb", type=int, default=DEFAULT_MAXKB)
    return parser


def _run_all(files: list[Path], maxkb: int) -> int:
    """依次执行全部卫生检查并汇总输出；任一项有问题返回 1。

    Args:
        files: 待检查文件列表。
        maxkb: 大文件阈值（KB）。
    """
    checks = [
        ("trailing-whitespace", lambda fs: _check_trailing(fs)),
        ("end-of-file", lambda fs: _check_eof(fs)),
        ("mixed-line-ending", lambda fs: _check_mixed(fs, "lf")),
        ("check-toml", lambda fs: _check_toml(fs)),
        ("check-merge-conflict", lambda fs: _check_conflict(fs)),
        ("check-added-large-files", lambda fs: _check_large(fs, maxkb)),
    ]
    failed = False
    for name, func in checks:
        problems = func(files)
        if problems:
            failed = True
            print(f"[{name}]")
            for item in problems:
                print(f"  {item}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    files = [Path(p) for p in args.paths] or _tracked_files()

    if args.check == "all":
        return _run_all(files, args.maxkb)
    if args.check == "trailing-whitespace":
        problems = _check_trailing(files)
    elif args.check == "end-of-file":
        problems = _check_eof(files)
    elif args.check == "mixed-line-ending":
        problems = _check_mixed(files, args.fix)
    elif args.check == "check-toml":
        problems = _check_toml(files)
    elif args.check == "check-merge-conflict":
        problems = _check_conflict(files)
    else:
        problems = _check_large(files, args.maxkb)

    if problems:
        for item in problems:
            print(item)
        print(f"\n{args.check}: {len(problems)} 项")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
