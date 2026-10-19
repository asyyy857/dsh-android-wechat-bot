# -*- coding: utf-8 -*-
"""随机碎话 —— 每天不定时往群里甩一句没头没尾的话（2026-10-19 用户要求）。

用户口径：
  · 一天 3~5 次，发「目标群」群
  · 白天正常发，深夜偶尔来一句
  · 内容：短感叹 / 猫猫碎碎念 / 无厘头自问自答 / 语言游戏 / 喊话 / 荒诞小剧场
  · 新闻类：真新闻 + 一句话概况 + 自己的反应（每天最多 1 次）

调度为什么这么写：
  **每天早上把当天的发送时刻表算出来存进 JSON**，主循环每轮只看"到点没"。
  比"每轮掷骰子"好调试 —— 出问题能直接看到今天计划几点发、实际发了什么。
  时刻表在 ``data/random_chatter.json``，删掉它就重新排一天的。

三条铁律（跟机器人其它发送路径保持一致）：
  1. 急停 / 配额 / 长度校验全部复用 ``Bot._send_instant``，这里不管；
  2. **新闻只写标题里确定的事实**，不许补细节（模型最容易给新闻加戏）；
  3. 抓不到 / 过滤后没有安全条目 → 这一轮改发句库，宁可少发一条也不编。
"""
from __future__ import annotations

import json
import random
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from .paths import DATA_DIR

STATE = DATA_DIR / "random_chatter.json"
LINES = DATA_DIR / "random_lines.txt"

HOT_URL = ("https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc")
HOT_BACKUP = "https://top.baidu.com/board?tab=realtime"
UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36")

# 热榜天然一半是政治/灾难/犯罪：命中就不发（宁可少一条）
BLACK = (
    "中纪委", "纪委", "落马", "被查", "被捕", "逮捕", "判决", "判刑", "死刑",
    "遇难", "死亡", "去世", "逝世", "坠亡", "自杀", "袭击", "爆炸", "枪击",
    "战争", "开战", "台独", "港独", "精日", "旭日旗", "解放军", "军演",
    "赖清德", "蔡英文", "抗议", "宵禁", "暴乱", "性侵", "强奸", "猥亵",
    "失踪", "跳楼", "肿瘤", "癌", "车祸", "溺水", "地震", "洪水", "台风",
    "坠机", "坠楼", "中毒", "火灾", "坍塌", "灭门", "杀", "尸体",
)

# 轻松向优先（有这些词的排前面）
WHITE = (
    "猫", "狗", "宠物", "动物", "美食", "小炒", "奶茶", "火锅", "月饼",
    "开学", "放假", "大学生", "打工人", "上班", "天气", "降温", "下雪",
    "下雨", "演唱会", "电影", "明星", "体重", "减肥", "旅游", "景区",
    "粽子", "羽绒服", "老人", "孩子", "童年", "零食", "水果",
)


# ---------------------------------------------------------------- 时间小工具
def _hm(s, default=0):
    """'09:30' -> 570（分钟）"""
    try:
        h, m = str(s).split(":")[:2]
        return int(h) * 60 + int(m)
    except Exception:
        return default


def _fmt(minutes):
    m = int(minutes) % (24 * 60)
    return "%02d:%02d" % (m // 60, m % 60)


def _now_min(dt=None):
    dt = dt or datetime.now()
    return dt.hour * 60 + dt.minute


# ---------------------------------------------------------------- 状态文件
def _load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st):
    try:
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- 当天时刻表
def make_plan(cfg, rng, today):
    """排出当天要发的时刻。返回 state dict。

    白天窗口按次数等分成 N 段、每段里随机取一点 —— 间隔自然是几个小时，
    又不会像整点打卡。深夜单独掷一次骰子，命中就在凌晨抽一个点（每天最多 1 次）。
    """
    start = _hm(getattr(cfg, "start", "09:00"), 540)
    end = _hm(getattr(cfg, "end", "23:00"), 1380)
    lo, hi = getattr(cfg, "per_day", [3, 5]) or [3, 5]
    try:
        n = rng.randint(int(lo), int(hi))
    except Exception:
        n = 3
    n = max(1, n)
    span = max(60, end - start)

    slots = []
    for i in range(n):
        a = start + span * i // n
        b = start + span * (i + 1) // n
        # 段尾留 40 分钟余量，免得和下一段挤在一起
        slots.append(rng.randint(a, max(a, b - 40)))

    # 新闻类：每天最多一次，随机挑一个时段当"新闻档"
    news_at = None
    if bool(getattr(cfg, "news", True)) and n > 0:
        news_at = rng.randrange(n)

    plan = [{"at": t, "kind": "news" if i == news_at else "line",
             "done": False, "tries": 0, "text": None,
             "sent_at": None, "error": None}
            for i, t in enumerate(sorted(slots))]

    night = None
    try:
        chance = float(getattr(cfg, "night_chance", 0.15) or 0)
    except Exception:
        chance = 0.15
    if rng.random() < chance:
        wa, wb = getattr(cfg, "night_window", ["00:30", "04:00"]) or ["00:30", "04:00"]
        night = {"at": rng.randint(_hm(wa, 30), _hm(wb, 240)), "kind": "line",
                 "done": False, "tries": 0, "text": None,
                 "sent_at": None, "error": None}

    return {"date": today, "plan": plan, "night": night,
            "recent": [], "news_done": 0}


