#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bangumi (bgm.tv) helper CLI.

Pure standard library (urllib) so it runs on a bare Python install.
API reference: https://bangumi.github.io/api/  (base: https://api.bgm.tv/v0)

Commands
  whoami                      show the account bound to the access token
  search <keyword>            search subjects (anime/book/game/music/real)
  info <subject_id>           subject detail
  episodes <subject_id>       episode list (with your watch status when token set)
  mark <subject_id> --status wish|doing|done|onhold|dropped [--rate N]
  rate <subject_id> <score>   set / clear the score (0 clears)
  ep-watch <subject_id> <ep>  mark episode #ep of a subject as watched
  ep-watch-id <episode_id>    mark an episode as watched by episode id
  airing [--deep]             check which of my wish/doing anime are on air
  calendar                    currently airing anime (per weekday)
  comments <episode_id>       episode comments / 吐槽 (newest or oldest first)
  topics <subject_id>         discussion board topics of a subject
"""

import argparse
import gzip
import html as html_mod
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.bgm.tv/v0"
SITE = "https://bgm.tv"
UA = "opencode-bangumi/1.0 (https://github.com/anomalyco/opencode)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
SELF = os.path.abspath(__file__)

STATUS_CN = {1: "想看", 2: "看过", 3: "在看", 4: "搁置", 5: "抛弃"}
STATUS_KEY = {"wish": 1, "done": 2, "collect": 2, "doing": 3, "onhold": 4,
              "on_hold": 4, "hold": 4, "dropped": 5, "drop": 5,
              "想看": 1, "看过": 2, "在看": 3, "搁置": 4, "抛弃": 5}
EP_STATUS_CN = {0: "未收藏", 1: "想看", 2: "看过", 3: "抛弃"}
SUBJECT_TYPE_CN = {1: "书籍", 2: "动画", 3: "音乐", 4: "游戏", 6: "三次元"}
WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class BgmError(Exception):
    pass


# ---------------------------------------------------------------- output ----

def out(text=""):
    sys.stdout.write(text + "\n")


def jout(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


# ---------------------------------------------------------------- network ---

def _registry_proxy():
    if os.name != "nt":
        return None
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        try:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        finally:
            winreg.CloseKey(key)
        if not enabled or not server:
            return None
        if "=" in server:
            parts = {}
            for chunk in server.split(";"):
                if "=" in chunk:
                    k, v = chunk.split("=", 1)
                    parts[k.strip().lower()] = v.strip()
            server = parts.get("https") or parts.get("http")
        if not server:
            return None
        if "://" not in server:
            server = "http://" + server
        return server
    except Exception:
        return None


def build_opener():
    proxy = os.environ.get("BGM_PROXY")
    if not proxy:
        for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy",
                    "HTTP_PROXY", "http_proxy"):
            if os.environ.get(var):
                proxy = os.environ[var]
                break
    if not proxy:
        proxy = _registry_proxy()
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    else:
        handler = urllib.request.ProxyHandler({})
    return urllib.request.build_opener(handler), proxy


OPENER = build_opener()[0]


def http(method, url, params=None, body=None, token=None, ua=None,
         timeout=45, retries=3, want_json=True):
    """Return (status, parsed-or-text). Raises BgmError on network failure."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params, doseq=True)
    data = None
    headers = {"User-Agent": ua or UA, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token

    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers,
                                         method=method)
            with OPENER.open(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                if want_json and raw:
                    try:
                        return resp.status, json.loads(raw.decode("utf-8"))
                    except ValueError:
                        return resp.status, raw.decode("utf-8", "replace")
                return resp.status, raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            if exc.headers and exc.headers.get("Content-Encoding") == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            text = raw.decode("utf-8", "replace") if raw else ""
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                last_err = BgmError("HTTP %s" % exc.code)
                time.sleep(2 * (attempt + 1))
                continue
            return exc.code, text
        except (urllib.error.URLError, OSError) as exc:
            last_err = exc
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
    raise BgmError("网络请求失败: %s\n提示: 本机需要代理才能访问 bgm.tv/api.bgm.tv，"
                   "请设置 BGM_PROXY=http://127.0.0.1:7890" % last_err)


def api(method, path, params=None, body=None, token=None, expect=None):
    status, data = http(method, API + path, params=params, body=body,
                        token=token)
    ok = (200 <= status < 300) if expect is None else (status in expect)
    if not ok:
        detail = data
        if isinstance(data, dict):
            detail = data.get("description") or data.get("title") or data
        raise BgmError("API %s %s -> HTTP %s\n%s\n%s" % (
            method, path, status, detail, _api_hint(status)))
    return data


def _api_hint(status):
    if status == 401:
        return ("提示: 该操作需要 access token（scope: write:collection）。\n"
                "      见 SKILL.md「配置 access token」一节。")
    if status == 403:
        return "提示: token 缺少对应 scope，或请求被拒绝。"
    if status == 404:
        return "提示: 条目/章节不存在，或该条目未收藏。"
    return ""


# ------------------------------------------------------------------ token ---

def token_file_candidates():
    return [
        os.path.join(SKILL_ROOT, "token.txt"),
        os.path.join(SKILL_ROOT, ".token"),
        os.path.join(os.path.expanduser("~"), ".config", "bangumi", "token"),
        os.path.join(os.environ.get("APPDATA", ""), "bangumi", "token"),
    ]


def load_token(cli_token=None):
    if cli_token:
        return cli_token.strip()
    for var in ("BANGUMI_TOKEN", "BGM_TOKEN"):
        if os.environ.get(var):
            return os.environ[var].strip()
    for path in token_file_candidates():
        if path and os.path.isfile(path):
            try:
                with io.open(path, "r", encoding="utf-8-sig") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            line = line.split("=", 1)[1].strip()
                        if line.lower().startswith("bearer "):
                            line = line[7:].strip()
                        return line
            except OSError:
                continue
    return None


def require_token(args):
    token = load_token(getattr(args, "token", None))
    if not token:
        raise BgmError(
            "缺少 access token。\n"
            "  1) 打开 https://next.bgm.tv/demo/access-token 生成个人令牌\n"
            "  2) 写入 %s （只放一行 token）\n"
            "     或设置环境变量 BANGUMI_TOKEN\n"
            % os.path.join(SKILL_ROOT, "token.txt"))
    return token


_ME_CACHE = {}


def my_username(token):
    if token not in _ME_CACHE:
        me = api("GET", "/me", token=token)
        _ME_CACHE[token] = me.get("username") or me.get("id")
    return _ME_CACHE[token]


def resolve_subject_id(target, subject_type=None):
    """Accept a numeric subject id or a title keyword."""
    if target is None:
        raise BgmError("需要一个条目 id 或标题")
    text = str(target).strip()
    if not text:
        raise BgmError("需要一个条目 id 或标题")
    if re.fullmatch(r"\d+", text):
        return int(text)
    return pick_subject(text, subject_type=subject_type)


def search_subjects(keyword, subject_type=2, limit=10):
    body = {"keyword": keyword}
    if subject_type:
        body["filter"] = {"type": [subject_type]}
    status, data = http("POST", API + "/search/subjects",
                        params={"limit": limit}, body=body)
    if status != 200 or not isinstance(data, dict):
        raise BgmError("搜索失败: HTTP %s %s" % (status, data))
    return data


def pick_subject(keyword, subject_type=None):
    data = search_subjects(keyword, subject_type=subject_type, limit=10)
    items = data.get("data") or []
    if not items:
        raise BgmError("没有搜到与 %r 匹配的条目" % keyword)
    exact = [i for i in items
             if (i.get("name_cn") or "").strip() == keyword.strip()
             or (i.get("name") or "").strip() == keyword.strip()]
    if len(exact) == 1:
        return exact[0]["id"]
    if len(items) == 1:
        return items[0]["id"]
    out("关键词 %r 匹配到多个条目，请把要用的 id 作为第一个参数重新执行：" % keyword)
    print_subjects(items)
    raise BgmError("条目不唯一，未执行任何写操作")


def print_subjects(items):
    for it in items:
        name_cn = it.get("name_cn") or it.get("name") or ""
        name = it.get("name") or ""
        date = it.get("date") or "?"
        eps = it.get("eps")
        try:
            score = (it.get("rating") or {}).get("score") or 0
        except Exception:
            score = 0
        extra = " / %s" % name if name and name != name_cn else ""
        out("  %-8s %s%s" % (it.get("id"), name_cn, extra))
        out("           %s  eps=%s  评分=%s" % (date, eps, score))


# --------------------------------------------------------------- rendering --

def render_message(raw):
    s = raw
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r'<img[^>]*?alt="([^"]*)"[^>]*>', r"\1", s, flags=re.I)
    s = re.sub(r"<img[^>]*>", "", s, flags=re.I)
    s = re.sub(r"<a[^>]*>(.*?)</a>", r"\1", s, flags=re.I | re.S)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_mod.unescape(s)
    lines = [ln.strip() for ln in s.split("\n")]
    cleaned = []
    for ln in lines:
        if ln == "" and (not cleaned or cleaned[-1] == ""):
            continue
        cleaned.append(ln)
    return "\n".join(cleaned).strip()


