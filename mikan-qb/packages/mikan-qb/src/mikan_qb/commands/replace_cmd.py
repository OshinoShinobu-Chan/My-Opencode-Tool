"""replace 子命令：换组替换的两阶段事务（默认 dry-run）。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import typer
from rich.table import Table

from mikan_health.data import MikanError
from mikan_qb import EXIT_BAD_PARAMS, EXIT_NEEDS_DECISION
from mikan_qb.analysis import load_schedule
from mikan_qb.commands._shared import (
    JsonFlag,
    RefreshFlag,
    _bangumi,
    _ctx,
    _emit,
    _qb,
    console,
    err_console,
)
from mikan_qb.context import AppContext
from mikan_qb.maintenance.cleanup import auto_cleanup
from mikan_qb.mikan import build_source
from mikan_qb.replace import (
    ReplaceError,
    ReplaceJob,
    abort_replace,
    finalize_replace,
    job_status,
    load_job,
    resolve_old_entry,
    start_replace,
)
from mikan_qb.rules import Plan, PlanError, build_plan, plan_file, save_plan
from mikan_qb.state import ManagedEntry, State, load_state, save_state

replace_app = typer.Typer(help="换组替换（两阶段事务，默认 dry-run）", no_args_is_help=False)

STRATEGY_STRICT = "strict"
STRATEGY_FORCE = "force"


@replace_app.callback(invoke_without_command=True)
def replace_start(
    ctx: typer.Context,
    bangumi_id: Annotated[int | None, typer.Option("--bangumi", help="番剧 id")] = None,
    to_subgroup: Annotated[int | None, typer.Option("--to", help="新字幕组 id")] = None,
    from_subgroup: Annotated[
        int | None, typer.Option("--from", help="旧字幕组 id（该番有多条托管记录时必填）")
    ] = None,
    mode: Annotated[str, typer.Option("--mode", help="fast | normal")] = "normal",
    strategy: Annotated[
        str, typer.Option("--strategy", help="strict=覆盖不全则拒绝；force=照常执行")
    ] = STRATEGY_STRICT,
    season: Annotated[str | None, typer.Option("--season", help="季度标签，如 2026夏")] = None,
    save_path: Annotated[str | None, typer.Option("--save-path", help="覆盖保存路径")] = None,
    backfill: Annotated[
        bool, typer.Option("--backfill/--no-backfill", help="是否回填已播集数")
    ] = True,
    paused: Annotated[bool, typer.Option("--paused", help="回填的种子以暂停状态添加")] = False,
    bgm_enabled: Annotated[
        bool, typer.Option("--bgm/--no-bgm", help="用 Bangumi 已播集数校验覆盖率")
    ] = True,
    apply: Annotated[bool, typer.Option("--apply", help="真正写入 qB")] = False,
    refresh: RefreshFlag = False,
    json_output: JsonFlag = False,
) -> None:
    """阶段一：快照旧组、写入新组订阅并回填，不删除任何旧内容。

    Args:
        bangumi_id: 番剧 id。
        to_subgroup: 新字幕组 id。
        from_subgroup: 旧字幕组 id；None 表示在该番记录中唯一确定。
        mode: 分析模式，fast | normal。
        strategy: 覆盖策略，strict | force。
        season: 季度标签；None 表示自动推断。
        save_path: 覆盖保存路径；None 表示用模板。
        backfill: 是否回填已播集数。
        paused: 回填的种子是否暂停。
        bgm_enabled: 是否用 Bangumi 已播集数校验覆盖率。
        apply: 是否真正写入 qB。
        refresh: 是否忽略缓存强制重新抓取。
        json_output: 是否以 JSON 输出。
    """
    if ctx.invoked_subcommand is not None:
        return
    if bangumi_id is None or to_subgroup is None:
        err_console.print("[red]需要 --bangumi 与 --to（新字幕组 id）[/red]")
        raise typer.Exit(code=EXIT_BAD_PARAMS)

    app_ctx = _ctx(ctx, json_output)
    state = load_state()
    try:
        old_entry = resolve_old_entry(state, bangumi_id, from_subgroup)
    except ReplaceError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=EXIT_NEEDS_DECISION) from exc

    plan = _build_plan(
        app_ctx, bangumi_id, to_subgroup, mode, season, save_path, bgm_enabled, refresh
    )
    problem = _strict_problem(plan, strategy)
    if problem is not None:
        err_console.print(f"[red]{problem}。可加 --strategy force 强制继续[/red]")
        raise typer.Exit(code=EXIT_NEEDS_DECISION)

    save_plan(plan)
    client = _qb(app_ctx)
    old_torrents = client.torrents_by_tag(old_entry.tag) if old_entry.tag else []
    actions = [
        {"item": "feed", "action": "create", "detail": plan.feed_path},
        {"item": "rule", "action": "create", "detail": plan.rule_name},
        {
            "item": "torrents",
            "action": "backfill" if backfill else "skip",
            "detail": f"{len(plan.matches)} 集",
        },
        {
            "item": "old-torrents",
            "action": "snapshot",
            "detail": f"{len(old_torrents)} 个（finalize 时才删除）",
        },
    ]
    payload: dict[str, Any] = {
        "job": None,
        "plan_file": str(plan_file(plan.bangumi_id, plan.subgroup_id)),
        "from_subgroup": old_entry.subgroup_name,
        "to_subgroup": plan.subgroup_name,
        "actions": actions,
        "applied": False,
    }

    def render(data: dict[str, Any]) -> None:
        """渲染阶段一将执行的动作表。

        Args:
            data: 待渲染的字典。
        """
        console.print(
            f"[bold]{plan.bangumi_title}[/bold]  {old_entry.subgroup_name}"
            f" → {plan.subgroup_name}（mode={plan.mode}）"
        )
        _render_actions(data["actions"])

    _emit(app_ctx, payload, render)

    if not apply:
        console.print("[yellow]dry-run[/yellow] 加 --apply 才会写入 qB")
        return

    try:
        job = start_replace(
            client, app_ctx.config, plan, old_entry, backfill=backfill, paused=paused
        )
    except ReplaceError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=EXIT_NEEDS_DECISION) from exc

    _upsert_new_entry(state, plan, job)
    save_state(state)
    payload["job"] = job.model_dump(mode="json")
    payload["applied"] = True
    console.print(f"[green]阶段一完成[/green] job={job.job_id}，旧组内容尚未删除")
    console.print(f"用 `mikan replace status {job.job_id}` 查看进度，下完后 finalize")


@replace_app.command("status")
def replace_status(
    ctx: typer.Context,
    job_id: Annotated[str, typer.Argument(help="替换任务 id")],
    json_output: JsonFlag = False,
) -> None:
    """查看替换任务的新组下载进度与缺失集。

    Args:
        job_id: 替换任务 id。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    job = _load_job_or_exit(job_id)
    client = _qb(app_ctx)
    payload = job_status(client, job, app_ctx.config.taxonomy)

    def render(data: dict[str, Any]) -> None:
        """渲染新组下载进度表。

        Args:
            data: 待渲染的字典。
        """
        _render_status(data)

    _emit(app_ctx, payload, render)


