# -*- coding: utf-8 -*-
"""猫娘的情绪状态（★ 2026-10-18 用户要求「表现得更像一个人，拥有真实的情感」）。

为什么要它：此前机器人**没有情绪连续性** —— 每条回复都是"重新开始"，被怼了
下一句照样嬉皮笑脸，深夜也不会低落，看起来就是个执行器。这份状态把「心情」
做成一个**会持久化、会随时间回归、会被群友影响**的值，写进 prompt 让语气跟着变。

设计（刻意保守，别让它变成负担）：
  · 一个 5~95 的数值 + 最近几条情绪缘由，存 `data/mood.json`；
  · 每 30 分钟向「当前时段的基线」回归一点（白天 55、深夜 40）；
  · 事件：被 @ +3、被夸 +6、被凶 -10、有人聊天 +1、长时间没人理 -1；
  · 同类事件 60 秒内只记一次（读屏一轮可能看到好几条同样的消息）；
  · `describe()` 给模型一句自然语言（不是数字），`factor()` 给主动插话乘系数。
  · 只影响**语气和主动程度**，不影响是否回复/安全逻辑。
"""
from __future__ import annotations

import json
import time

from .paths import DATA_DIR

MOOD_FILE = DATA_DIR / "mood.json"
MIN, MAX = 5.0, 95.0
DAY_BASE, NIGHT_BASE = 55.0, 40.0
HALF_LIFE = 45 * 60          # 向基线回归的半衰期（秒）
EVENT_GAP = 60               # 同类事件最小间隔（秒）

_PRAISE = ("可爱", "好萌", "喜欢你", "爱你", "谢谢", "真棒", "厉害", "好乖", "乖")
_SCOLD = ("滚", "闭嘴", "烦死", "有病", "神经病", "蠢货", "傻逼", "废物", "别烦")


def _load() -> dict:
    try:
        d = json.loads(MOOD_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {"value": DAY_BASE, "ts": time.time(), "events": [], "seen": {}}


def _save(d: dict) -> None:
    try:
        MOOD_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _base(now: float) -> float:
    h = time.localtime(now).tm_hour
    return NIGHT_BASE if (h >= 23 or h < 3) else DAY_BASE


def get() -> dict:
    """读状态并做时间回归（不写盘也可以，调用方会通过 add 落盘）。"""
    d = _load()
    now = time.time()
    try:
        v = float(d.get("value") or DAY_BASE)
        ts = float(d.get("ts") or now)
    except Exception:
        v, ts = DAY_BASE, now
    dt = max(0.0, now - ts)
    if dt > 0:
        base = _base(now)
        # 指数回归：dt 秒后向基线靠拢
        k = 1.0 - 0.5 ** (dt / HALF_LIFE)
        v = v + (base - v) * k
    d["value"] = max(MIN, min(MAX, v))
    return d


def add(delta: float, why: str = "", key: str = "", who: str = "") -> None:
    """记一次情绪事件。key 非空时做 60 秒同类节流。"""
    now = time.time()
    d = get()
    seen = d.get("seen") or {}
    if key:
        try:
            if now - float(seen.get(key) or 0) < EVENT_GAP:
                return
        except Exception:
            pass
        seen[key] = now
        d["seen"] = seen
    d["value"] = max(MIN, min(MAX, float(d.get("value") or DAY_BASE) + float(delta)))
    d["ts"] = now
    ev = d.get("events") or []
    if why:
        ev.append({"t": now, "d": delta, "why": why, "who": who})
        # ★ 2026-10-18（用户选做项 2：情绪记忆跨天）：缘由留 7 天，不再只留 5 条
        ev = [e for e in ev if now - float(e.get("t") or 0) < 7 * 86400][-20:]
    d["events"] = ev
    _save(d)


def observe(messages, is_mention=None) -> None:
    """看这一轮读到的群友消息，更新情绪（只取最新几条，避免重复计）。"""
    if not messages:
        # 很久没人说话 → 慢慢低落（5 分钟才记一次，不然每轮都掉）
        d = get()
        try:
            last = float((d.get("seen") or {}).get("ignored") or 0)
        except Exception:
            last = 0.0
        if time.time() - last >= 300:
            add(-0.5, "没人理", key="ignored")
        return
    try:
        from . import relations
    except Exception:
        relations = None
    hit_bad = hit_good = hit_mention = False
    bad_who = good_who = ""
    for m in messages[-5:]:
        t = str(getattr(m, "text", "") or "")
        who = str(getattr(m, "sender", "") or "").strip()
        if any(w in t for w in _SCOLD):
            hit_bad = True
            bad_who = bad_who or who
            if who and relations:
                relations.bump(who, -15, "说过你不好听的", key="scold")
        elif any(w in t for w in _PRAISE):
            hit_good = True
            good_who = good_who or who
            if who and relations:
                relations.bump(who, +8, "夸过你", key="praise")
        if is_mention and is_mention(t):
            hit_mention = True
            if who and relations:
                relations.bump(who, +3, "常叫你", key="mention")
    if hit_bad:
        add(-10.0, "被凶了", key="scold", who=bad_who)
    elif hit_good:
        add(+4.0, "被夸了", key="praise", who=good_who)
    if hit_mention:
        add(+3.0, "被叫到了", key="mention")
    add(+1.0, "群里在聊", key="chat")


def describe() -> str:
    """给模型看的一句自然语言（不带数值，免得它把数字念出来）。"""
    v = float(get()["value"])
    if v < 25:
        return ("你现在的状态：**很低落**，有点不想说话。回复要短、别硬撑活泼，"
                "别人逗你也只是淡淡应一声；不要主动找话。")
    if v < 42:
        return ("你现在的状态：**有点没精神**，话不多，偶尔走神；"
                "别人问就答，但不主动凑热闹。")
    if v < 62:
        return "你现在的状态：**一般般**，正常聊，不用刻意嗨也不用装丧。"
    if v < 80:
        return ("你现在的状态：**心情不错**，尾巴翘着，话可以多一点，"
                "爱逗人、偶尔主动撩一下。")
    return ("你现在的状态：**很开心**，比平时更活泼、更黏人，"
            "愿意多说两句，也更容易接别人的玩笑。")


def factor() -> float:
    """主动插话的乘数（心情低就少开口）。"""
    v = float(get()["value"])
    if v < 30:
        return 0.3
    if v < 45:
        return 0.6
    if v < 65:
        return 1.0
    return 1.25


def _when(age: float) -> str:
    if age < 86400:
        return "今天"
    if age < 2 * 86400:
        return "昨天"
    if age < 3 * 86400:
        return "前天"
    return "%d 天前" % int(age // 86400)


def mood_line() -> str:
    """附上一句情绪缘由 —— 跨天保留（用户选做项 2：昨天被怼，今天还有点介意）。

    只取一条：优先最近 3 天内的**负面**事件（情绪更强），没有就取最近一条。
    太久（>3 天）的不再提，免得变成祥林嫂。
    """
    d = get()
    now = time.time()
    ev = []
    for e in (d.get("events") or []):
        try:
            age = now - float(e.get("t") or 0)
        except Exception:
            continue
        if age <= 3 * 86400 and e.get("why"):
            ev.append((age, e))
    if not ev:
        return ""
    ev.sort(key=lambda x: x[0])
    neg = [x for x in ev if float(x[1].get("d") or 0) < 0]
    age, e = (neg[0] if neg else ev[0])
    why = str(e.get("why") or "")
    who = str(e.get("who") or "").strip()
    extra = ("（%s「%s」）" % (_when(age), who)) if who else ""
    return "（最近的情绪：%s%s，现在还记着）" % (why, extra)