POST_RE = re.compile(
    r'<div id="post_(\d+)" class="([^"]*)"\s+name="(floor-[\d\-]+)"')
FLOOR_TIME_RE = re.compile(r'class="floor-anchor">#([\d\-]+)</a>\s*-\s*([^<]+)</small>')
TOP_USER_RE = re.compile(r'class="l post_author_\d+">(.*?)</a>', re.S)
SUB_USER_RE = re.compile(r'<strong><a id="\d+"[^>]*class="l">(.*?)</a>', re.S)
TOP_MSG_RE = re.compile(r'class="message clearit">\s*(.*?)\s*</div>', re.S)
SUB_MSG_RE = re.compile(r'class="cmt_sub_content">(.*?)</div>', re.S)
TOMBSTONE_RE = re.compile(r'class="tip_collapsed"')
TOMBSTONE_TEXT = "删除了回复"


def _is_deleted(block, content):
    return bool(TOMBSTONE_RE.search(block)) or content.strip() == TOMBSTONE_TEXT


def _drop_deleted(comments):
    """Remove deleted posts; replies under a deleted floor are re-surfaced.

    Promotion is safe because every reply keeps its own floor number
    (e.g. #10-1), so it stays self-describing on its own.
    """
    kept = []
    for c in comments:
        replies = _drop_deleted(c.get("replies") or [])
        if c.pop("_deleted", False):
            kept.extend(replies)
        else:
            c["replies"] = replies
            kept.append(c)
    return kept