@replace_app.command("finalize")
def replace_finalize(
    ctx: typer.Context,
    job_id: Annotated[str, typer.Argument(help="替换任务 id")],
    apply: Annotated[bool, typer.Option("--apply", help="真正执行删除")] = False,
    keep_torrents: Annotated[
        bool, typer.Option("--keep-torrents", help="保留旧组种子，只删 rule/feed")
    ] = False,
    force: Annotated[bool, typer.Option("--force", help="新组未下完也强制完成")] = False,
    json_output: JsonFlag = False,
) -> None:
    """阶段二：校验覆盖 → 删旧组种子+文件 → 删旧组 rule/feed。

    Args:
        job_id: 替换任务 id。
        apply: 是否真正执行。
        keep_torrents: 是否保留旧组种子。
        force: 新组未下完时是否强制完成。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    job = _load_job_or_exit(job_id)
    client = _qb(app_ctx)
    status = job_status(client, job, app_ctx.config.taxonomy)
    if status["missing"] and not force:
        err_console.print(
            f"[red]新组仍有 {len(status['missing'])} 集未下完：{status['missing']}；"
            "补齐后重试，或用 --force 强制完成[/red]"
        )
        raise typer.Exit(code=EXIT_NEEDS_DECISION)

    payload: dict[str, Any] = {
        "job_id": job.job_id,
        "phase": job.phase,
        "missing": status["missing"],
        "delete_torrents": 0 if keep_torrents else len(job.old_torrents),
        "remove_rule": job.old_rule_name,
        "remove_feed": job.old_feed_path if job.old_feed_created else None,
        "applied": False,
    }

    def render(data: dict[str, Any]) -> None:
        """渲染阶段二将删除的对象。

        Args:
            data: 待渲染的字典。
        """
        console.print(
            f"[bold]finalize {job.job_id}[/bold]  旧组 {job.from_subgroup_name}"
            f" → 新组 {job.to_subgroup_name}"
        )
        _render_actions(
            [
                {"item": "torrents", "action": "delete", "detail": str(data["delete_torrents"])},
                {"item": "rule", "action": "remove", "detail": data["remove_rule"] or "-"},
                {"item": "feed", "action": "remove", "detail": data["remove_feed"] or "保留"},
            ]
        )

    _emit(app_ctx, payload, render)

    if not apply:
        console.print("[yellow]dry-run[/yellow] 加 --apply 才会删除旧组内容")
        return

    try:
        result = finalize_replace(
            client, job, app_ctx.config.taxonomy, keep_torrents=keep_torrents, force=force
        )
    except ReplaceError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=EXIT_NEEDS_DECISION) from exc

    state = load_state()
    if job.from_subgroup_id is not None:
        state.drop(job.bangumi_id, job.from_subgroup_id)
    save_state(state)
    auto_cleanup(state)
    payload.update(result)
    payload["applied"] = True
    console.print(
        f"[green]已删除旧组[/green] 种子 {result['deleted_torrents']} 个"
        f"，rule={result['removed_rule']}，feed={result['removed_feed']}"
    )


@replace_app.command("abort")
def replace_abort(
    ctx: typer.Context,
    job_id: Annotated[str, typer.Argument(help="替换任务 id")],
    apply: Annotated[bool, typer.Option("--apply", help="真正执行回滚")] = False,
    purge_new: Annotated[
        bool, typer.Option("--purge-new", help="同时删除新组已下载的种子与文件")
    ] = False,
    json_output: JsonFlag = False,
) -> None:
    """中止回滚：删新组 rule/feed，可选一并删除已下的新种子。

    Args:
        job_id: 替换任务 id。
        apply: 是否真正执行。
        purge_new: 是否删除新组已下载的种子与文件。
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    job = _load_job_or_exit(job_id)
    client = _qb(app_ctx)
    status = job_status(client, job, app_ctx.config.taxonomy)
    payload: dict[str, Any] = {
        "job_id": job.job_id,
        "phase": job.phase,
        "purge_new": purge_new,
        "new_torrents": len(status["torrents"]),
        "remove_rule": job.new_rule_name,
        "remove_feed": job.new_feed_path if job.new_feed_created else None,
        "applied": False,
    }

    def render(data: dict[str, Any]) -> None:
        """渲染回滚将删除的新组对象。

        Args:
            data: 待渲染的字典。
        """
        console.print(f"[bold]abort {job.job_id}[/bold]  回滚新组 {job.to_subgroup_name}")
        _render_actions(
            [
                {"item": "rule", "action": "remove", "detail": data["remove_rule"] or "-"},
                {"item": "feed", "action": "remove", "detail": data["remove_feed"] or "保留"},
                {
                    "item": "new-torrents",
                    "action": "delete" if data["purge_new"] else "keep",
                    "detail": str(data["new_torrents"]),
                },
            ]
        )

    _emit(app_ctx, payload, render)

    if not apply:
        console.print("[yellow]dry-run[/yellow] 加 --apply 才会执行回滚")
        return

    try:
        result = abort_replace(client, job, app_ctx.config.taxonomy, purge_new=purge_new)
    except ReplaceError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=EXIT_NEEDS_DECISION) from exc

    state = load_state()
    state.drop(job.bangumi_id, job.to_subgroup_id)
    save_state(state)
    payload.update(result)
    payload["applied"] = True
    console.print(
        f"[green]已回滚[/green] rule={result['removed_rule']}，feed={result['removed_feed']}"
        f"，清理新种子 {result['purged_torrents']} 个"
    )


