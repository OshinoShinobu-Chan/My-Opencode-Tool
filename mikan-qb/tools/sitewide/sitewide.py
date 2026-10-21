"""全站字幕组评估：采集 / 聚合 / 报告（详见仓库根目录 SITEWIDE.md）。

分阶段、可中断续采，数据落 data/sitewide.sqlite3：

    seasons   抓季表，登记番剧与归属季度
    anime     抓番剧页，登记字幕组、参与关系与最近发布
    deep      对活跃组的季番作品全量深采发布（ExpandEpisodeTable）
    schedule  对「季番 × 深采作品」取 Bangumi 精确放送日程
    status    打印各阶段进度
    aggregate 计算 D1–D4 指标并写 reports/sitewide/<range>/groups.csv
    report    由 groups.csv 生成 summary.md 与 PNG 图表（需 matplotlib）

默认窗口 2024 至今，示例：
    SW=tools/sitewide/sitewide.py
    uv run --all-packages python $SW seasons
    uv run --all-packages python $SW anime
    uv run --all-packages python $SW status
    uv run --all-packages python $SW deep
    uv run --all-packages python $SW schedule
    uv run --all-packages python $SW aggregate
    uv run --with matplotlib python $SW report
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from mikan_health.console import force_pipe_utf8
from mikan_health.core.airtime import build_anchor, build_schedule
from mikan_health.core.episodes import (
    episode_events,
    median_or_none,
    relative_delays_hours,
    subtitle_baseline,
)
from mikan_health.core.policy import Policy, load_profile
from mikan_health.core.risk import WorkRecord, assess_risk, season_is_recent
from mikan_health.data.models import BangumiDetail, Release, SeasonEntry
from mikan_qb.context import GlobalOptions, build_context
from mikan_qb.mikan import build_bangumi_client, build_source
from mikan_qb.paths import data_dir, repo_root

SEASON_ORDER = ["冬", "春", "夏", "秋"]
SEASON_MONTH = {"冬": 1, "春": 4, "夏": 7, "秋": 10}
MIN_WORKS = 3
MAX_SEASONAL_EPISODES = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS season_anime (
    season     TEXT NOT NULL,
    bangumi_id INTEGER NOT NULL,
    title      TEXT,
    PRIMARY KEY (season, bangumi_id)
);
CREATE TABLE IF NOT EXISTS anime (
    bangumi_id   INTEGER PRIMARY KEY,
    title        TEXT,
    season       TEXT,
    start_date   TEXT,
    weekday      TEXT,
    bgm_subject_id INTEGER,
    max_episode  REAL,
    is_seasonal  INTEGER DEFAULT 0,
    fetched_at   REAL
);
CREATE TABLE IF NOT EXISTS subgroup (
    subgroup_id      INTEGER PRIMARY KEY,
    name             TEXT,
    publish_group_id INTEGER,
    works            INTEGER DEFAULT 0,
    active           INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS participation (
    bangumi_id INTEGER NOT NULL,
    subgroup_id INTEGER NOT NULL,
    PRIMARY KEY (bangumi_id, subgroup_id)
);
CREATE TABLE IF NOT EXISTS release (
    bangumi_id     INTEGER NOT NULL,
    subgroup_id    INTEGER NOT NULL,
    title          TEXT NOT NULL,
    episode        REAL,
    episode_raw    TEXT,
    language       TEXT,
    language_source TEXT,
    sub_type       TEXT,
    is_pack        INTEGER DEFAULT 0,
    is_extra       INTEGER DEFAULT 0,
    published_at   TEXT,
    size_bytes     INTEGER,
    PRIMARY KEY (bangumi_id, subgroup_id, title)
);
CREATE TABLE IF NOT EXISTS deep (
    bangumi_id  INTEGER NOT NULL,
    subgroup_id INTEGER NOT NULL,
    fetched_at  REAL,
    PRIMARY KEY (bangumi_id, subgroup_id)
);
CREATE TABLE IF NOT EXISTS schedule (
    bangumi_id     INTEGER PRIMARY KEY,
    airdates       TEXT,
    total_episodes INTEGER,
    fetched_at     REAL
);
"""