def parse_episode_comments(page_html):
    start = page_html.find('id="comment_list"')
    if start < 0:
        return []
    end = page_html.find('<template id="likes_reaction_grid_item"', start)
    region = page_html[start:end if end > 0 else len(page_html)]

    marks = list(POST_RE.finditer(region))
    comments = []
    for i, m in enumerate(marks):
        blk_end = marks[i + 1].start() if i + 1 < len(marks) else len(region)
        block = region[m.start():blk_end]
        cls = m.group(2)
        floor = m.group(3).replace("floor-", "")
        is_sub = "sub_reply" in cls
        tm = FLOOR_TIME_RE.search(block)
        tm_text = tm.group(2).strip() if tm else ""
        if is_sub:
            um = SUB_USER_RE.search(block)
            msg = SUB_MSG_RE.search(block)
        else:
            um = TOP_USER_RE.search(block)
            msg = TOP_MSG_RE.search(block)
        user = render_message(um.group(1)) if um else "?"
        content = render_message(msg.group(1)) if msg else "(内容无法解析)"
        rec = {
            "floor": floor,
            "time": tm_text,
            "user": user,
            "content": content,
            "replies": [],
            "_deleted": _is_deleted(block, content),
        }
        if is_sub and comments:
            comments[-1]["replies"].append(rec)
        else:
            comments.append(rec)
    return _drop_deleted(comments)


def slice_comments(comments, limit=50, sort="desc"):
    if sort == "asc":
        return comments[:limit]
    return list(reversed(comments))[:limit]


