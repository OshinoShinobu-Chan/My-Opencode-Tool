"""config 子命令：生成、查看与校验配置。"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from mikan_qb import EXIT_BAD_PARAMS
from mikan_qb.commands._shared import JsonFlag, _ctx, _emit, console
from mikan_qb.config import effective_mikan_base, env_example_path, validate_config
from mikan_qb.paths import default_config_path, find_config_path, find_env_path

config_app = typer.Typer(help="配置管理", no_args_is_help=True)


@config_app.command("init")
def config_init(
    ctx: typer.Context,
    force: Annotated[bool, typer.Option("--force", help="覆盖已存在的文件")] = False,
) -> None:
    """生成 config.toml 与 .env.example 模板。

    Args:
        force: 是否覆盖已存在的文件。
    """
    target_config = find_config_path(ctx.obj.config)
    target_env_example = env_example_path()

    if target_config.exists() and not force:
        console.print(f"[yellow]已存在，跳过：[/yellow]{target_config}")
    else:
        target_config.parent.mkdir(parents=True, exist_ok=True)
        template = default_config_path().read_text(encoding="utf-8")
        target_config.write_text(template, encoding="utf-8")
        console.print(f"[green]已写入[/green] {target_config}")

    if target_env_example.exists() and not force:
        console.print(f"[yellow]已存在，跳过：[/yellow]{target_env_example}")
    else:
        target_env_example.parent.mkdir(parents=True, exist_ok=True)
        target_env_example.write_text(
            "# 复制为 .env 并填写凭据\n"
            "QB_HOST=http://www.crimmy.top:8000\n"
            "QB_USERNAME=\n"
            "QB_PASSWORD=\n"
            "MIKAN_BASE=https://mikanani.kas.pub\n",
            encoding="utf-8",
        )
        console.print(f"[green]已写入[/green] {target_env_example}")

    env_file = find_env_path(ctx.obj.env)
    if not env_file.is_file():
        console.print(
            f"[yellow]下一步：[/yellow]把 {target_env_example.name} 复制为 {env_file} "
            "并填写 QB_USERNAME / QB_PASSWORD"
        )


@config_app.command("show")
def config_show(ctx: typer.Context, json_output: JsonFlag = False) -> None:
    """打印生效配置，敏感值脱敏显示。

    Args:
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    payload = {
        "config_path": str(app_ctx.config_path),
        "env_path": str(app_ctx.env_path) if app_ctx.env_path else None,
        "mikan": app_ctx.config.mikan.model_dump(),
        "qb": app_ctx.config.qb.model_dump(),
        "bangumi": app_ctx.config.bangumi.model_dump(),
        "taxonomy": app_ctx.config.taxonomy.model_dump(),
        "analysis": app_ctx.config.analysis.model_dump(),
        "env": {
            "qb_host": app_ctx.env.qb_host,
            "qb_username": app_ctx.env.qb_username,
            "qb_password": "***" if app_ctx.env.qb_password else "",
            "mikan_base": app_ctx.env.mikan_base,
            "bgm_token": "***" if app_ctx.env.bgm_token else "",
            "bgm_proxy": app_ctx.env.bgm_proxy,
        },
        "effective_mikan_base": effective_mikan_base(app_ctx.config, app_ctx.env),
    }

    def render(data: dict) -> None:
        """渲染生效配置表（敏感值已脱敏）。

        Args:
            data: 待渲染的字典。
        """
        console.print(f"[bold]config[/bold] {data['config_path']}")
        console.print(f"[bold]env   [/bold] {data['env_path'] or '(未找到 .env)'}\n")
        table = Table(title="生效配置", show_header=False)
        table.add_column("项", style="cyan")
        table.add_column("值")
        table.add_row("mikan.base_url", data["effective_mikan_base"])
        table.add_row("mikan.rate_limit_per_sec", str(data["mikan"]["rate_limit_per_sec"]))
        table.add_row("mikan.cache_ttl_hours", str(data["mikan"]["cache_ttl_hours"]))
        table.add_row("qb.category", data["qb"]["category"])
        table.add_row("qb.save_root", data["qb"]["save_root"] or "(空)")
        table.add_row("qb.save_path_template", data["qb"]["save_path_template"])
        table.add_row("qb.feed_folder_template", data["qb"]["feed_folder_template"])
        table.add_row("analysis.profile", data["analysis"]["profile"])
        table.add_row("bangumi.enabled", str(data["bangumi"]["enabled"]))
        table.add_row("QB_HOST", data["env"]["qb_host"] or "(未配置)")
        table.add_row("QB_USERNAME", data["env"]["qb_username"] or "(未配置)")
        table.add_row("QB_PASSWORD", data["env"]["qb_password"] or "(未配置)")
        table.add_row("BGM_TOKEN", data["env"]["bgm_token"] or "(未配置)")
        table.add_row("BGM_PROXY", data["env"]["bgm_proxy"] or "(自动探测)")
        console.print(table)

    _emit(app_ctx, payload, render)


@config_app.command("validate")
def config_validate(ctx: typer.Context, json_output: JsonFlag = False) -> None:
    """校验配置与环境，有错误时以退出码 1 结束。

    Args:
        json_output: 是否以 JSON 输出。
    """
    app_ctx = _ctx(ctx, json_output)
    errors, warnings = validate_config(app_ctx.config, app_ctx.env)
    payload = {"ok": not errors, "errors": errors, "warnings": warnings}

    def render(data: dict) -> None:
        """打印配置校验的警告与错误。

        Args:
            data: 待渲染的字典。
        """
        for message in data["warnings"]:
            console.print(f"[yellow]警告[/yellow] {message}")
        for message in data["errors"]:
            console.print(f"[red]错误[/red] {message}")
        if data["ok"]:
            console.print("[green]配置校验通过[/green]")

    _emit(app_ctx, payload, render)
    if errors:
        raise typer.Exit(code=EXIT_BAD_PARAMS)