def _build_plan(
    app_ctx: AppContext,
    bangumi_id: int,
    to_subgroup: int,
    mode: str,
    season: str | None,
    save_path: str | None,
    bgm_enabled: bool,
    refresh: bool,
) -> Plan:
    """抓取番剧与新组发布并构建订阅计划；失败时以退出码 3 结束。

    Args:
        app_ctx: 应用上下文。
        bangumi_id: 番剧 id。
        to_subgroup: 新字幕组 id。
        mode: 分析模式。
        season: 季度标签；None 表示自动推断。
        save_path: 覆盖保存路径；None 表示用模板。
        bgm_enabled: 是否用 Bangumi 已播集数校验覆盖率。
        refresh: 是否忽略缓存强制重新抓取。
    """
    source = build_source(app_ctx.config, app_ctx.env)
    try:
        detail = source.bangumi(bangumi_id, force=refresh)
        releases = source.subgroup_releases(bangumi_id, to_subgroup, force=refresh)
        schedule = load_schedule(_bangumi(app_ctx, bgm_enabled), detail)
        return build_plan(
            app_ctx.config,
            app_ctx.env,
            detail,
            releases,
            to_subgroup,
            mode,
            season=season,
            save_path_override=save_path,
            expected_episodes=schedule.aired_count(datetime.now()) if schedule else None,
        )
    except (MikanError, PlanError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=EXIT_NEEDS_DECISION) from exc


