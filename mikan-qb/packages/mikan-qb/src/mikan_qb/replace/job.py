"""换组替换 job 的数据模型与持久化（jobs/<job_id>.json）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from mikan_qb.paths import jobs_dir

PHASE_AWAITING = "awaiting_verification"
PHASE_DONE = "done"
PHASE_ABORTED = "aborted"


class ReplaceError(Exception):
    """换组替换无法继续（校验不通过 / 任务缺失）时抛出。"""


class TorrentSnapshot(BaseModel):
    """阶段一快照到的一个旧组种子。

    Attributes:
        hash: 种子哈希。
        name: 种子名称。
    """

    hash: str
    name: str


class ReplaceJob(BaseModel):
    """一次换组替换（两阶段事务）的持久化状态。

    Attributes:
        job_id: 事务 id（同时也是文件名）。
        bangumi_id: 番剧 id。
        bangumi_title: 番剧标题。
        from_subgroup_id: 旧字幕组 id；无则 None。
        from_subgroup_name: 旧字幕组名称。
        to_subgroup_id: 新字幕组 id。
        to_subgroup_name: 新字幕组名称。
        mode: 运行模式 fast / normal。
        season: 季度标签。
        phase: 当前阶段（awaiting_verification / done / aborted）。
        created_at: 创建时间。
        updated_at: 最近更新时间。
        plan_file: 新组订阅计划文件路径。
        new_feed_path: 新 feed 路径。
        new_feed_url: 新 feed URL。
        new_feed_created: 新 feed 是否由本工具创建。
        new_rule_name: 新规则名。
        new_tag: 新组的语言标签；无则 None。
        old_feed_path: 旧 feed 路径；无则 None。
        old_feed_created: 旧 feed 是否由本工具创建。
        old_rule_name: 旧规则名；无则 None。
        old_tag: 用于定位该番全部种子的标签；无则 None。
        episodes: 期望新组覆盖的集号列表。
        old_torrents: 阶段一快照的旧组种子。
        notes: 生成 / 执行过程中的提示。
    """

    job_id: str
    bangumi_id: int
    bangumi_title: str = ""
    from_subgroup_id: int | None = None
    from_subgroup_name: str = ""
    to_subgroup_id: int
    to_subgroup_name: str = ""
    mode: str = "normal"
    season: str = ""
    phase: str = PHASE_AWAITING
    created_at: datetime
    updated_at: datetime
    plan_file: str = ""
    new_feed_path: str = ""
    new_feed_url: str = ""
    new_feed_created: bool = False
    new_rule_name: str = ""
    new_tag: str | None = None
    old_feed_path: str | None = None
    old_feed_created: bool = False
    old_rule_name: str | None = None
    old_tag: str | None = None
    episodes: list[float] = Field(default_factory=list)
    old_torrents: list[TorrentSnapshot] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def new_job_id() -> str:
    """生成一个 12 位十六进制的短事务 id。"""
    return uuid.uuid4().hex[:12]


def job_file(job_id: str) -> Path:
    """job 文件路径 jobs/<job_id>.json。

    Args:
        job_id: 事务 id。
    """
    return jobs_dir() / f"{job_id}.json"


def save_job(job: ReplaceJob) -> Path:
    """把 job 写入 jobs/<job_id>.json，刷新 updated_at 并返回路径。

    Args:
        job: 待保存的事务。
    """
    path = job_file(job.job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    job.updated_at = datetime.now()
    path.write_text(job.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_job(job_id: str) -> ReplaceJob:
    """读取指定 job；文件缺失或损坏时抛 ReplaceError。

    Args:
        job_id: 事务 id。
    """
    path = job_file(job_id)
    if not path.is_file():
        raise ReplaceError(f"找不到替换任务 {job_id}（应为 jobs/{job_id}.json）")
    try:
        return ReplaceJob.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ReplaceError(f"替换任务文件损坏：{path}（{exc}）") from exc


def list_jobs() -> list[ReplaceJob]:
    """按创建时间倒序返回全部 job；损坏的文件跳过。"""
    rows: list[ReplaceJob] = []
    for path in jobs_dir().glob("*.json"):
        try:
            rows.append(ReplaceJob.model_validate_json(path.read_text(encoding="utf-8")))
        except ValueError:
            continue
    rows.sort(key=lambda job: job.created_at, reverse=True)
    return rows


def find_active_job(bangumi_id: int) -> ReplaceJob | None:
    """返回该番剧仍处于等待校验阶段的 job；无则 None。

    Args:
        bangumi_id: 番剧 id。
    """
    for job in list_jobs():
        if job.bangumi_id == bangumi_id and job.phase == PHASE_AWAITING:
            return job
    return None