def db_path() -> Path:
    """全站评估数据库路径。"""
    return data_dir() / "sitewide.sqlite3"


def season_label(year: int, season: str) -> str:
    """拼季度标签（如 2024春）。

    Args:
        year: 年份。
        season: 季度中文（冬/春/夏/秋）。
    """
    return f"{year}{season}"


def season_of(start: date) -> str:
    """由日期推出季度标签。

    Args:
        start: 开播日期。
    """
    if start.month <= 3:
        season = "冬"
    elif start.month <= 6:
        season = "春"
    elif start.month <= 9:
        season = "夏"
    else:
        season = "秋"
    return season_label(start.year, season)


def iter_seasons(from_year: int, to_year: int, today: date) -> list[tuple[int, str]]:
    """列出窗口内已开播的 (年, 季度)，跳过未来季度。

    Args:
        from_year: 起始年。
        to_year: 结束年（含）。
        today: 当前日期，用于跳过未来季度。
    """
    result: list[tuple[int, str]] = []
    for year in range(from_year, to_year + 1):
        for season in SEASON_ORDER:
            anchor = date(year, SEASON_MONTH[season], 1)
            if anchor <= today:
                result.append((year, season))
    return result


def season_date(label: str) -> str:
    """把 2024春 转成 risk 模块使用的 2024.4 形式。

    Args:
        label: 形如 2024春 的季度标签。
    """
    year = label[:4]
    season = label[4:]
    return f"{year}.{SEASON_MONTH.get(season, 1)}"


