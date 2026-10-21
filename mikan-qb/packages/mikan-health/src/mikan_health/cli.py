"""mikan-health 命令行入口：profiles/show 子命令与 Rich 输出。"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mikan_health import DEFAULT_PROFILE, __version__
from mikan_health.console import force_pipe_utf8
from mikan_health.core.policy import Policy, load_profile, profile_names
from mikan_health.paths import profiles_dir

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name="mikan-health",
    help="Mikan 字幕组健康度评估（独立算法包，不需要 qBittorrent）",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    """处理 --version：为真时打印版本并退出。

    Args:
        value: 是否传入 --version。
    """
    if value:
        console.print(f"mikan-health {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="显示版本号并退出"
        ),
    ] = None,
) -> None:
    """Typer 顶层回调，仅承载 --version 选项。

    Args:
        version: 是否传入 --version；None 表示未提供。
    """
    pass


@app.command("profiles")
def profiles_cmd(
    as_json: Annotated[bool, typer.Option("--json", help="以 JSON 输出")] = False,
) -> None:
    """列出可用 profile 及其模式与说明。

    Args:
        as_json: 为真时以 JSON 输出，否则打印表格。
    """
    names = profile_names()
    if as_json:
        console.print_json(data={"profiles_dir": str(profiles_dir()), "profiles": names})
        return
    table = Table(title="可用 profile")
    table.add_column("名称", style="cyan")
    table.add_column("模式")
    table.add_column("说明")
    for name in names:
        try:
            policy = load_profile(name)
        except Exception as exc:  # noqa: BLE001
            table.add_row(name, "-", f"[red]加载失败：{exc}[/red]")
            continue
        table.add_row(name, policy.profile.mode, policy.profile.description)
    console.print(table)


@app.command("show")
def show_cmd(
    name: Annotated[
        str, typer.Argument(help=f"profile 名称或 .toml 路径，默认 {DEFAULT_PROFILE}")
    ] = DEFAULT_PROFILE,
    as_json: Annotated[bool, typer.Option("--json", help="以 JSON 输出")] = False,
) -> None:
    """打印指定 profile 的全部参数（表格或 JSON）。

    Args:
        name: profile 名称或 .toml 路径，默认 DEFAULT_PROFILE。
        as_json: 为真时以 JSON 输出，否则打印表格。
    """
    try:
        policy = load_profile(name)
    except (FileNotFoundError, ValueError) as exc:
        err_console.print(f"[red]错误：{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if as_json:
        console.print_json(data=policy.model_dump(mode="json"))
        return
    _print_policy(policy)


def _print_policy(policy: Policy) -> None:
    """以多张表格展示 Policy 的阈值、权重与语言分。

    Args:
        policy: 待展示的已加载策略。
    """
    console.print(f"[bold cyan]{policy.profile.name}[/bold cyan]  (mode={policy.profile.mode})")
    console.print(f"[dim]{policy.profile.description}[/dim]\n")

    table = Table(title="阈值与参数", show_header=False)
    table.add_column("项", style="cyan")
    table.add_column("值")
    nested = {"profile", "risk_tier", "weights", "lang_scores"}
    for key, value in policy.model_dump().items():
        if key not in nested:
            table.add_row(key, str(value))
    table.add_row(
        "risk_tier",
        f"low < {policy.risk_tier.low} <= mid <= {policy.risk_tier.high} < high",
    )
    console.print(table)

    weights = Table(title="评分权重")
    weights.add_column("项", style="cyan")
    weights.add_column("值")
    weights.add_row("speed", str(policy.weights.speed))
    weights.add_row("lang", str(policy.weights.lang))
    weights.add_row("stability", str(policy.weights.stability))
    weights.add_row("合计", str(round(policy.weights.total(), 4)))
    console.print(weights)

    langs = Table(title="语言分")
    langs.add_column("标签", style="cyan")
    langs.add_column("分值")
    for label, score in policy.lang_scores.items():
        langs.add_row(label, str(score))
    console.print(langs)


def main() -> None:
    """CLI 主入口：先修正输出编码，再交给 Typer。"""
    force_pipe_utf8()
    app()


if __name__ == "__main__":
    main()