def print_comments(comments, episode=None, subject=None, limit=50, sort="desc"):
    total = len(comments)
    reply_total = sum(len(c.get("replies") or []) for c in comments)
    shown = slice_comments(comments, limit, sort)
    head = []
    if subject:
        head.append(subject)
    if episode is not None:
        head.append("ep.%s" % episode)
    if head:
        out("### " + " / ".join(head))
    scope = "前 %d 条" % len(shown) if sort == "asc" else "最近 %d 条" % len(shown)
    order = "时间正序" if sort == "asc" else "时间倒序"
    cnt = "共 %d 条" % total
    if reply_total:
        cnt += "（另含 %d 条楼中楼）" % reply_total
    out("%s，按%s显示%s" % (cnt, order, scope))
    out("")
    for c in shown:
        meta = c["user"] + (" · " + c["time"] if c["time"] else "")
        out("#%s  %s" % (c["floor"], meta))
        for line in (c["content"] or "").split("\n"):
            out("    " + line if line else "")
        for r in c["replies"]:
            rmeta = r["user"] + (" · " + r["time"] if r["time"] else "")
            out("    ↳ #%s %s" % (r["floor"], rmeta))
            for line in (r["content"] or "").split("\n"):
                out("        " + line if line else "")
        out("")


# -------------------------------------------------------------- commands ----

def cmd_whoami(args):
    token = require_token(args)
    me = api("GET", "/me", token=token)
    if args.json:
        jout(me)
        return
    out("用户: %s (%s)" % (me.get("nickname"), me.get("username")))
    out("ID: %s  注册: %s" % (me.get("id"), me.get("reg_time")))
    out("token 有效，可用于写操作。")


def cmd_search(args):
    data = search_subjects(args.keyword, subject_type=args.type, limit=args.limit)
    if args.json:
        jout(data)
        return
    out("找到 %s 条（显示 %s 条）：" % (data.get("total"), len(data.get("data") or [])))
    print_subjects(data.get("data") or [])


def cmd_info(args):
    subject_id = resolve_subject_id(args.target)
    d = api("GET", "/subjects/%d" % subject_id)
    if args.json:
        jout(d)
        return
    out("#%s %s" % (d.get("id"), d.get("name_cn") or d.get("name")))
    if d.get("name") and d.get("name") != d.get("name_cn"):
        out("原名: %s" % d.get("name"))
    rating = d.get("rating") or {}
    coll = d.get("collection") or {}
    out("类型: %s  放送日: %s  章节数: %s  平台: %s" % (
        SUBJECT_TYPE_CN.get(d.get("type"), d.get("type")),
        d.get("date") or "?", d.get("eps"), d.get("platform") or "-"))
    out("评分: %s (%s 人评分, rank %s)" % (
        rating.get("score"), rating.get("total"), rating.get("rank")))
    out("收藏: 想看 %s / 在看 %s / 看过 %s" % (
        coll.get("wish"), coll.get("doing"), coll.get("collect")))
    url = "%s/subject/%s" % (SITE, d.get("id"))
    out("链接: %s" % url)
    if d.get("summary"):
        out("")
        out(d["summary"].strip())


def fetch_episodes(subject_id, ep_type=0, limit=100):
    eps = []
    offset = 0
    while True:
        params = {"subject_id": subject_id, "limit": limit, "offset": offset}
        if ep_type is not None:
            params["type"] = ep_type
        d = api("GET", "/episodes", params=params)
        batch = d.get("data") or []
        eps.extend(batch)
        if len(batch) < limit or offset > 2000:
            break
        offset += limit
    return eps


def fetch_my_episode_status(subject_id, token):
    try:
        d = api("GET", "/users/-/collections/%d/episodes" % subject_id,
                params={"episode_type": 0, "limit": 1000}, token=token)
    except BgmError:
        return {}
    return {str(item["episode"]["id"]): item.get("type", 0)
            for item in (d.get("data") or [])}


def cmd_episodes(args):
    subject_id = resolve_subject_id(args.target)
    eps = fetch_episodes(subject_id, ep_type=(None if args.all else args.ep_type))
    token = load_token(getattr(args, "token", None))
    status = fetch_my_episode_status(subject_id, token) if token else {}
    if args.json:
        jout({"subject_id": subject_id, "episodes": eps, "my_status": status})
        return
    if not eps:
        out("该条目没有可列出的章节。")
        return
    out("章节列表 (subject_id=%d, 共 %d 条)：%s" % (
        subject_id, len(eps),
        "含你的观看状态" if status else "要显示观看状态请配置 token"))
    out("")
    for e in eps:
        st = status.get(str(e["id"]))
        tag = "" if st is None else "  [%s]" % EP_STATUS_CN.get(st, st)
        out("  id=%-8s ep=%-4s %-10s %s%s" % (
            e["id"], e.get("ep"), e.get("airdate") or "?",
            e.get("name_cn") or e.get("name") or "", tag))
    out("")
    out('标记看过: python "%s" ep-watch %d <集数>' % (SELF, subject_id))