def _ensure_today(st, cfg, rng, today):
    if st.get("date") != today:
        old_recent = (st.get("recent") or [])[-20:]
        st = make_plan(cfg, rng, today)
        st["recent"] = old_recent          # 跨天也记得最近说过什么
    st.setdefault("plan", [])
    st.setdefault("recent", [])
    st.setdefault("news_done", 0)
    return st


# ---------------------------------------------------------------- 句库
def load_lines(path=None):
    p = Path(path) if path else LINES
    out = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            s = re.sub(r"^\d+[.、]\s*", "", s)
            if s:
                out.append(s)
    except Exception:
        pass
    return out


def pick_line(lines, recent, rng):
    """尽量不跟最近说过的重复（句库 < 20 条时自然退化成随便挑）。"""
    if not lines:
        return None
    pool = [x for x in lines if x not in recent] or list(lines)
    return rng.choice(pool)


# ---------------------------------------------------------------- 模型现编
def _clean(raw, limit):
    s = str(raw or "").strip()
    s = re.split(r"[\r\n]+", s)[0].strip()
    s = s.strip(" \"'“”「」『』")
    s = re.sub(r"^(猫猫女仆|猫娘|喵娘|喵|人家)[:：]\s*", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if limit and len(s) > limit:
        s = s[:limit].rstrip("，。、")
    return s


def model_line(brain, rng, samples, limit=30):
    """让模型照句库的语气再编一句。失败返回 None（调用方回落句库）。"""
    try:
        if not getattr(brain, "available", False):
            return None
        seed = "\n".join("- " + s for s in samples[:6])
        payload = {
            "model": getattr(brain.cfg, "model", "deepseek-chat"),
            "messages": [
                {"role": "system",
                 "content": ("你是一只猫娘（猫猫女仆），现在群里没人跟你说话，"
                             "你想随口说一句没头没尾的话。说人话、短、别解释、"
                             "别提问太多，**句尾要带个「喵」**。")},
                {"role": "user",
                 "content": (f"这些是平时随口说的话：\n{seed}\n\n"
                             "照着这个语气再写一句全新的、没出现过的，"
                             f"{max(10, limit - 15)}~{limit} 字，句尾带喵。"
                             "只输出这一句。")},
            ],
            "max_tokens": 160,
            "temperature": 1.5,
            "_no_effort": True,
        }
        body = brain._post(payload)
        if not body:
            return None
        raw = body["choices"][0]["message"]["content"]
        s = _clean(raw, limit)
        # 太短 / 全是标点 / 跟样本一模一样 → 当失败
        if len(s) < 4 or s in samples:
            return None
        return s
    except Exception:
        return None


# ---------------------------------------------------------------- 新闻
def _fetch_hot(log=None):
    """抓热榜标题。头条 JSON 优先，百度页内嵌 JSON 兜底。失败返回 []。"""
    for url in (HOT_URL, HOT_BACKUP):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=8) as r:
                raw = r.read().decode("utf-8", "ignore")
            if "toutiao" in url:
                data = json.loads(raw)
                titles = [x.get("Title") for x in (data.get("data") or [])
                          if x.get("Title")]
            else:
                i = raw.find("s-data:")
                if i < 0:
                    continue
                obj, _ = json.JSONDecoder().raw_decode(raw[i + 7:])
                cards = obj["cards"][0]["content"][0]["content"]
                titles = [x.get("word") for x in cards if x.get("word")]
            if titles:
                return titles
        except Exception as exc:
            if log:
                try:
                    log.info("热搜源不可用（%s）：%r" % (url.split("/")[2], exc))
                except Exception:
                    pass
    return []


def safe_titles(titles):
    """黑名单过滤 + 白名单优先排序。返回可能为空。"""
    ok = [t for t in titles if t and not any(b in t for b in BLACK)]
    if not ok:
        return []
    ok.sort(key=lambda t: 0 if any(w in t for w in WHITE) else 1)
    return ok