def _strict_problem(plan: Plan, strategy: str) -> str | None:
    """strict 策略下检查新组能否完整覆盖；有问题返回描述，否则 None。

    Args:
        plan: 新字幕组订阅计划。
        strategy: 覆盖策略，strict | force。
    """
    if strategy != STRATEGY_STRICT:
        return None
    problems: list[str] = []
    if not plan.valid:
        problems.append("规则模拟或覆盖率未通过")
    if plan.gaps:
        problems.append(f"存在漏集 {plan.gaps}")
    return "；".join(problems) if problems else None


def _upsert_new_entry(state: State, plan: Plan, job: ReplaceJob) -> None:
    """把新组的订阅写入托管清单（旧记录保留到 finalize）。

    Args:
        state: 托管清单。
        plan: 新字幕组订阅计划。
        job: 刚创建的替换任务，提供 feed 复用信息。
    """
    tags = plan.rule_def.get("torrentParams", {}).get("tags") or []
    state.upsert(
        ManagedEntry(
            bangumi_id=plan.bangumi_id,
            subgroup_id=plan.subgroup_id,
            bangumi_title=plan.bangumi_title,
            subgroup_name=plan.subgroup_name,
            season=plan.season,
            mode=plan.mode,
            feed_path=job.new_feed_path,
            feed_url=plan.feed_url,
            feed_created=job.new_feed_created,
            rule_name=plan.rule_name,
            tag=next((t for t in tags if t), None),
            save_path=str(plan.rule_def.get("savePath") or ""),
        )
    )


def _load_job_or_exit(job_id: str) -> ReplaceJob:
    """读取替换任务，失败时以退出码 1 结束。

    Args:
        job_id: 替换任务 id。
    """
    try:
        return load_job(job_id)
    except ReplaceError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=EXIT_BAD_PARAMS) from exc


def _render_actions(rows: list[dict[str, str]]) -> None:
    """把动作列表渲染成三列表格。

    Args:
        rows: 含 item / action / detail 的动作列表。
    """
    table = Table(title="将执行的动作")
    table.add_column("对象", style="cyan")
    table.add_column("动作")
    table.add_column("详情")
    for row in rows:
        table.add_row(row["item"], row["action"], row["detail"])
    console.print(table)


def _render_status(data: dict[str, Any]) -> None:
    """渲染 job 状态与种子进度表。

    Args:
        data: job_status 的返回字典。
    """
    head = Table(title=f"替换任务 {data['job_id']}", show_header=False)
    head.add_column("项", style="cyan")
    head.add_column("值")
    head.add_row("阶段", data["phase"])
    head.add_row("番剧", f"{data['bangumi_title']}（{data['bangumi_id']}）")
    head.add_row("换组", f"{data['from_subgroup']} → {data['to_subgroup']}")
    head.add_row("进度", f"已完成 {len(data['completed'])}/{data['expected']}")
    head.add_row("缺失集", str(data["missing"]) if data["missing"] else "无")
    console.print(head)
    if not data["torrents"]:
        return
    table = Table(title="新组种子")
    table.add_column("集", justify="right", no_wrap=True)
    table.add_column("进度", justify="right", no_wrap=True)
    table.add_column("完成", justify="center", no_wrap=True)
    table.add_column("名称", overflow="ellipsis", max_width=60, no_wrap=True)
    for row in data["torrents"]:
        table.add_row(
            str(row["episode"]) if row["episode"] is not None else "-",
            f"{row['progress']:.0%}",
            "[green]是[/green]" if row["completed"] else "[yellow]否[/yellow]",
            row["name"],
        )
    console.print(table)
