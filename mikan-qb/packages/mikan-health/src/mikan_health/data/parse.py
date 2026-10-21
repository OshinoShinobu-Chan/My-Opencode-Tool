"""Mikan 页面解析：把 HTML/RSS 转成数据模型。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime

from bs4 import BeautifulSoup, Tag

from mikan_health.core.releases import ParsedTitle, parse_title
from mikan_health.core.taxonomy import Taxonomy
from mikan_health.data.models import (
    SOURCE_BANGUMI_PAGE,
    SOURCE_EXPAND,
    SOURCE_RSS,
    BangumiDetail,
    PublishGroupDetail,
    Release,
    SearchResult,
    SeasonEntry,
    Subgroup,
    WorkRef,
)

DATETIME_FORMATS = ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")
DATE_FORMATS = ("%m/%d/%Y", "%Y/%m/%d", "%Y-%m-%d")
SIZE_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}


def parse_bangumi_page(
    html: str, bangumi_id: int, taxonomy: Taxonomy | None = None
) -> BangumiDetail:
    """解析番剧主页，得到字幕组列表与全部发布。

    Args:
        html: 番剧主页 HTML。
        bangumi_id: 番剧 id。
        taxonomy: 标签表；None 用默认。
    """
    soup = BeautifulSoup(html, "lxml")
    tax = taxonomy or Taxonomy()

    title = _bangumi_title(soup)
    info = _bangumi_info(soup)
    rss_anchor = soup.select_one("p.bangumi-title a.mikan-rss")

    last_updates = _leftbar_last_updates(soup)
    leftbar_names = _leftbar_names(soup)
    subgroups: list[Subgroup] = []
    releases: list[Release] = []

    for block in soup.select("div.subgroup-text[id]"):
        subgroup_id = _as_int(block.get("id"))
        if subgroup_id is None:
            continue
        link = block.select_one('a[href^="/Home/PublishGroup/"]')
        rss = block.select_one("a.mikan-rss")
        name = (
            link.get_text(strip=True)
            if link
            else leftbar_names.get(subgroup_id) or f"subgroup-{subgroup_id}"
        )
        table = block.find_next_sibling("div", class_="episode-table")
        subgroup_releases = _parse_table(table, bangumi_id, subgroup_id, tax, SOURCE_BANGUMI_PAGE)
        releases.extend(subgroup_releases)
        subgroups.append(
            Subgroup(
                subtitle_group_id=subgroup_id,
                name=name,
                publish_group_id=_as_int(_href_id(link)),
                rss_url=_str_attr(rss, "href"),
                last_update=_max_published_at(subgroup_releases) or last_updates.get(subgroup_id),
                release_count=len(subgroup_releases),
            )
        )

    return BangumiDetail(
        bangumi_id=bangumi_id,
        title=title,
        weekday=info.get("weekday"),
        start_date=_parse_date(info.get("start_date")),
        official_site=info.get("official_site"),
        bangumi_tv_url=info.get("bangumi_tv_url"),
        rss_url=_str_attr(rss_anchor, "href") or f"/RSS/Bangumi?bangumiId={bangumi_id}",
        subgroups=subgroups,
        releases=releases,
    )


def parse_episode_table(
    html: str,
    bangumi_id: int,
    subgroup_id: int,
    taxonomy: Taxonomy | None = None,
    source: str = SOURCE_EXPAND,
) -> list[Release]:
    """解析展开的剧集表格（AJAX 片段）。

    Args:
        html: 展开表格的 HTML 片段。
        bangumi_id: 番剧 id。
        subgroup_id: 字幕组 id。
        taxonomy: 标签表；None 用默认。
        source: 数据来源标记。
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("div.episode-table") or soup.select_one("table")
    return _parse_table(table, bangumi_id, subgroup_id, taxonomy or Taxonomy(), source)


def parse_search(html: str) -> list[SearchResult]:
    """解析搜索结果页。

    Args:
        html: 搜索结果页 HTML。
    """
    soup = BeautifulSoup(html, "lxml")
    results: list[SearchResult] = []
    seen: set[int] = set()
    for li in soup.select("ul.an-ul li"):
        anchor = li.select_one('a[href^="/Home/Bangumi/"]')
        if anchor is None:
            continue
        bangumi_id = _as_int(_href_id(anchor))
        if bangumi_id is None or bangumi_id in seen:
            continue
        seen.add(bangumi_id)
        cover = li.select_one("span.b-lazy[data-src], img[data-src]")
        results.append(
            SearchResult(
                bangumi_id=bangumi_id,
                title=anchor.get_text(" ", strip=True),
                cover_url=_str_attr(cover, "data-src"),
            )
        )
    return results