def _apply_subject_collection(subject_id, body, token, use_patch=False):
    method = "PATCH" if use_patch else "POST"
    api(method, "/users/-/collections/%d" % subject_id, body=body, token=token)


def cmd_mark(args):
    token = require_token(args)
    subject_id = resolve_subject_id(args.target, subject_type=2)
    try:
        status = STATUS_KEY[str(args.status).lower()]
    except KeyError:
        raise BgmError("未知状态 %r，可用: 想看/看过/在看/搁置/抛弃 或 wish/done/doing/onhold/dropped"
                       % args.status)
    body = {"type": status}
    if args.rate is not None:
        body["rate"] = args.rate
    if args.comment is not None:
        body["comment"] = args.comment
    if args.private:
        body["private"] = True
    _apply_subject_collection(subject_id, body, token)
    out("已把条目 %d 标记为「%s」%s" % (
        subject_id, STATUS_CN.get(status, status),
        ("，评分 %s" % args.rate) if args.rate is not None else ""))
    if status == 2 and args.rate is None:
        out("尚未评分。请询问用户给几分（1-10，0 = 不评分），然后执行：")
        out('  python "%s" rate %d <分数>' % (SELF, subject_id))


def cmd_rate(args):
    token = require_token(args)
    subject_id = args.subject_id
    score = args.score
    if not (0 <= score <= 10):
        raise BgmError("评分范围是 0-10（0 表示删除评分）")
    _apply_subject_collection(subject_id, {"rate": score}, token, use_patch=True)
    if score == 0:
        out("已清除条目 %d 的评分。" % subject_id)
    else:
        out("已给条目 %d 打分 %d。" % (subject_id, score))


def cmd_ep_watch(args):
    token = require_token(args)
    subject_id = args.subject_id
    eps = fetch_episodes(subject_id, ep_type=0)
    want = str(args.ep)
    target = None
    for e in eps:
        if str(e.get("ep")) == want or str(e.get("sort")) == want:
            target = e
            break
    if target is None:
        raise BgmError("在条目 %d 中找不到第 %s 集（可用 ep 编号: %s）" % (
            subject_id, want, ", ".join(str(e.get("ep")) for e in eps[:60])))
    mark_episode(token, target["id"], args.ep_status)
    out("已将条目 %d 的第 %s 集（episode_id=%s，%s）标记为「%s」" % (
        subject_id, target.get("ep"), target["id"],
        target.get("name_cn") or target.get("name") or "",
        EP_STATUS_CN.get(args.ep_status, args.ep_status)))


def cmd_ep_watch_id(args):
    token = require_token(args)
    mark_episode(token, args.episode_id, args.ep_status)
    out("已将 episode %d 标记为「%s」" % (
        args.episode_id, EP_STATUS_CN.get(args.ep_status, args.ep_status)))


def mark_episode(token, episode_id, ep_status):
    status, data = http("PUT", "%s/users/-/collections/-/episodes/%d" % (API, episode_id),
                        body={"type": ep_status}, token=token)
    if 200 <= status < 300:
        return
    detail = data
    if isinstance(data, dict):
        detail = data.get("description") or data.get("title") or data
    if status == 400 and "collect" in str(detail).lower():
        raise BgmError("该条目还未收藏，无法标记单集。请先执行：\n"
                       '  python "%s" mark <条目id> --status doing' % SELF)
    raise BgmError("标记章节失败 HTTP %s: %s\n%s" % (status, detail, _api_hint(status)))


def cmd_calendar(args):
    d = http("GET", "https://api.bgm.tv/calendar")[1]
    if args.json:
        jout(d)
        return
    for day in d:
        weekday = day.get("weekday", {}).get("cn") or day.get("weekday", {}).get("id")
        out("== %s ==" % weekday)
        for s in day.get("items") or []:
            out("  %-8s %s" % (s.get("id"), s.get("name_cn") or s.get("name")))
        out("")


