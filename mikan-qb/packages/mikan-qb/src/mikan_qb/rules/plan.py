"""订阅计划的构建、持久化与和 qB 现状的差异对比。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from mikan_health.core.policy import Policy, load_profile
from mikan_health.core.variants import Solution, solve
from mikan_health.data.models import BangumiDetail, Release, Subgroup
from mikan_qb.config import AppConfig, EnvSettings, expand_template
from mikan_qb.paths import plans_dir

PLAN_VERSION = 1
ILLEGAL_PATH_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
SEASON_BY_MONTH = {
    1: "冬",
    2: "冬",
    3: "冬",
    4: "春",
    5: "春",
    6: "春",
    7: "夏",
    8: "夏",
    9: "夏",
    10: "秋",
    11: "秋",
    12: "秋",
}


class PlanError(Exception):
    """计划构建或读取失败时抛出。"""


class PlanFilter(BaseModel):
    """qB 规则的三项过滤条件。

    Attributes:
        must_contain: 必须命中的正则 / 子串。
        must_not_contain: 必须排除的正则 / 子串。
        use_regex: 是否按正则匹配。
    """

    must_contain: str
    must_not_contain: str
    use_regex: bool


class PlanVariant(BaseModel):
    """计划选用的一个发布版本（语言 / 版本 / 集数）。

    Attributes:
        key: 变体标识（语言|字幕类型|token）。
        language: 语言标签。
        sub_type: 字幕类型；无则 None。
        token: 标题中的代表 token；无则 None。
        label: 人类可读标签。
        episode_count: 该变体覆盖的集数。
    """

    key: str
    language: str
    sub_type: str | None = None
    token: str | None = None
    label: str
    episode_count: int = 0


class PlanMatch(BaseModel):
    """计划中单集命中的种子信息。

    Attributes:
        episode: 集号。
        title: 命中标题。
        torrent_url: 种子链接。
        magnet: 磁力链接。
        size_bytes: 文件大小（字节）。
        published_at: 发布时间。
    """

    episode: float
    title: str
    torrent_url: str | None = None
    magnet: str | None = None
    size_bytes: int | None = None
    published_at: datetime | None = None


class Plan(BaseModel):
    """一份完整的订阅计划（feed、规则、匹配集与模拟结论）。

    Attributes:
        version: 计划格式版本。
        created_at: 生成时间。
        bangumi_id: 番剧 id。
        bangumi_title: 番剧标题。
        subgroup_id: 字幕组 id。
        subgroup_name: 字幕组名称。
        season: 季度标签（如 2026夏）。
        mode: 运行模式 fast / normal。
        language: 选中变体的语言。
        rule_lang: 规则名中使用的语言标签。
        anime_dir: 番剧目录名（已清洗非法字符）。
        feed_folder: feed 所在目录。
        feed_path: feed 完整路径。
        feed_url: Mikan RSS URL。
        feed_refresh_interval: feed 刷新间隔（分钟）。
        rule_name: qB 规则名。
        rule_def: qB 规则定义（mustContain / savePath 等）。
        variants: 选中的变体。
        all_variants: 全部候选变体。
        matches: 命中集与种子。
        gaps: 漏集集号。
        warnings: 生成过程中的警告。
        simulation: 模拟汇总（episode_count / gaps / multiple 等）。
        valid: 模拟与覆盖率是否通过。
        source: 数据来源（如 bangumi-page）。
    """

    version: int = PLAN_VERSION
    created_at: datetime
    bangumi_id: int
    bangumi_title: str
    subgroup_id: int
    subgroup_name: str
    season: str
    mode: str
    language: str
    rule_lang: str
    anime_dir: str
    feed_folder: str
    feed_path: str
    feed_url: str
    feed_refresh_interval: int
    rule_name: str
    rule_def: dict
    variants: list[PlanVariant] = Field(default_factory=list)
    all_variants: list[PlanVariant] = Field(default_factory=list)
    matches: list[PlanMatch] = Field(default_factory=list)
    gaps: list[float] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    simulation: dict = Field(default_factory=dict)
    valid: bool = True
    source: str = "bangumi-page"


def season_of(detail: BangumiDetail) -> str:
    """由番剧开播日期推断季度标签（如 2026夏）；缺日期时抛 PlanError。

    Args:
        detail: 番剧详情，需含 start_date。
    """
    if detail.start_date is None:
        raise PlanError("番剧缺少放送开始日期，无法推断季度；请用 --season 指定")
    return f"{detail.start_date.year}{SEASON_BY_MONTH[detail.start_date.month]}"


def sanitize_dir(name: str) -> str:
    """去掉文件名非法字符并清理首尾，空则回退为 unknown。

    Args:
        name: 原始目录 / 文件名。
    """
    cleaned = ILLEGAL_PATH_CHARS.sub("", name).strip().rstrip(". ")
    return cleaned or "unknown"


def build_plan(
    config: AppConfig,
    env: EnvSettings,
    detail: BangumiDetail,
    releases: list[Release],
    subgroup_id: int,
    mode: str,
    season: str | None = None,
    policy: Policy | None = None,
    save_path_override: str | None = None,
    expected_episodes: int | None = None,
) -> Plan:
    """为指定字幕组构建订阅计划（feed、qB 规则与集数模拟）。

    模拟或覆盖率不通过时不抛错，只置 valid=False 并记录 warnings。

    Args:
        config: 应用配置，提供模板与 qB 参数。
        env: 环境凭据，用于拼 Mikan 地址。
        detail: 番剧详情。
        releases: 该番剧的发布记录。
        subgroup_id: 目标字幕组 id。
        mode: 运行模式 fast / normal。
        season: 季度标签；None 由开播日期推断。
        policy: 分析策略；None 按 mode 加载。
        save_path_override: 覆盖保存路径；None 用模板展开。
        expected_episodes: 期望集数；None 用同番其他组推断。
    """
    subgroup = _require_subgroup(detail, subgroup_id)
    active_policy = policy or _load_policy(config, mode)
    if active_policy.mode != mode:
        active_policy = active_policy.model_copy(
            update={"profile": active_policy.profile.model_copy(update={"mode": mode})}
        )

    solution = solve(releases, active_policy, config.taxonomy)
    if solution is None:
        raise PlanError(
            "无法生成唯一版本的过滤规则：该字幕组的发布格式无法用一条规则精确覆盖"
            "（存在多版本共存或集数缺失）。请先运行 `mikan rule preview` 查看细节。"
        )

    coverage = _coverage_check(detail, solution.simulation.episodes, expected_episodes)

    warnings: list[str] = []
    valid = solution.simulation.ok and coverage["ok"]
    if not solution.simulation.ok:
        if solution.simulation.multiple:
            warnings.append(
                f"存在同集多命中：{solution.simulation.multiple}；该组同集会发布多个相同标记的版本"
            )
        if solution.simulation.gaps:
            warnings.append(f"存在漏集：{solution.simulation.gaps}")
    if not coverage["ok"]:
        warnings.append(coverage["message"])

    lang_label = _rule_lang(mode, solution)
    season_label = season or season_of(detail)
    anime_dir = sanitize_dir(detail.title)

    feed_folder = expand_template(
        config.qb.feed_folder_template, season=season_label, root=season_label
    )
    feed_name = expand_template(
        config.qb.feed_name_template,
        anime=anime_dir,
        subgroup=sanitize_dir(subgroup.name),
        season=season_label,
        lang=lang_label,
        bangumi_id=detail.bangumi_id,
    )
    feed_path = f"{feed_folder}/{feed_name}" if feed_folder else feed_name
    feed_url = _feed_url(config, env, detail.bangumi_id, subgroup_id)

    rule_name = expand_template(
        config.qb.rule_name_template,
        lang=lang_label,
        anime=anime_dir,
        subgroup=subgroup.name,
        season=season_label,
        bangumi_id=detail.bangumi_id,
    )

    save_path = save_path_override or expand_template(
        config.qb.save_path_template,
        root=config.qb.save_root,
        anime=anime_dir,
        season=season_label,
        subgroup=subgroup.name,
    )

    tags = [t for t in config.qb.tags if t]
    tag_template = config.qb.torrent_tag_template
    if tag_template:
        tags.append(
            expand_template(
                tag_template,
                bangumi_id=detail.bangumi_id,
                anime=anime_dir,
                lang=lang_label,
                subgroup=subgroup.name,
                season=season_label,
            )
        )
    if lang_label and lang_label not in tags:
        tags.append(lang_label)

    rule_def = {
        "enabled": True,
        "mustContain": solution.filter.must_contain,
        "mustNotContain": solution.filter.must_not_contain,
        "useRegex": solution.filter.use_regex,
        "episodeFilter": "",
        "smartFilter": False,
        "previouslyMatchedEpisodes": [],
        "affectedFeeds": [feed_url],
        "ignoreDays": 0,
        "lastMatch": "",
        "addPaused": None,
        "assignedCategory": config.qb.category,
        "priority": 0,
        "savePath": save_path,
        "torrentContentLayout": None,
        "torrentParams": {
            "category": config.qb.category,
            "save_path": save_path,
            "tags": tags,
            "operating_mode": "AutoManaged",
        },
    }

    variants = [_plan_variant(v) for v in solution.variants]
    matches = [
        PlanMatch(
            episode=match.episode,
            title=match.title,
            torrent_url=match.release.torrent_url if match.release else None,
            magnet=match.release.magnet if match.release else None,
            size_bytes=match.release.size_bytes if match.release else None,
            published_at=match.release.published_at if match.release else None,
        )
        for match in solution.simulation.episodes
        if match.matches == 1 and match.release is not None
    ]
    matches.sort(key=lambda m: m.episode)

    return Plan(
        created_at=datetime.now(),
        bangumi_id=detail.bangumi_id,
        bangumi_title=detail.title,
        subgroup_id=subgroup_id,
        subgroup_name=subgroup.name,
        season=season_label,
        mode=mode,
        language=variants[0].language if variants else "无中文",
        rule_lang=lang_label,
        anime_dir=anime_dir,
        feed_folder=feed_folder,
        feed_path=feed_path,
        feed_url=feed_url,
        feed_refresh_interval=config.qb.refresh_interval_minutes,
        rule_name=rule_name,
        rule_def=rule_def,
        variants=variants,
        all_variants=[_plan_variant(v) for v in solution.all_variants],
        matches=matches,
        gaps=solution.simulation.gaps,
        warnings=warnings,
        simulation=solution.simulation.summary(),
        valid=valid,
        source=releases[0].source if releases else "bangumi-page",
    )


def _coverage_check(detail: BangumiDetail, episodes: list, expected_episodes: int | None) -> dict:
    """检查该字幕组的集数是否明显少于同番其他组（或 Bangumi 已播集数）。

    Args:
        detail: 番剧详情，用于统计其他组的集数。
        episodes: 该组命中的集列表。
        expected_episodes: 期望集数；None 时用同番条目最多的组。
    """
    covered = len(episodes)
    reference = expected_episodes
    source = "Bangumi 已播集数"
    if not reference:

        def _count(subgroup_id: int) -> int:
            """统计该字幕组在本页的去重集数。

            Args:
                subgroup_id: 待统计的字幕组 id。
            """
            return len(
                {
                    r.episode
                    for r in detail.releases
                    if r.subtitle_group_id == subgroup_id and r.is_episode
                }
            )

        peer_max = max(
            (_count(s.subtitle_group_id) for s in detail.subgroups),
            default=0,
        )
        reference = peer_max or None
        source = "同番条目数最多的字幕组"
    if not reference:
        return {"ok": True, "message": "", "covered": covered, "reference": None}

    if covered >= 0.6 * reference:
        return {"ok": True, "message": "", "covered": covered, "reference": reference}

    return {
        "ok": False,
        "covered": covered,
        "reference": reference,
        "message": (
            f"该组只覆盖 {covered} 集，而{source}为 {reference} 集，"
            "明显落后于其他字幕组，不建议选它追更"
        ),
    }


def plan_file(bangumi_id: int, subgroup_id: int) -> Path:
    """计划文件的默认路径：plans/{bangumi}-{subgroup}.json。

    Args:
        bangumi_id: 番剧 id。
        subgroup_id: 字幕组 id。
    """
    return plans_dir() / f"{bangumi_id}-{subgroup_id}.json"


def save_plan(plan: Plan, path: Path | None = None) -> Path:
    """把计划写入指定或默认路径，返回实际写入的路径。

    Args:
        plan: 待保存的计划。
        path: 目标路径；None 时用 plan_file 默认路径。
    """
    target = path or plan_file(plan.bangumi_id, plan.subgroup_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return target


def load_plan(path: Path | str) -> Plan:
    """从 JSON 文件读取并校验计划。

    Args:
        path: 计划 JSON 文件路径。
    """
    text = Path(path).read_text(encoding="utf-8")
    return Plan.model_validate(json.loads(text))


def find_plan(bangumi_id: int, subgroup_id: int | None = None) -> Plan | None:
    """按番剧与可选字幕组查找计划；省略字幕组时取文件名最大的一份。

    Args:
        bangumi_id: 番剧 id。
        subgroup_id: 字幕组 id；None 时取最近的一份。
    """
    if subgroup_id is not None:
        target = plan_file(bangumi_id, subgroup_id)
        return load_plan(target) if target.is_file() else None
    candidates = sorted(plans_dir().glob(f"{bangumi_id}-*.json"))
    if not candidates:
        return None
    return load_plan(candidates[-1])


def diff_plan(plan: Plan, client) -> dict:
    """对比计划与 qB 现状，返回 feed / 规则的动作差异列表。

    Args:
        plan: 待对比的订阅计划。
        client: qB 客户端，用于读取现有 feed 与规则。
    """
    feeds = {row["path"]: row for row in client.feeds()}
    rules = client.rules()
    differences: list[dict] = []

    existing_feed = feeds.get(plan.feed_path)
    same_url_path = next((path for path, row in feeds.items() if row["url"] == plan.feed_url), None)
    if existing_feed is None:
        if same_url_path is not None and same_url_path != plan.feed_path:
            differences.append(
                {
                    "item": "feed",
                    "action": "reuse_existing",
                    "detail": f"URL 已存在于 {same_url_path}，无需新建",
                }
            )
        else:
            differences.append({"item": "feed", "action": "create", "detail": plan.feed_path})
    elif existing_feed["url"] != plan.feed_url:
        differences.append(
            {
                "item": "feed",
                "action": "update_url",
                "detail": f"{existing_feed['url']} -> {plan.feed_url}",
            }
        )
    else:
        differences.append({"item": "feed", "action": "unchanged", "detail": plan.feed_path})

    existing_rule = rules.get(plan.rule_name)
    if existing_rule is None:
        differences.append({"item": "rule", "action": "create", "detail": plan.rule_name})
    else:
        changed = [
            key
            for key in ("mustContain", "mustNotContain", "useRegex", "savePath", "affectedFeeds")
            if _normalized(existing_rule.get(key)) != _normalized(plan.rule_def.get(key))
        ]
        tags_now = (existing_rule.get("torrentParams") or {}).get("tags") or []
        tags_new = (plan.rule_def.get("torrentParams") or {}).get("tags") or []
        if sorted(t for t in tags_now if t) != sorted(t for t in tags_new if t):
            changed.append("tags")
        differences.append(
            {
                "item": "rule",
                "action": "update" if changed else "unchanged",
                "detail": ",".join(changed) if changed else plan.rule_name,
            }
        )

    return {
        "feed_path": plan.feed_path,
        "rule_name": plan.rule_name,
        "differences": differences,
        "has_changes": any(d["action"] not in ("unchanged", "reuse_existing") for d in differences),
    }


def _normalized(value: object) -> object:
    """把列表元素统一转为字符串，便于规则字段的稳定比较。

    Args:
        value: 待归一化的规则字段值。
    """
    if isinstance(value, list):
        return [str(v) for v in value]
    return value


def _plan_variant(variant) -> PlanVariant:
    """把内部 variant 转换为可序列化的 PlanVariant。

    Args:
        variant: 求解器给出的内部变体对象。
    """
    return PlanVariant(
        key=variant.key,
        language=variant.language,
        sub_type=variant.sub_type,
        token=variant.token,
        label=variant.label,
        episode_count=len(variant.episodes),
    )


def _rule_lang(mode: str, solution: Solution) -> str:
    """由模式与所选版本推导规则语言标签（fast 固定为「追更」）。

    Args:
        mode: 运行模式 fast / normal。
        solution: 版本求解结果。
    """
    if mode == "fast":
        return "追更"
    labels: list[str] = []
    for variant in solution.variants:
        if variant.language not in labels:
            labels.append(variant.language)
    return "+".join(labels) if labels else "未知"


def _load_policy(config: AppConfig, mode: str) -> Policy:
    """按 mode 解析 profile；找不到自定义目录时回退内置 profile。

    Args:
        config: 应用配置，提供 profile 名与自定义目录。
        mode: 运行模式 fast / normal。
    """
    name = config.analysis.profile
    if mode == "fast" and name.endswith("normal"):
        name = name[: -len("normal")] + "fast"
    try:
        directory = Path(config.analysis.profiles_dir) if config.analysis.profiles_dir else None
        return load_profile(name, directory)
    except FileNotFoundError:
        return load_profile("personal-fast" if mode == "fast" else "personal-normal")


def _require_subgroup(detail: BangumiDetail, subgroup_id: int) -> Subgroup:
    """取字幕组，不存在时抛出带可选列表的 PlanError。

    Args:
        detail: 番剧详情。
        subgroup_id: 目标字幕组 id。
    """
    subgroup = detail.subgroup(subgroup_id)
    if subgroup is None:
        available = ", ".join(f"{s.subtitle_group_id}={s.name}" for s in detail.subgroups)
        raise PlanError(f"番剧 {detail.bangumi_id} 下没有字幕组 {subgroup_id}。可选：{available}")
    return subgroup


def _feed_url(config: AppConfig, env: EnvSettings, bangumi_id: int, subgroup_id: int) -> str:
    """拼出该字幕组的 Mikan RSS 订阅地址。

    Args:
        config: 应用配置，提供 mikan.base_url。
        env: 环境凭据，可覆盖 Mikan 地址。
        bangumi_id: 番剧 id。
        subgroup_id: 字幕组 id。
    """
    from mikan_qb.config import effective_mikan_base

    return (
        f"{effective_mikan_base(config, env)}/RSS/Bangumi"
        f"?bangumiId={bangumi_id}&subgroupid={subgroup_id}"
    )