def parse_publish_group(html: str, publish_group_id: int) -> PublishGroupDetail:
    """解析字幕组年表页，得到其作品列表。

    Args:
        html: 年表页 HTML。
        publish_group_id: 作品年表 id。
    """
    soup = BeautifulSoup(html, "lxml")
    name = ""
    title_node = soup.select_one("head > title")
    if title_node:
        name = re.sub(r"^Mikan Project\s*-\s*", "", title_node.get_text(strip=True))

    works: list[WorkRef] = []
    seen: set[tuple[int, int]] = set()
    for block in soup.select("div.pubgroup-timeline-item"):
        date_node = block.select_one("div.pubgroup-date")
        season_node = block.select_one("div.pubgroup-season")
        season_date = date_node.get_text(strip=True) if date_node else ""
        season_label = season_node.get_text(strip=True) if season_node else ""
        is_current = bool(season_node and "current-season" in _class_list(season_node))

        for li in block.select("ul.pubgroup-ul li[data-bangumiid]"):
            bangumi_id = _as_int(li.get("data-bangumiid"))
            subgroup_id = _as_int(li.get("data-subtitlegroupid"))
            if bangumi_id is None or subgroup_id is None:
                continue
            if (bangumi_id, subgroup_id) in seen:
                continue
            seen.add((bangumi_id, subgroup_id))
            anchor = li.select_one("a.an-text")
            works.append(
                WorkRef(
                    bangumi_id=bangumi_id,
                    subtitle_group_id=subgroup_id,
                    title=_str_attr(anchor, "title") or "",
                    season_date=season_date,
                    season_label=season_label,
                    is_current=is_current,
                )
            )

    return PublishGroupDetail(publish_group_id=publish_group_id, name=name, works=works)


def parse_season(html: str) -> list[SeasonEntry]:
    """解析按星期分组的季度番剧页。

    Args:
        html: 季度番剧页 HTML。
    """
    soup = BeautifulSoup(html, "lxml")
    entries: list[SeasonEntry] = []
    seen: set[int] = set()

    for block in soup.select("div.sk-bangumi"):
        weekday_raw = block.get("data-dayofweek")
        weekday = _as_int(weekday_raw)
        label_node = block.select_one("div.row")
        weekday_cn = label_node.get_text(strip=True) if label_node else None
        for anchor in block.select('a[href^="/Home/Bangumi/"]'):
            bangumi_id = _as_int(_href_id(anchor))
            if bangumi_id is None or bangumi_id in seen:
                continue
            seen.add(bangumi_id)
            info_group = anchor.find_parent("div", class_="an-info-group")
            date_node = info_group.select_one("div.date-text") if info_group else None
            entries.append(
                SeasonEntry(
                    bangumi_id=bangumi_id,
                    title=_str_attr(anchor, "title") or anchor.get_text(strip=True),
                    weekday=weekday,
                    weekday_cn=weekday_cn,
                    last_update=date_node.get_text(strip=True) if date_node else None,
                )
            )
    return entries


def _release_from(
    parsed: ParsedTitle,
    *,
    bangumi_id: int,
    subtitle_group_id: int,
    title: str,
    source: str,
    published_at: datetime | None = None,
    size_bytes: int | None = None,
    magnet: str | None = None,
    torrent_url: str | None = None,
    episode_url: str | None = None,
) -> Release:
    """把标题解析结果与来源元数据合成一条发布记录。

    Args:
        parsed: 标题解析结果。
        bangumi_id: 番剧 id。
        subtitle_group_id: 字幕组 id。
        title: 发布标题。
        source: 数据来源标记。
        published_at: 发布时间；未知为 None。
        size_bytes: 文件大小（字节）；未知为 None。
        magnet: 磁力链接；无则为 None。
        torrent_url: 种子链接；无则为 None。
        episode_url: 单集页面链接；无则为 None。
    """
    return Release(
        bangumi_id=bangumi_id,
        subtitle_group_id=subtitle_group_id,
        title=title,
        episode=parsed.episode,
        episode_raw=parsed.episode_raw,
        is_pack=parsed.is_pack,
        is_extra=parsed.is_extra,
        resolution=parsed.resolution,
        codec=parsed.codec,
        language=parsed.language,
        language_source=parsed.language_source,
        sub_type=parsed.sub_type,
        is_dual_zh_jp=parsed.is_dual_zh_jp,
        subgroup_label=parsed.subgroup_label,
        published_at=published_at,
        size_bytes=size_bytes,
        magnet=magnet,
        torrent_url=torrent_url,
        episode_url=episode_url,
        source=source,
    )