class Store:
    """全站评估的 SQLite 存取（采集与聚合共用）。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- 采集写入 ----

    def set_season(self, year: int, season: str, entries: list[SeasonEntry]) -> None:
        """登记某季番剧。

        Args:
            year: 年份。
            season: 季度中文。
            entries: 该季番剧列表。
        """
        label = season_label(year, season)
        rows = [(label, e.bangumi_id, e.title) for e in entries]
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO season_anime (season, bangumi_id, title) VALUES (?, ?, ?)",
                rows,
            )

    def pending_anime(self, from_year: int, to_year: int, limit: int | None) -> list[int]:
        """尚未抓取番剧页的 bangumi_id 列表。

        Args:
            from_year: 窗口起始年。
            to_year: 窗口结束年。
            limit: 最多返回条数；None 表示不限。
        """
        sql = (
            "SELECT DISTINCT s.bangumi_id FROM season_anime s "
            "LEFT JOIN anime a ON a.bangumi_id = s.bangumi_id "
            "WHERE a.fetched_at IS NULL AND CAST(substr(s.season, 1, 4) AS INT) BETWEEN ? AND ? "
            "ORDER BY s.bangumi_id"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._connect() as conn:
            return [int(r[0]) for r in conn.execute(sql, (from_year, to_year))]

    def save_anime(self, detail: BangumiDetail, season: str) -> None:
        """写入一部番剧的详情、字幕组、参与关系与最近发布。

        Args:
            detail: 番剧详情。
            season: 该番归属季度标签。
        """
        start = detail.start_date.isoformat() if detail.start_date else None
        subject_id = _subject_id(detail.bangumi_tv_url)
        max_ep = max((r.episode or 0) for r in detail.releases) if detail.releases else 0
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO anime (bangumi_id, title, season, start_date, weekday, "
                "bgm_subject_id, max_episode, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(bangumi_id) DO UPDATE SET title=excluded.title, "
                "season=COALESCE(anime.season, excluded.season), start_date=excluded.start_date, "
                "weekday=excluded.weekday, bgm_subject_id=excluded.bgm_subject_id, "
                "max_episode=MAX(COALESCE(anime.max_episode, 0), "
                "COALESCE(excluded.max_episode, 0)), "
                "fetched_at=excluded.fetched_at",
                (
                    detail.bangumi_id,
                    detail.title,
                    season,
                    start,
                    detail.weekday,
                    subject_id,
                    max_ep,
                    time.time(),
                ),
            )
            conn.executemany(
                "INSERT INTO subgroup (subgroup_id, name, publish_group_id) VALUES (?, ?, ?) "
                "ON CONFLICT(subgroup_id) DO UPDATE SET name=excluded.name, "
                "publish_group_id=COALESCE(excluded.publish_group_id, subgroup.publish_group_id)",
                [(s.subtitle_group_id, s.name, s.publish_group_id) for s in detail.subgroups],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO participation (bangumi_id, subgroup_id) VALUES (?, ?)",
                [(detail.bangumi_id, s.subtitle_group_id) for s in detail.subgroups],
            )
            self._write_releases(conn, detail.releases)

    def save_deep(self, bangumi_id: int, subgroup_id: int, releases: list[Release]) -> None:
        """写入某组某番的全量发布并标记已深采。

        Args:
            bangumi_id: 番剧 id。
            subgroup_id: 字幕组 id。
            releases: 全量发布记录。
        """
        max_ep = max((r.episode or 0) for r in releases) if releases else 0
        with self._connect() as conn:
            self._write_releases(conn, releases)
            conn.execute(
                "INSERT OR REPLACE INTO deep "
                "(bangumi_id, subgroup_id, fetched_at) VALUES (?, ?, ?)",
                (bangumi_id, subgroup_id, time.time()),
            )
            conn.execute(
                "UPDATE anime SET max_episode = MAX(COALESCE(max_episode, 0), ?) "
                "WHERE bangumi_id = ?",
                (max_ep, bangumi_id),
            )

    def save_schedule(self, bangumi_id: int, schedule_json: str, total: int | None) -> None:
        """写入某番的 Bangumi 日程。

        Args:
            bangumi_id: 番剧 id。
            schedule_json: 放送日期 JSON 数组。
            total: 总集数；未知为 None。
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO schedule "
                "(bangumi_id, airdates, total_episodes, fetched_at) VALUES (?, ?, ?, ?)",
                (bangumi_id, schedule_json, total, time.time()),
            )

    @staticmethod
    def _write_releases(conn: sqlite3.Connection, releases: list[Release]) -> None:
        """批量写入发布记录（以标题去重）。"""
        conn.executemany(
            "INSERT OR REPLACE INTO release (bangumi_id, subgroup_id, title, episode, episode_raw, "
            "language, language_source, sub_type, is_pack, is_extra, published_at, size_bytes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    r.bangumi_id,
                    r.subtitle_group_id,
                    r.title,
                    r.episode,
                    r.episode_raw,
                    r.language,
                    r.language_source,
                    r.sub_type,
                    int(r.is_pack),
                    int(r.is_extra),
                    r.published_at.isoformat() if r.published_at else None,
                    r.size_bytes,
                )
                for r in releases
            ],
        )

    # ---- 状态刷新与查询 ----

    def refresh_flags(self, window_start: date, min_works: int = MIN_WORKS) -> None:
        """重算 is_seasonal / subgroup.works / subgroup.active。

        Args:
            window_start: 季番起始日（早于此起点视作长番/非季番）。
            min_works: 活跃组所需最少作品数。
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE anime SET max_episode = "
                "(SELECT MAX(episode) FROM release r WHERE r.bangumi_id = anime.bangumi_id)"
            )
            conn.execute(
                "UPDATE anime SET is_seasonal = CASE WHEN start_date IS NOT NULL "
                "AND start_date >= ? AND COALESCE(max_episode, 0) <= ? THEN 1 ELSE 0 END",
                (window_start.isoformat(), MAX_SEASONAL_EPISODES),
            )
            conn.execute(
                "UPDATE subgroup SET works = "
                "(SELECT COUNT(DISTINCT p.bangumi_id) FROM participation p "
                "JOIN anime a ON a.bangumi_id = p.bangumi_id "
                "WHERE p.subgroup_id = subgroup.subgroup_id AND a.is_seasonal = 1)"
            )
            conn.execute(
                "UPDATE subgroup SET active = CASE WHEN works >= ? THEN 1 ELSE 0 END", (min_works,)
            )

    def pending_deep(self, limit: int | None) -> list[tuple[int, int]]:
        """待深采的 (番剧, 字幕组) 列表。

        Args:
            limit: 最多返回条数；None 表示不限。
        """
        sql = (
            "SELECT p.bangumi_id, p.subgroup_id FROM participation p "
            "JOIN anime a ON a.bangumi_id = p.bangumi_id "
            "JOIN subgroup g ON g.subgroup_id = p.subgroup_id "
            "LEFT JOIN deep d ON d.bangumi_id = p.bangumi_id AND d.subgroup_id = p.subgroup_id "
            "WHERE a.is_seasonal = 1 AND g.active = 1 AND d.bangumi_id IS NULL "
            "ORDER BY p.bangumi_id"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._connect() as conn:
            return [(int(r[0]), int(r[1])) for r in conn.execute(sql)]

    def pending_schedule(self, limit: int | None) -> list[int]:
        """待取日程的季番 bangumi_id 列表（已有深采数据者）。

        Args:
            limit: 最多返回条数；None 表示不限。
        """
        sql = (
            "SELECT DISTINCT a.bangumi_id FROM anime a JOIN deep d ON d.bangumi_id = a.bangumi_id "
            "LEFT JOIN schedule s ON s.bangumi_id = a.bangumi_id "
            "WHERE a.is_seasonal = 1 AND s.bangumi_id IS NULL AND a.bgm_subject_id IS NOT NULL "
            "ORDER BY a.bangumi_id"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._connect() as conn:
            return [int(r[0]) for r in conn.execute(sql)]

    def progress(self) -> dict[str, int]:
        """返回各阶段计数，用于 status。"""
        queries = {
            "season_anime": "SELECT COUNT(*) FROM season_anime",
            "anime_total": "SELECT COUNT(*) FROM anime",
            "anime_fetched": "SELECT COUNT(*) FROM anime WHERE fetched_at IS NOT NULL",
            "seasonal": "SELECT COUNT(*) FROM anime WHERE is_seasonal = 1",
            "subgroup_total": "SELECT COUNT(*) FROM subgroup",
            "subgroup_active": "SELECT COUNT(*) FROM subgroup WHERE active = 1",
            "participation": "SELECT COUNT(*) FROM participation",
            "release": "SELECT COUNT(*) FROM release",
            "deep_done": "SELECT COUNT(*) FROM deep",
            "schedule_done": "SELECT COUNT(*) FROM schedule",
        }
        with self._connect() as conn:
            return {name: int(conn.execute(sql).fetchone()[0]) for name, sql in queries.items()}

    def load_for_aggregate(self) -> dict[str, object]:
        """一次性读出聚合所需的全部行。"""
        with self._connect() as conn:
            anime = {int(r["bangumi_id"]): dict(r) for r in conn.execute("SELECT * FROM anime")}
            groups = [dict(r) for r in conn.execute("SELECT * FROM subgroup WHERE active = 1")]
            parts = [
                (int(r[0]), int(r[1]))
                for r in conn.execute("SELECT bangumi_id, subgroup_id FROM participation")
            ]
            releases: dict[tuple[int, int], list[Release]] = defaultdict(list)
            for r in conn.execute("SELECT * FROM release"):
                releases[(int(r["bangumi_id"]), int(r["subgroup_id"]))].append(_row_to_release(r))
            schedules = {
                int(r["bangumi_id"]): _row_to_schedule(r)
                for r in conn.execute("SELECT * FROM schedule")
            }
        return {
            "anime": anime,
            "groups": groups,
            "parts": parts,
            "releases": releases,
            "schedules": schedules,
        }


def _subject_id(url: str | None) -> int | None:
    """从 bgm.tv 链接抽取 subject id。"""
    from mikan_health.data.bangumi import subject_id_from_url

    return subject_id_from_url(url)


def _row_to_release(row: sqlite3.Row) -> Release:
    """把 release 行还原为 Release 模型。"""
    published = datetime.fromisoformat(row["published_at"]) if row["published_at"] else None
    return Release(
        bangumi_id=int(row["bangumi_id"]),
        subtitle_group_id=int(row["subgroup_id"]),
        title=row["title"],
        episode=row["episode"],
        episode_raw=row["episode_raw"],
        language=row["language"] or "无中文",
        language_source=row["language_source"] or "none",
        sub_type=row["sub_type"],
        is_pack=bool(row["is_pack"]),
        is_extra=bool(row["is_extra"]),
        published_at=published,
        size_bytes=row["size_bytes"],
    )


def _row_to_schedule(row: sqlite3.Row) -> list[date]:
    """把 schedule 行还原成放送日期列表。"""
    return [date.fromisoformat(d) for d in json.loads(row["airdates"])]


def _quantile(values: list[float], q: float) -> float | None:
    """线性插值分位数；空列表返回 None。

    Args:
        values: 数值列表。
        q: 分位（0–1）。
    """
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _timed(fn, *args, **kwargs):
    """调用并返回 (结果, 耗时秒, 错误文本)。"""
    start = time.perf_counter()
    try:
        return fn(*args, **kwargs), time.perf_counter() - start, ""
    except Exception as exc:  # noqa: BLE001
        return None, time.perf_counter() - start, f"{type(exc).__name__}: {exc}"


def _runtime(from_year: int, to_year: int):
    """构建 (store, source, bgm, config)。"""
    ctx = build_context(GlobalOptions())
    return (
        Store(),
        build_source(ctx.config, ctx.env),
        build_bangumi_client(ctx.config, ctx.env),
        ctx.config,
    )


# ---- 阶段命令 ----


def stage_seasons(args: argparse.Namespace) -> None:
    """抓取窗口内所有季度的番剧列表。"""
    store, source, _bgm, _config = _runtime(args.from_year, args.to_year)
    for year, season in iter_seasons(args.from_year, args.to_year, date.today()):
        entries, elapsed, err = _timed(source.season, year, season)
        if entries is None:
            print(f"[seasons] {year}{season} 失败：{err}")
            continue
        store.set_season(year, season, entries)
        print(f"[seasons] {year}{season}: n={len(entries)} {elapsed:.1f}s")


def stage_anime(args: argparse.Namespace) -> None:
    """抓取番剧页，登记字幕组与参与关系。"""
    store, source, _bgm, _config = _runtime(args.from_year, args.to_year)
    ids = store.pending_anime(args.from_year, args.to_year, args.limit)
    print(f"[anime] 待抓 {len(ids)} 部")
    for index, bangumi_id in enumerate(ids, 1):
        detail, elapsed, err = _timed(source.bangumi, bangumi_id)
        if detail is None:
            print(f"[anime] {bangumi_id} 失败：{err}")
            continue
        season = _earliest_season(store, bangumi_id) or (
            season_of(detail.start_date) if detail.start_date else ""
        )
        store.save_anime(detail, season)
        if index % 20 == 0 or index == len(ids):
            print(
                f"[anime] {index}/{len(ids)}  最近 {bangumi_id} {detail.title[:24]} {elapsed:.1f}s"
            )


def _earliest_season(store: Store, bangumi_id: int) -> str | None:
    """取该番在季表中最早出现的季度标签。"""
    with store._connect() as conn:
        row = conn.execute(
            "SELECT season FROM season_anime WHERE bangumi_id = ? ORDER BY season LIMIT 1",
            (bangumi_id,),
        ).fetchone()
    return row["season"] if row else None


def stage_deep(args: argparse.Namespace) -> None:
    """对活跃组的季番作品全量深采发布。"""
    store, source, _bgm, _config = _runtime(args.from_year, args.to_year)
    store.refresh_flags(date(args.from_year, 1, 1))
    pairs = store.pending_deep(args.limit)
    print(f"[deep] 待深采 {len(pairs)} 个 (番剧, 字幕组)")
    for index, (bangumi_id, subgroup_id) in enumerate(pairs, 1):
        releases, elapsed, err = _timed(source.subgroup_releases, bangumi_id, subgroup_id)
        if releases is None:
            print(f"[deep] {bangumi_id}/{subgroup_id} 失败：{err}")
            continue
        store.save_deep(bangumi_id, subgroup_id, releases)
        if index % 20 == 0 or index == len(pairs):
            print(
                f"[deep] {index}/{len(pairs)}  {bangumi_id}/{subgroup_id} "
                f"n={len(releases)} {elapsed:.1f}s"
            )


def stage_schedule(args: argparse.Namespace) -> None:
    """对季番 × 深采作品取 Bangumi 精确日程。"""
    store, _source, bgm, _config = _runtime(args.from_year, args.to_year)
    ids = store.pending_schedule(args.limit)
    print(f"[schedule] 待取 {len(ids)} 部")
    for index, bangumi_id in enumerate(ids, 1):
        with store._connect() as conn:
            row = conn.execute(
                "SELECT bgm_subject_id FROM anime WHERE bangumi_id = ?", (bangumi_id,)
            ).fetchone()
        subject_id = row["bgm_subject_id"] if row else None
        if subject_id is None:
            continue
        subject, elapsed, err = _timed(bgm.subject, int(subject_id))
        if subject is None:
            print(f"[schedule] {bangumi_id} subject 失败：{err}")
            continue
        episodes, elapsed2, err = _timed(bgm.episodes, int(subject_id))
        if episodes is None:
            print(f"[schedule] {bangumi_id} episodes 失败：{err}")
            continue
        schedule = build_schedule(subject, episodes)
        store.save_schedule(
            bangumi_id,
            json.dumps([d.isoformat() for d in schedule.airdates]),
            schedule.total_episodes,
        )
        if index % 20 == 0 or index == len(ids):
            print(
                f"[schedule] {index}/{len(ids)}  {bangumi_id} "
                f"日程={len(schedule)} {elapsed + elapsed2:.1f}s"
            )


def stage_status(args: argparse.Namespace) -> None:
    """打印各阶段进度。"""
    store = Store()
    store.refresh_flags(date(args.from_year, 1, 1))
    for key, value in store.progress().items():
        print(f"{key:>16}: {value}")
    print(f"{'deep_pending':>16}: {len(store.pending_deep(None))}")
    print(f"{'schedule_pending':>16}: {len(store.pending_schedule(None))}")


# ---- 聚合 ----


def aggregate(args: argparse.Namespace) -> list[dict[str, object]]:
    """计算每个活跃字幕组的 D1–D4 指标。

    Args:
        args: 含 from_year / to_year 的参数。
    """
    store = Store()
    store.refresh_flags(date(args.from_year, 1, 1))
    data = store.load_for_aggregate()
    anime: dict[int, dict] = data["anime"]  # type: ignore[assignment]
    releases: dict[tuple[int, int], list[Release]] = data["releases"]  # type: ignore[assignment]
    schedules: dict[int, list[date]] = data["schedules"]  # type: ignore[assignment]
    policy = _load_policy()
    now = datetime.now()

    by_anime: dict[int, dict[int, list[Release]]] = defaultdict(lambda: defaultdict(list))
    for (bid, sid), rows in releases.items():
        by_anime[bid][sid] = rows

    seasonal_ids = {bid for bid, row in anime.items() if row.get("is_seasonal")}
    rows: list[dict[str, object]] = []
    for group in data["groups"]:  # type: ignore[union-attr]
        sid = int(group["subgroup_id"])
        work_ids = sorted({bid for (bid, g) in data["parts"] if g == sid and bid in seasonal_ids})  # type: ignore[union-attr]
        abs_delays: list[float] = []
        rel_delays: list[float] = []
        lang = Counter()
        lang_inferred = Counter()
        soft = hard = dual = total = explicit = 0
        seasons = Counter()
        records: list[WorkRecord] = []
        last_release: datetime | None = None
        for bid in work_ids:
            own = releases.get((bid, sid), [])
            if not own:
                continue
            season = str(anime[bid].get("season") or "")
            seasons[season] += 1
            all_groups = by_anime[bid]
            events_by_group = {g: episode_events(rs) for g, rs in all_groups.items() if rs}
            own_events = events_by_group.get(sid, {})
            fact_eps = [e.episode for ev in events_by_group.values() for e in ev.values()]
            fact_final = max(fact_eps) if fact_eps else None
            has_pack = any(r.is_pack for r in own)
            # 活跃时间用全部发布（含合集）衡量，避免纯批量组被误判停更
            stamps = [r.published_at for r in own if r.published_at]
            group_last = max(stamps) if stamps else None
            if group_last and (last_release is None or group_last > last_release):
                last_release = group_last
            fact_stamps = [
                r.published_at for rs in all_groups.values() for r in rs if r.published_at
            ]
            if not own_events and not (has_pack and fact_final):
                continue
            group_final = max((e.episode for e in own_events.values()), default=0)
            if has_pack and fact_final:
                group_final = max(group_final, fact_final)
            rel_map = relative_delays_hours(
                events_by_group, subtitle_baseline([r for rs in all_groups.values() for r in rs])
            ).get(sid, {})
            rel_delays.extend(rel_map.values())
            airdates = schedules.get(bid)
            # 只对覆盖了该番过半集数的组算绝对延迟，排除合集/单集噪声（如 7³ACG 的批量发布）
            if airdates and len(own_events) >= 0.5 * len(airdates):
                anchor = build_anchor(sid, list(own_events), _dummy_schedule(airdates))
                for episode, event in own_events.items():
                    if event.released_at == datetime.min:
                        continue
                    ordinal = anchor.ordinal_of.get(episode)
                    if ordinal is None or not (1 <= ordinal <= len(airdates)):
                        continue
                    nominal = datetime.combine(airdates[ordinal - 1], datetime.min.time())
                    delay = (event.released_at - nominal).total_seconds() / 3600
                    if -168 <= delay <= 1440:  # 排除异常值：早于 1 周或晚于 60 天
                        abs_delays.append(delay)
            records.append(
                WorkRecord(
                    bangumi_id=bid,
                    title=str(anime[bid].get("title") or ""),
                    season_date=season_date(season) if season else "",
                    group_final_episode=group_final or None,
                    fact_final_episode=fact_final,
                    group_last_release=group_last,
                    fact_last_release=max(fact_stamps) if fact_stamps else None,
                    rel_p50_h=median_or_none(list(rel_map.values())),
                )
            )
            for release in own:
                total += 1
                lang[release.language] += 1
                if release.has_explicit_chinese:
                    explicit += 1
                if release.language_source == "inferred":
                    lang_inferred[release.language] += 1
                if release.sub_type in ("内封", "外挂"):
                    soft += 1
                elif release.sub_type in ("内嵌", "内挂"):
                    hard += 1
                if release.is_dual_zh_jp:
                    dual += 1

        last_days = (now - last_release).total_seconds() / 86400 if last_release else None
        accepts_new = any(season_is_recent(season_date(s), now.date()) for s in seasons if s)
        assessment = assess_risk(
            records,
            policy,
            now,
            last_days,
            accepts_new,
            peer_gap_h=None,
            stall_lag_days=policy.stall_lag_days,
        )
        primary = lang.most_common(1)[0][0] if lang else ""
        is_raw, raw_reason = _raw_flag(explicit, str(group["name"]))
        rows.append(
            {
                "subgroup_id": sid,
                "name": group["name"],
                "publish_group_id": group["publish_group_id"],
                "is_raw": int(is_raw),
                "raw_reason": raw_reason,
                "works": len(work_ids),
                "seasons_active": len(seasons),
                "season_first": min(seasons) if seasons else "",
                "season_last": max(seasons) if seasons else "",
                "works_per_season": round(len(work_ids) / len(seasons), 2) if seasons else 0,
                "peak_season_count": max(seasons.values()) if seasons else 0,
                "season_counts": json.dumps(
                    {k: v for k, v in sorted(seasons.items()) if k}, ensure_ascii=False
                ),
                "abs_delay_p50_h": _round(_quantile(abs_delays, 0.5)),
                "abs_delay_p90_h": _round(_quantile(abs_delays, 0.9)),
                "abs_delay_n": len(abs_delays),
                "rel_delay_p50_h": _round(_quantile(rel_delays, 0.5)),
                "rel_delay_p90_h": _round(_quantile(rel_delays, 0.9)),
                "rel_delay_n": len(rel_delays),
                "lang_primary": primary,
                "lang_mix": "|".join(f"{k}:{v}" for k, v in lang.most_common()),
                "inferred_ratio": round(lang_inferred.get(primary, 0) / lang[primary], 3)
                if lang and primary
                else None,
                "simplified_pct": _pct(lang, total, {"简体", "简繁", "简日双语", "简繁日"}),
                "traditional_pct": _pct(lang, total, {"繁体"}),
                "dual_pct": round(dual * 100 / total, 1) if total else None,
                "softsub_pct": round(soft * 100 / (soft + hard), 1) if (soft + hard) else None,
                "drop_rate": _round(assessment.drop_rate),
                "risk": _round(assessment.risk),
                "risk_level": assessment.level,
                "tier": assessment.tier,
                "confidence": assessment.confidence,
                "last_release_days": _round(last_days),
                "accepts_new_season": accepts_new,
                "evidence": "；".join(assessment.evidence),
            }
        )
    rows.sort(key=lambda r: (-int(r["works"]), r["name"]))  # type: ignore[arg-type]
    return rows


def _dummy_schedule(airdates: list[date]):
    """构造仅含 airdates 的 Schedule，供 build_anchor 使用。"""
    from mikan_health.core.airtime import Schedule

    return Schedule(subject_id=0, airdates=airdates)


def _raw_flag(explicit: int, name: str) -> tuple[bool, str]:
    """判断是否为生肉/未标注组（无任何显式中文字幕），返回 (是否 raw, 原因)。

    Args:
        explicit: 该组显式中文字幕的发布条目数。
        name: 字幕组名称。
    """
    if explicit == 0:
        return True, "无显式中文字幕（生肉/未标注）"
    if any(marker in name for marker in ("生肉", "不明", "raw", "RAW")):
        return True, "名称标记为生肉/不明"
    return False, ""


def _round(value: float | None) -> float | None:
    """四舍五入到 2 位；None 原样返回。"""
    return None if value is None else round(value, 2)


def _pct(counts: Counter, total: int, labels: set[str]) -> float | None:
    """给定标签集合占已解析发布总数的百分比。"""
    if not total:
        return None
    matched = sum(v for k, v in counts.items() if k in labels)
    return round(matched * 100 / total, 1)


def _load_policy() -> Policy:
    """加载配置指定的 profile，缺失时回退 personal-normal。"""
    ctx = build_context(GlobalOptions())
    name = ctx.config.analysis.profile
    directory = Path(ctx.config.analysis.profiles_dir) if ctx.config.analysis.profiles_dir else None
    try:
        return load_profile(name, directory)
    except FileNotFoundError:
        return load_profile("personal-normal")


def report_dir(from_year: int, to_year: int) -> Path:
    """报告输出目录。"""
    return repo_root() / "reports" / "sitewide" / f"{from_year}-{to_year}"


def cmd_aggregate(args: argparse.Namespace) -> None:
    """聚合并把结果写入 groups.csv。"""
    rows = aggregate(args)
    target = report_dir(args.from_year, args.to_year)
    target.mkdir(parents=True, exist_ok=True)
    path = target / "groups.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=list(rows[0].keys()) if rows else [], lineterminator="\n"
        )
        if rows:
            writer.writeheader()
            writer.writerows(rows)
    print(f"[aggregate] {len(rows)} 个活跃组 -> {path}")


def cmd_report(args: argparse.Namespace) -> None:
    """由 groups.csv 生成 summary.md 与 PNG 图表。"""
    from sitewide_report import build_report

    rows = _read_csv(report_dir(args.from_year, args.to_year) / "groups.csv")
    build_report(rows, report_dir(args.from_year, args.to_year), args.from_year, args.to_year)


def _read_csv(path: Path) -> list[dict[str, str]]:
    """读取 groups.csv。"""
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def main(argv: list[str] | None = None) -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="全站字幕组评估")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func, with_limit: bool = True) -> None:
        p = sub.add_parser(name)
        p.add_argument("--from-year", type=int, default=2024)
        p.add_argument("--to-year", type=int, default=date.today().year)
        if with_limit:
            p.add_argument("--limit", type=int, default=None)
        p.set_defaults(func=func)

    add("seasons", stage_seasons, with_limit=False)
    add("anime", stage_anime)
    add("deep", stage_deep)
    add("schedule", stage_schedule)
    add("status", stage_status, with_limit=False)
    add("aggregate", cmd_aggregate, with_limit=False)
    add("report", cmd_report, with_limit=False)

    args = parser.parse_args(argv)
    force_pipe_utf8()
    args.func(args)


if __name__ == "__main__":
    main()