def news_text(brain, rng, log=None, limit=60):
    """抓一条真新闻，让模型写「概况 + 反应」。任何一步失败都返回 None。"""
    titles = _fetch_hot(log)
    if not titles:
        return None
    cand = safe_titles(titles)
    if not cand:
        if log:
            log.info("热榜过滤后没有安全条目，新闻类本轮跳过")
        return None
    title = rng.choice(cand[:12])
    if not getattr(brain, "available", False):
        return None
    payload = {
        "model": getattr(brain.cfg, "model", "deepseek-chat"),
        "messages": [
            {"role": "system",
             "content": ("你是群里的猫娘（猫猫女仆），看到一条新闻，想随口说两句。"
                         "只写新闻标题里确定的事实，不许补充数字、原因、结果、细节。"
                         "句尾带个「喵」。")},
            {"role": "user",
             "content": (f"新闻标题：{title}\n\n"
                         "写两句：第一句说清发生了什么（只根据标题，≤30字）；"
                         "第二句是你自己的反应（≤25字，口语，别上价值）。"
                         f"总共不超过 {limit} 字，直接输出正文。")},
        ],
        "max_tokens": 220,
        "temperature": 1.2,
        "_no_effort": True,
    }
    try:
        body = brain._post(payload)
        if not body:
            return None
        s = _clean(body["choices"][0]["message"]["content"], limit)
        if len(s) < 12 or any(b in s for b in BLACK):
            return None
        return s
    except Exception:
        return None


# ---------------------------------------------------------------- 补喵
def add_meow(text, limit=0):
    """碎话统一带「喵」（2026-10-19 用户要求）。

    比 ``safety.with_meow`` 多管一件事：句尾是问号/感叹号时，「喵」加在
    标点**前面** —— 「这也行喵？」而不是「这也行？喵」。括号小动作仍交给
    ``with_meow``（它知道把喵放在括号前）。
    """
    t = (text or "").strip()
    if not t or "喵" in t:
        return t
    m = re.search(r"([。！？!?～~…]+)\s*$", t)
    if m and not t.endswith("）"):
        cand = t[:m.start()].rstrip() + "喵" + m.group(1)
        if not limit or len(cand) <= limit:
            return cand
    try:
        from .safety import with_meow
        return with_meow(t, limit or None)
    except Exception:
        return t + "喵"


# ---------------------------------------------------------------- 入口
def maybe_run(bot, cfg, log=None):
    """主循环每轮调一次：到点就发，没到就直接返回。"""
    rng = bot.rng
    now_dt = datetime.now()
    today = now_dt.strftime("%Y-%m-%d")
    now = _now_min(now_dt)

    st = _ensure_today(_load_state(), cfg, rng, today)

    items = list(st["plan"])
    if st.get("night"):
        items.append(st["night"])

    # 找出"到点且还没发"的：迟到超过 30 分钟就作废。
    # ★ 2026-10-19 实测：原来给 120 分钟，机器人重启后把 11 分钟前那条补发了
    #   （时间点对不上，群里看着像"突然冒一句"）。30 分钟足够覆盖一次短暂重启，
    #   又不会让停机后的补发显得莫名其妙。
    due = None
    for it in items:
        if it.get("done"):
            continue
        at = int(it.get("at") or 0)
        if now < at:
            continue
        if now - at > 30:
            it["done"] = True
            it["error"] = "过时作废"
            continue
        if due is None or at < int(due.get("at") or 0):
            due = it
    if due is None:
        _save_state(st)
        return False

    limit = int(getattr(cfg, "max_chars", 60) or 60)
    text = None
    kind = due.get("kind")

    # 新闻档：每天最多一次，失败就回落句库
    if kind == "news" and st.get("news_done", 0) < 1:
        text = news_text(bot.brain, rng, log, limit=limit)
        if text:
            st["news_done"] = st.get("news_done", 0) + 1
        elif log:
            log.info("新闻类没取到内容，改发句库")

    if not text:
        lines = load_lines()
        ratio = float(getattr(cfg, "model_ratio", 0.3) or 0)
        if lines and rng.random() < ratio:
            samples = [x for x in lines if x not in st["recent"]][:6] or lines[:6]
            text = model_line(bot.brain, rng, samples, limit=min(limit, 34))
        if not text:
            text = pick_line(lines, st["recent"], rng)
    if not text:
        due["done"] = True
        due["error"] = "句库为空"
        _save_state(st)
        if log:
            log.warning("随机碎话：句库是空的，检查 data/random_lines.txt")
        return False

    # ★ 2026-10-19 用户要求「文案带喵」：碎话不依赖 `_persona_meows()` 的
    #   人设判断（机器人刚启动、还没选过偶像时那个判断是空的，实测第一条就没喵）。
    #   句子里已经有「喵」的不重复加；没有的补在句尾（标点 / 括号动作前面）。
    text = add_meow(text, limit)

    ok = bot._send_instant(text, reason="随机碎话", label="随机碎话", limit=limit)
    due["tries"] = int(due.get("tries") or 0) + 1
    if ok:
        due["done"] = True
        due["text"] = text
        due["sent_at"] = now_dt.strftime("%H:%M:%S")
        st["recent"] = (st["recent"] + [text])[-20:]
        _save_state(st)
        if log:
            log.info("随机碎话已发（%s）：%s" % (_fmt(int(due["at"])), text))
        return True

    due["error"] = "发送未成功"
    if due["tries"] >= 3:
        due["done"] = True            # 试三次还不行就放弃，别卡在这一条
        st["recent"] = (st["recent"] + [text])[-20:]
    _save_state(st)
    return False