def parse_rss(
    xml_text: str,
    bangumi_id: int,
    subgroup_id: int,
    taxonomy: Taxonomy | None = None,
) -> list[Release]:
    """把 RSS（Torznab 风格）解析成发布列表。

    Args:
        xml_text: RSS XML 文本。
        bangumi_id: 番剧 id。
        subgroup_id: 字幕组 id。
        taxonomy: 标签表；None 用默认。
    """
    tax = taxonomy or Taxonomy()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    releases: list[Release] = []
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        title = _child_text(item, "title")
        if not title:
            continue

        parsed = parse_title(title, tax)
        published_at = None
        size_bytes = None
        torrent_url = None
        episode_url = None

        for child in item:
            local = _local(child.tag)
            if local == "link" and episode_url is None:
                episode_url = (child.text or "").strip() or None
            elif local == "enclosure":
                torrent_url = child.get("url") or torrent_url
                size_bytes = _as_int(child.get("length")) or size_bytes
            elif local == "torrent":
                for grand in child:
                    grand_local = _local(grand.tag)
                    if grand_local == "pubDate":
                        published_at = _parse_datetime(grand.text)
                    elif grand_local == "contentLength":
                        size_bytes = _as_int(grand.text) or size_bytes

        releases.append(
            _release_from(
                parsed,
                bangumi_id=bangumi_id,
                subtitle_group_id=subgroup_id,
                title=title,
                source=SOURCE_RSS,
                published_at=published_at,
                size_bytes=size_bytes,
                torrent_url=torrent_url,
                episode_url=episode_url,
            )
        )
    return releases


def _parse_table(
    table: Tag | None,
    bangumi_id: int,
    subgroup_id: int,
    taxonomy: Taxonomy,
    source: str,
) -> list[Release]:
    """解析剧集表格的每一行，生成发布记录。

    Args:
        table: 剧集表格节点；None 返回空列表。
        bangumi_id: 番剧 id。
        subgroup_id: 字幕组 id。
        taxonomy: 标签表。
        source: 数据来源标记。
    """
    if table is None:
        return []
    releases: list[Release] = []
    for row in table.select("tbody tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 4:
            continue
        checkbox = cells[0].select_one("input.js-episode-select")
        title_anchor = cells[1].select_one("a.magnet-link-wrap")
        title = title_anchor.get_text(strip=True) if title_anchor else ""
        if not title:
            continue
        parsed = parse_title(title, taxonomy)
        torrent_anchor = cells[4].select_one('a[href^="/Download/"]') if len(cells) > 4 else None
        releases.append(
            _release_from(
                parsed,
                bangumi_id=bangumi_id,
                subtitle_group_id=subgroup_id,
                title=title,
                source=source,
                published_at=_parse_datetime(cells[3].get_text(strip=True)),
                size_bytes=_parse_size(cells[2].get_text(strip=True)),
                magnet=_str_attr(checkbox, "data-magnet"),
                torrent_url=_str_attr(torrent_anchor, "href"),
                episode_url=_str_attr(title_anchor, "href"),
            )
        )
    return releases


def _bangumi_title(soup: BeautifulSoup) -> str:
    """提取番剧标题，去掉其中的链接文本。

    Args:
        soup: 已解析的页面。
    """
    node = soup.select_one("p.bangumi-title")
    if node is None:
        return ""
    for anchor in node.select("a"):
        anchor.decompose()
    return node.get_text(" ", strip=True)


def _bangumi_info(soup: BeautifulSoup) -> dict[str, str | None]:
    """解析信息栏，抽取放送日期/开始日期/官网等字段。

    Args:
        soup: 已解析的页面。
    """
    info: dict[str, str | None] = {}
    for node in soup.select("p.bangumi-info"):
        text = node.get_text(" ", strip=True)
        if text.startswith("放送日期"):
            info["weekday"] = text.split("：", 1)[-1].strip()
        elif text.startswith("放送开始"):
            info["start_date"] = text.split("：", 1)[-1].strip()
        elif text.startswith("官方网站"):
            link = node.select_one("a")
            info["official_site"] = (_str_attr(link, "href") or text.split("：", 1)[-1]).strip()
        elif "Bangumi" in text and "链接" in text:
            link = node.select_one("a")
            info["bangumi_tv_url"] = (_str_attr(link, "href") or "").strip() or None
    return info


def _leftbar_last_updates(soup: BeautifulSoup) -> dict[int, datetime | None]:
    """从左侧栏读取各字幕组的最近更新时间。

    Args:
        soup: 已解析的页面。
    """
    updates: dict[int, datetime | None] = {}
    for li in soup.select("li.leftbar-item"):
        anchor = li.select_one("a.subgroup-name")
        if anchor is None:
            continue
        classes = _class_list(anchor)
        subgroup_id = None
        for cls in classes:
            if cls.startswith("subgroup-"):
                subgroup_id = _as_int(cls.split("-", 1)[1])
        if subgroup_id is None:
            continue
        date_node = li.select_one("span.date")
        updates[subgroup_id] = _parse_date_time(
            date_node.get_text(strip=True) if date_node else None
        )
    return updates


def _leftbar_names(soup: BeautifulSoup) -> dict[int, str]:
    """从左侧栏读取 subgroup_id → 名称 映射。

    Args:
        soup: 已解析的页面。
    """
    names: dict[int, str] = {}
    for li in soup.select("li.leftbar-item"):
        anchor = li.select_one("a.subgroup-name")
        if anchor is None:
            continue
        for cls in _class_list(anchor):
            if cls.startswith("subgroup-"):
                subgroup_id = _as_int(cls.split("-", 1)[1])
                if subgroup_id is not None:
                    names[subgroup_id] = anchor.get_text(strip=True)
    return names


def _max_published_at(releases: list[Release]) -> datetime | None:
    """取列表中最新发布时间；无有效时间返回 None。

    Args:
        releases: 发布记录列表。
    """
    stamps = [r.published_at for r in releases if r.published_at is not None]
    return max(stamps) if stamps else None


def _str_attr(node: Tag | None, name: str) -> str | None:
    """读取 HTML 属性的字符串值；多值/缺失一律返回 None。

    Args:
        node: 目标节点；None 返回 None。
        name: 属性名。
    """
    if node is None:
        return None
    value = node.get(name)
    return value if isinstance(value, str) else None


def _class_list(node: Tag) -> list[str]:
    """读取节点的 class 属性并统一为字符串列表。

    Args:
        node: 目标节点。
    """
    value = node.get("class")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, str):
        return [value]
    return []