def cmd_topics(args):
    subject_id = resolve_subject_id(args.target)
    status, data = http("GET", "https://api.bgm.tv/subject/%d" % subject_id,
                        params={"responseGroup": "large"})
    if status != 200 or not isinstance(data, dict):
        raise BgmError("获取讨论版失败 HTTP %s" % status)
    topics = data.get("topic") or []
    if args.json:
        jout(topics)
        return
    if not topics:
        out("该条目没有讨论版主题。")
        return
    out("条目 %d 的讨论版主题（%d 条）：" % (subject_id, len(topics)))
    for t in topics:
        out("  %-8s 回复 %-5s %s" % (t.get("id"), t.get("replies"), t.get("title")))
        out("           https://bgm.tv/subject/topic/%s" % t.get("id"))


def cmd_comments(args):
    episode_id = args.episode_id
    subject_label = args.label or None
    ep_no = args.ep
    if episode_id is None:
        if not args.subject:
            raise BgmError("用法: comments <episode_id>  或  comments --subject <条目id> --ep <集数>")
        eps = fetch_episodes(args.subject, ep_type=0)
        want = str(ep_no)
        for e in eps:
            if str(e.get("ep")) == want or str(e.get("sort")) == want:
                episode_id = e["id"]
                break
        if episode_id is None:
            raise BgmError("在条目 %s 中找不到第 %s 集" % (args.subject, want))
    if ep_no is None or subject_label is None:
        try:
            detail = api("GET", "/episodes/%d" % episode_id)
            if ep_no is None:
                ep_no = detail.get("ep") or detail.get("sort")
            if subject_label is None and detail.get("subject_id"):
                subj = api("GET", "/subjects/%d" % detail["subject_id"])
                subject_label = subj.get("name_cn") or subj.get("name")
        except BgmError:
            pass
    status, page = http("GET", "%s/ep/%d" % (SITE, episode_id), ua=BROWSER_UA)
    if status != 200:
        raise BgmError("抓取 bgm.tv 剧集页失败 HTTP %s" % status)
    comments = parse_episode_comments(page)
    if args.json:
        shown = slice_comments(comments, args.limit, args.sort)
        jout({"episode_id": episode_id, "subject": subject_label,
              "ep": ep_no, "total": len(comments),
              "replies": sum(len(c.get("replies") or []) for c in comments),
              "count": len(shown), "sort": args.sort, "comments": shown})
        return
    if not comments:
        out("这一集还没有吐槽/讨论。（episode_id=%s）" % episode_id)
        out("链接: %s/ep/%s" % (SITE, episode_id))
        return
    print_comments(comments, episode=ep_no if ep_no is not None else "?",
                   subject=subject_label, limit=args.limit, sort=args.sort)
    out("链接: %s/ep/%s" % (SITE, episode_id))


