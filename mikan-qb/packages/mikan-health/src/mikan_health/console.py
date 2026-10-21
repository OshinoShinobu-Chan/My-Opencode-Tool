"""Windows 终端与管道编码处理，避免中文乱码。"""

from __future__ import annotations

import sys


def force_pipe_utf8() -> None:
    """管道/重定向时强制 UTF-8，终端内仅替换不可编码字符，避免 Windows 乱码。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            if not stream.isatty():
                reconfigure(encoding="utf-8", errors="replace")
            else:
                reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