def _href_id(node: Tag | None) -> str | None:
    """取 href 末段作为 id 文本；缺失返回 None。

    Args:
        node: 含 href 的节点；None 返回 None。
    """
    href = _str_attr(node, "href") or ""
    return href.rstrip("/").rsplit("/", 1)[-1] or None


def _local(tag: str) -> str:
    """去掉 XML 标签的命名空间前缀。

    Args:
        tag: 原始标签名（可能形如 {ns}name）。
    """
    return tag.rsplit("}", 1)[-1]


def _child_text(node: ET.Element, name: str) -> str | None:
    """按标签名取子元素的文本；无匹配返回 None。

    Args:
        node: 父元素。
        name: 目标子标签名（忽略命名空间）。
    """
    for child in node:
        if _local(child.tag) == name and child.text:
            return child.text.strip()
    return None


def _parse_datetime(value: str | None) -> datetime | None:
    """按多种格式解析日期时间字符串；失败返回 None。

    Args:
        value: 待解析文本；None/空返回 None。
    """
    if not value:
        return None
    text = value.strip()
    for fmt in DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_date_time(value: str | None) -> datetime | None:
    """解析日期时间，缺失时间时回落为当日零点；失败返回 None。

    Args:
        value: 待解析文本；None/空返回 None。
    """
    parsed = _parse_datetime(value)
    if parsed is not None:
        return parsed
    parsed_date = _parse_date(value)
    return datetime(parsed_date.year, parsed_date.month, parsed_date.day) if parsed_date else None


def _parse_date(value: str | None) -> date | None:
    """按多种格式解析日期；失败返回 None。

    Args:
        value: 待解析文本；None/空返回 None。
    """
    if not value:
        return None
    text = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_size(value: str | None) -> int | None:
    """把 '1.2 GB' 之类的大小文本换算成字节数。

    Args:
        value: 大小文本；None/空返回 None。
    """
    if not value:
        return None
    match = re.match(r"(?i)^([\d.]+)\s*([KMGTP]?B)$", value.strip())
    if match is None:
        return _as_int(value.strip())
    number, unit = match.group(1), match.group(2).upper()
    return int(float(number) * SIZE_UNITS[unit])


def _as_int(value: object) -> int | None:
    """尽力把值转成整数；不可转返回 None。

    Args:
        value: 待转换值；None 返回 None。
    """
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
