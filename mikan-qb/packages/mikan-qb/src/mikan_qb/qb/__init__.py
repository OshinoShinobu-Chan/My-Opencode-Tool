"""qBittorrent 交互子包，转发 client 模块的公开接口。"""

from mikan_qb.qb.client import QBClient, QBError, QBStatus, walk_feeds

__all__ = ["QBClient", "QBError", "QBStatus", "walk_feeds"]