def cmd_airing(args):
    token = load_token(getattr(args, "token", None))
    username = args.username
    if not username:
        if not token:
            raise BgmError("需要 --username，或配置 token 后自动使用自己的账号")
        username = my_username(token)
    wanted = [STATUS_KEY[s] for s in args.status.split(",") if s in STATUS_KEY]
    if not wanted:
        wanted = [1, 3]

    items = []
    for st in wanted:
        offset = 0
        while True:
            d = api("GET", "/users/%s/collections" % urllib.parse.quote(str(username)),
                    params={"subject_type": 2, "type": st, "limit": 50,
                            "offset": offset},
                    token=token)
            batch = d.get("data") or []
            if not batch:
                break
            for it in batch:
                it["_collection_type"] = st
            items.extend(batch)
            offset += len(batch)
            if offset >= d.get("total", 0):
                break

    if not items:
        out("没有想看/在看的动画。")
        return

    calendar_map = {}
    try:
        cal = http("GET", "https://api.bgm.tv/calendar")[1]
        for i, day in enumerate(cal):
            wd = day.get("weekday", {})
            label = wd.get("cn") or WEEKDAY_CN[i % 7]
            for s in day.get("items") or []:
                calendar_map[s["id"]] = label
    except BgmError:
        pass

    today = time.strftime("%Y-%m-%d")
    results = []
    for it in items:
        sid = it["subject_id"]
        subj = it.get("subject") or {}
        title = subj.get("name_cn") or subj.get("name") or str(sid)
        row = {
            "subject_id": sid,
            "title": title,
            "collection": STATUS_CN.get(it["_collection_type"]),
            "ep_status": it.get("ep_status"),
            "eps": subj.get("eps"),
            "date": subj.get("date"),
        }
        if sid in calendar_map:
            row.update({"airing": True, "source": "calendar",
                        "weekday": calendar_map[sid], "today": calendar_map[sid] == WEEKDAY_CN[time.localtime().tm_wday]})
        elif args.deep:
            eps = fetch_episodes(sid, ep_type=0)
            dated = [e for e in eps if e.get("airdate")]
            future = [e for e in dated if e["airdate"] > today]
            aired = [e for e in dated if e["airdate"] <= today]
            row["ep_total"] = len(eps)
            if future:
                nxt = sorted(future, key=lambda e: e["airdate"])[0]
                row.update({"airing": True, "source": "episodes",
                            "next_ep": nxt.get("ep"), "next_airdate": nxt["airdate"]})
            elif aired:
                last = sorted(aired, key=lambda e: e["airdate"])[-1]
                row.update({"airing": False, "source": "episodes",
                            "last_ep": last.get("ep"), "last_airdate": last["airdate"]})
            else:
                row.update({"airing": None, "source": "episodes"})
            if row.get("airing") is False and subj.get("eps") and len(eps) < subj["eps"]:
                row["airing"] = None
            time.sleep(0.4)
        else:
            row.update(estimate_airing(subj))
        results.append(row)

    if args.json:
        jout(results)
        return
    airing = [r for r in results if r.get("airing") is True]
    maybe = [r for r in results if r.get("airing") is None]
    done = [r for r in results if r.get("airing") is False]

    def line(r):
        bits = ["%-8s %s" % (r["subject_id"], r["title"])]
        bits.append("[%s] 进度 %s/%s" % (r["collection"], r.get("ep_status", "?"), r.get("eps") or "?"))
        if r.get("source") == "calendar":
            bits.append("放送: %s%s" % (r["weekday"], "（今天）" if r.get("today") else ""))
        elif r.get("next_airdate"):
            bits.append("下一集 ep.%s @ %s" % (r.get("next_ep"), r["next_airdate"]))
        elif r.get("last_airdate"):
            bits.append("最后放送 ep.%s @ %s" % (r.get("last_ep"), r["last_airdate"]))
        elif r.get("date"):
            bits.append("开播 %s" % r["date"])
        return "  " + "  ".join(bits)

    out("检查账号: %s   今天: %s" % (username, today))
    out("")
    if airing:
        out("== 正在放送 (%d) ==" % len(airing))
        for r in airing:
            out(line(r))
        out("")
    if maybe:
        out("== 不确定 (日历里没有，需要核实) (%d) ==" % len(maybe))
        for r in maybe:
            out(line(r))
        out("")
    if done and not args.only:
        out("== 已完结 / 未开播 (%d) ==" % len(done))
        for r in done:
            out(line(r))
        out("")
    out("说明: 默认用「每日放送」日历判断；加 --deep 会逐条目拉取章节放送日核实（较慢）。")


def estimate_airing(subj):
    today = time.strftime("%Y-%m-%d")
    date = subj.get("date")
    eps = subj.get("eps") or 0
    if not date:
        return {"airing": None, "source": "estimate"}
    if date > today:
        return {"airing": False, "source": "estimate", "reason": "未开播"}
    if not eps:
        return {"airing": None, "source": "estimate"}
    try:
        y, m, d = [int(x) for x in date.split("-")]
        start = time.mktime((y, m, d, 0, 0, 0, 0, 0, -1))
    except Exception:
        return {"airing": None, "source": "estimate"}
    end = start + (eps - 1) * 7 * 86400 + 6 * 86400
    if end >= time.time():
        return {"airing": True, "source": "estimate", "reason": "按开播日+集数估算"}
    return {"airing": False, "source": "estimate", "reason": "按开播日+集数估算已完结"}


