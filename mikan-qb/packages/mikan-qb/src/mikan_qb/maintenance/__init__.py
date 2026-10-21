"""维护子命令集合：定期清理。"""

from __future__ import annotations

from mikan_qb.maintenance.cleanup import scan_cleanup

__all__ = ["scan_cleanup"]
