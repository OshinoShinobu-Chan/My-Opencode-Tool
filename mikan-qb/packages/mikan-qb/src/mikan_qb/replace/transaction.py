"""换组替换的两阶段事务：快照旧组 → 写新组 → 校验 → 删旧组 / 回滚。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mikan_health.core.releases import parse_title
from mikan_health.core.taxonomy import Taxonomy
from mikan_qb.config import AppConfig
from mikan_qb.qb import QBClient
from mikan_qb.replace.job import (
    PHASE_ABORTED,
    PHASE_AWAITING,
    PHASE_DONE,
    ReplaceError,
    ReplaceJob,
    TorrentSnapshot,
    find_active_job,
    new_job_id,
    save_job,
)
from mikan_qb.rules import Plan, plan_file
from mikan_qb.state import ManagedEntry, State

COMPLETE_RATIO = 0.999


def lang_tag_of(rule_def: dict) -> str | None:
    """取规则里最后一个非空标签（语言标签）。

    Args:
        rule_def: qB 规则定义。
    """
    tags = [t for t in ((rule_def.get("torrentParams") or {}).get("tags") or []) if t]
    return tags[-1] if tags else None


def resolve_old_entry(
    state: State, bangumi_id: int, from_subgroup: int | None = None
) -> ManagedEntry:
    """定位要替换掉的旧托管记录；缺失 / 有歧义 / 无 tag 时抛 ReplaceError。

    Args:
        state: 托管清单。
        bangumi_id: 番剧 id。
        from_subgroup: 旧字幕组 id；None 表示在该番剧的记录中唯一确定。
    """
    candidates = [e for e in state.entries if e.bangumi_id == bangumi_id]
    if from_subgroup is not None:
        candidates = [e for e in candidates if e.subgroup_id == from_subgroup]
    if not candidates:
        raise ReplaceError(
            f"托管清单里没有番剧 {bangumi_id} 的订阅记录；先 `mikan apply add` 纳管后再换组"
        )
    if from_subgroup is None and len(candidates) > 1:
        ids = ", ".join(str(e.subgroup_id) for e in candidates)
        raise ReplaceError(f"番剧 {bangumi_id} 有多条托管记录（{ids}），请用 --from 指定旧字幕组")
    entry = candidates[-1]
    if not entry.tag:
        raise ReplaceError("旧订阅缺少 tag，无法定位旧种子（W4 只支持带 tag 的新番订阅）")
    return entry


def snapshot_torrents(client: QBClient, tag: str | None) -> list[TorrentSnapshot]:
    """查询该标签下的种子并转为快照；tag 为空返回空列表。

    Args:
        client: qB 客户端。
        tag: 种子标签；None 表示无。
    """
    if not tag:
        return []
    return [
        TorrentSnapshot(hash=torrent.hash, name=torrent.name)
        for torrent in client.torrents_by_tag(tag)
    ]


def start_replace(
    client: QBClient,
    config: AppConfig,
    plan: Plan,
    old_entry: ManagedEntry,
    backfill: bool = True,
    paused: bool = False,
) -> ReplaceJob:
    """阶段一：快照旧组、写入新组 feed/rule 并回填，不删除任何旧内容。

    Args:
        client: qB 客户端。
        config: 应用配置。
        plan: 新字幕组的订阅计划。
        old_entry: 旧订阅的托管记录。
        backfill: 是否回填计划中的已播集。
        paused: 回填的种子是否以暂停状态添加。
    """
    if old_entry.subgroup_id == plan.subgroup_id:
        raise ReplaceError("新旧字幕组相同，无需替换")
    active = find_active_job(plan.bangumi_id)
    if active is not None:
        raise ReplaceError(
            f"番剧 {plan.bangumi_id} 已有进行中的替换任务 {active.job_id}；"
            "请先 `mikan replace finalize` 或 `mikan replace abort`"
        )

    snapshot = snapshot_torrents(client, old_entry.tag)
    client.ensure_category(config.qb.category, config.qb.save_root)

    feeds = {row["path"]: row for row in client.feeds()}
    same_url_path = next((path for path, row in feeds.items() if row["url"] == plan.feed_url), None)
    feed_created = same_url_path is None
    if feed_created:
        client.ensure_feed_parent(plan.feed_path)
        client.add_feed(plan.feed_url, plan.feed_path, plan.feed_refresh_interval)
    client.set_rule(plan.rule_name, plan.rule_def)

    added = _backfill(client, config, plan, paused) if backfill else 0

    job = ReplaceJob(
        job_id=new_job_id(),
        bangumi_id=plan.bangumi_id,
        bangumi_title=plan.bangumi_title,
        from_subgroup_id=old_entry.subgroup_id,
        from_subgroup_name=old_entry.subgroup_name,
        to_subgroup_id=plan.subgroup_id,
        to_subgroup_name=plan.subgroup_name,
        mode=plan.mode,
        season=plan.season,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        plan_file=str(plan_file(plan.bangumi_id, plan.subgroup_id)),
        new_feed_path=plan.feed_path if feed_created else (same_url_path or plan.feed_path),
        new_feed_url=plan.feed_url,
        new_feed_created=feed_created,
        new_rule_name=plan.rule_name,
        new_tag=lang_tag_of(plan.rule_def),
        old_feed_path=old_entry.feed_path or None,
        old_feed_created=old_entry.feed_created,
        old_rule_name=old_entry.rule_name or None,
        old_tag=old_entry.tag,
        episodes=[match.episode for match in plan.matches],
        old_torrents=snapshot,
        notes=[f"阶段一已回填 {added} 集"] if added else [],
    )
    save_job(job)
    return job


def job_status(client: QBClient, job: ReplaceJob, taxonomy: Taxonomy) -> dict[str, Any]:
    """汇总新组下载进度：已完成 / 进行中 / 缺失集。

    Args:
        client: qB 客户端。
        job: 替换任务。
        taxonomy: 标题词典，用于从种子名解析集号。
    """
    known = {item.hash for item in job.old_torrents}
    torrents = (
        [t for t in client.torrents_by_tag(job.old_tag) if getattr(t, "hash", "") not in known]
        if job.old_tag
        else []
    )
    completed: set[float] = set()
    in_progress: set[float] = set()
    rows: list[dict[str, Any]] = []
    for torrent in torrents:
        episode = parse_title(str(getattr(torrent, "name", "")), taxonomy).episode
        ratio = _ratio(torrent)
        if episode is not None:
            (completed if ratio >= COMPLETE_RATIO else in_progress).add(episode)
        rows.append(
            {
                "hash": getattr(torrent, "hash", ""),
                "name": getattr(torrent, "name", ""),
                "episode": episode,
                "progress": round(ratio, 3),
                "completed": ratio >= COMPLETE_RATIO,
            }
        )
    missing = [episode for episode in job.episodes if episode not in completed]
    return {
        "job_id": job.job_id,
        "phase": job.phase,
        "bangumi_id": job.bangumi_id,
        "bangumi_title": job.bangumi_title,
        "from_subgroup_id": job.from_subgroup_id,
        "from_subgroup": job.from_subgroup_name,
        "to_subgroup_id": job.to_subgroup_id,
        "to_subgroup": job.to_subgroup_name,
        "expected": len(job.episodes),
        "completed": sorted(completed),
        "in_progress": sorted(in_progress),
        "missing": missing,
        "torrents": rows,
        "complete": not missing,
    }


def finalize_replace(
    client: QBClient,
    job: ReplaceJob,
    taxonomy: Taxonomy,
    keep_torrents: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """阶段二：校验新组覆盖 → 删旧组种子 + 文件 → 删旧组 rule/feed。

    Args:
        client: qB 客户端。
        job: 替换任务。
        taxonomy: 标题词典，用于校验集数覆盖。
        keep_torrents: 是否保留旧组种子（默认删除并删文件）。
        force: 校验未通过时是否仍强制完成。
    """
    _require_phase(job, PHASE_AWAITING, "finalize")
    status = job_status(client, job, taxonomy)
    if status["missing"] and not force:
        raise ReplaceError(
            f"新组仍有 {len(status['missing'])} 集未下完：{status['missing']}；"
            "补齐后重试，或用 --force 强制完成"
        )

    deleted = 0
    if not keep_torrents and job.old_torrents:
        client.delete_torrents([item.hash for item in job.old_torrents], delete_files=True)
        deleted = len(job.old_torrents)
    removed_rule = _remove_rule(client, job.old_rule_name)
    removed_feed = _remove_old_feed(client, job)

    job.phase = PHASE_DONE
    save_job(job)
    return {
        "job_id": job.job_id,
        "phase": job.phase,
        "deleted_torrents": deleted,
        "removed_rule": removed_rule,
        "removed_feed": removed_feed,
        "missing": status["missing"],
    }


def abort_replace(
    client: QBClient, job: ReplaceJob, taxonomy: Taxonomy, purge_new: bool = False
) -> dict[str, Any]:
    """中止回滚：删新组 rule/feed，可选删除已下的新种子与文件。

    Args:
        client: qB 客户端。
        job: 替换任务。
        taxonomy: 标题词典，用于统计待清理的新种子。
        purge_new: 是否一并删除新组已下载的种子与文件。
    """
    _require_phase(job, PHASE_AWAITING, "abort")
    purged = 0
    if purge_new:
        hashes = [
            row["hash"] for row in job_status(client, job, taxonomy)["torrents"] if row["hash"]
        ]
        client.delete_torrents(hashes, delete_files=True)
        purged = len(hashes)
    removed_rule = _remove_rule(client, job.new_rule_name)
    removed_feed = False
    if job.new_feed_created and job.new_feed_path:
        client.remove_item(job.new_feed_path)
        removed_feed = True

    job.phase = PHASE_ABORTED
    save_job(job)
    return {
        "job_id": job.job_id,
        "phase": job.phase,
        "purged_torrents": purged,
        "removed_rule": removed_rule,
        "removed_feed": removed_feed,
    }


def _backfill(client: QBClient, config: AppConfig, plan: Plan, paused: bool) -> int:
    """把计划中已播集的种子加入 qB，返回尝试添加的条数。

    Args:
        client: qB 客户端。
        config: 应用配置，提供分类名。
        plan: 新字幕组订阅计划。
        paused: 是否以暂停状态添加。
    """
    urls: list[str] = []
    for match in plan.matches:
        url = match.torrent_url or match.magnet
        if url:
            urls.append(url)
    if not urls:
        return 0
    client.add_torrents(
        urls,
        save_path=plan.rule_def.get("savePath") or "",
        category=config.qb.category,
        tags=[t for t in (plan.rule_def.get("torrentParams", {}).get("tags") or []) if t],
        paused=paused,
    )
    return len(urls)


def _ratio(torrent: Any) -> float:
    """读种子的完成比例，读不到按 1.0（已完成）处理。

    Args:
        torrent: qB 种子对象。
    """
    try:
        return float(getattr(torrent, "progress", 1.0))
    except (TypeError, ValueError):
        return 1.0


def _require_phase(job: ReplaceJob, expected: str, action: str) -> None:
    """校验 job 阶段；不符时抛 ReplaceError。

    Args:
        job: 替换任务。
        expected: 期望阶段。
        action: 正在执行的动作名（用于报错）。
    """
    if job.phase != expected:
        raise ReplaceError(f"任务 {job.job_id} 当前阶段为 {job.phase}，不能 {action}")


def _remove_rule(client: QBClient, name: str | None) -> bool:
    """删除规则；name 为空时不动返回 False。

    Args:
        client: qB 客户端。
        name: 规则名；None 或空串表示无。
    """
    if not name:
        return False
    client.remove_rule(name)
    return True


def _remove_old_feed(client: QBClient, job: ReplaceJob) -> bool:
    """删除旧 feed（仅当由本工具创建）；返回是否删除。

    Args:
        client: qB 客户端。
        job: 替换任务。
    """
    if job.old_feed_created and job.old_feed_path:
        client.remove_item(job.old_feed_path)
        return True
    return False