def cmd_set_token(args):
    path = os.path.join(SKILL_ROOT, "token.txt")
    tok = args.token
    if not tok:
        import getpass
        tok = getpass.getpass("粘贴 access token: ").strip()
    if not tok:
        raise BgmError("没有拿到 token")
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(tok + "\n")
    out("已写入 %s" % path)
    out("验证中 ...")
    me = api("GET", "/me", token=tok)
    out("OK: %s (%s)" % (me.get("nickname"), me.get("username")))


# ------------------------------------------------------------------- main ---

def build_parser():
    p = argparse.ArgumentParser(prog="bgm.py", description="Bangumi helper CLI")
    p.add_argument("--token", help="access token (默认读取 token.txt / 环境变量)")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("whoami", help="验证 token 并显示账号")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_whoami)

    sp = sub.add_parser("search", help="搜索条目")
    sp.add_argument("keyword")
    sp.add_argument("--type", type=int, default=2,
                    help="1书籍 2动画 3音乐 4游戏 6三次元 (默认 2)")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("info", help="条目详情")
    sp.add_argument("target", help="条目 id 或标题")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("episodes", help="章节列表")
    sp.add_argument("target", help="条目 id 或标题")
    sp.add_argument("--type", dest="ep_type", type=int, default=0,
                    help="章节类型，默认 0 本篇")
    sp.add_argument("--all", action="store_true", help="列出全部类型章节")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_episodes)

    sp = sub.add_parser("mark", help="标记条目收藏状态（新增或修改）")
    sp.add_argument("target", help="条目 id 或标题")
    sp.add_argument("--status", required=True,
                    help="wish/doing/done/onhold/dropped 或 想看/在看/看过/搁置/抛弃")
    sp.add_argument("--rate", type=int, help="同时打分 1-10")
    sp.add_argument("--comment", help="短评")
    sp.add_argument("--private", action="store_true", help="设为仅自己可见")
    sp.set_defaults(func=cmd_mark)

    sp = sub.add_parser("rate", help="给条目打分（0 表示删除评分）")
    sp.add_argument("subject_id", type=int)
    sp.add_argument("score", type=int)
    sp.set_defaults(func=cmd_rate)

    sp = sub.add_parser("ep-watch", help="标记某动画的第 N 集为看过")
    sp.add_argument("subject_id", type=int)
    sp.add_argument("ep")
    sp.add_argument("--status", type=int, default=2, dest="ep_status",
                    choices=[0, 1, 2, 3], help="0未收藏 1想看 2看过 3抛弃 (默认 2)")
    sp.set_defaults(func=cmd_ep_watch)

    sp = sub.add_parser("ep-watch-id", help="按 episode_id 标记章节")
    sp.add_argument("episode_id", type=int)
    sp.add_argument("--status", type=int, default=2, dest="ep_status",
                    choices=[0, 1, 2, 3], help="0未收藏 1想看 2看过 3抛弃 (默认 2)")
    sp.set_defaults(func=cmd_ep_watch_id)

    sp = sub.add_parser("airing", help="检查想看/在看的动画是否正在放送")
    sp.add_argument("--username", help="默认用 token 对应的账号")
    sp.add_argument("--status", default="wish,doing", help="默认 wish,doing")
    sp.add_argument("--deep", action="store_true", help="逐条目拉章节放送日核实")
    sp.add_argument("--only", action="store_true", help="只输出正在放送的")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_airing)

    sp = sub.add_parser("calendar", help="每日放送（正在放送的动画）")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_calendar)

    sp = sub.add_parser("comments", help="某一集的吐槽/讨论")
    sp.add_argument("episode_id", nargs="?", type=int)
    sp.add_argument("--subject", type=int, help="条目 id（配合 --ep 定位剧集）")
    sp.add_argument("--ep", help="集数（配合 --subject）")
    sp.add_argument("--label", help="输出标题里显示的名字")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--sort", choices=["asc", "desc"], default="desc",
                    help="desc=最新的在前（默认），asc=从最早开始")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_comments)

    sp = sub.add_parser("topics", help="条目的讨论版主题列表")
    sp.add_argument("target", help="条目 id 或标题")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_topics)

    sp = sub.add_parser("set-token", help="把 access token 写入 token.txt")
    sp.add_argument("token", nargs="?")
    sp.set_defaults(func=cmd_set_token)
    return p


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        args.func(args)
        return 0
    except BgmError as exc:
        sys.stderr.write("错误: %s\n" % exc)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
