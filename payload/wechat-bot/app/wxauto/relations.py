# -*- coding: utf-8 -*-
"""对群友的态度差异（★ 2026-10-18 用户要求「对人的态度差异」）。

用户原话（选做项 3）：「谁常夸它、谁常凶它，语气上有点区别」。
这份状态让"猫娘"对具体的人有**一点点偏好**，而不是对谁都一个脸：

  · 每个群友一个 0~100 的好感度，初始 50，存 `data/relations.json`；
  · 被夸 +8、被凶 −15、被叫到 +3（只记最近 60 秒一次的同类事件）；
  · **时间衰减**：7 天半衰期向 50 回归 —— 不会记仇记一辈子；
  · `line()` 只给模型一句「对谁印象不错 / 对谁有点介意」，最多各 2 人；
  · 只是语气上的冷热，**不影响是否回复**，也不影响任何安全判断。
"""
from __future__ import annotations

import json
import time

from .paths import DATA_DIR

REL_FILE = DATA_DIR / "relations.json"
MIN, MAX = 0.0, 100.0
HALF_LIFE = 7 * 86400          # 好感度回归半衰期（7 天）
EVENT_GAP = 60
GOOD_EDGE, BAD_EDGE = 57.0, 43.0   # 偏离中位多少才算"有印象"


def _load() -> dict:
    try:
        d = json.loads(REL_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {"updated": time.time(), "people": {}, "seen": {}}


def _save(d: dict) -> None:
    try:
        REL_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _decay(rec: dict, now: float) -> float:
    try:
        v = float(rec.get("aff") or 50.0)
        ts = float(rec.get("ts") or now)
    except Exception:
        return 50.0
    dt = max(0.0, now - ts)
    k = 1.0 - 0.5 ** (dt / HALF_LIFE) if HALF_LIFE > 0 else 1.0
    return v + (50.0 - v) * k


def bump(name: str, delta: float, why: str = "", key: str = "") -> None:
    """给某个群友的好感度加减（key 非空时做 60 秒同类节流）。"""
    name = str(name or "").strip()
    if not name:
        return
    now = time.time()
    d = _load()
    seen = d.get("seen") or {}
    k = "%s:%s" % (name, key or why)
    if key:
        try:
            if now - float(seen.get(k) or 0) < EVENT_GAP:
                return
        except Exception:
            pass
        seen[k] = now
        # 只留最近的节流记录，别无限涨
        if len(seen) > 200:
            seen = dict(sorted(seen.items(), key=lambda kv: -float(kv[1] or 0))[:100])
        d["seen"] = seen
    people = d.get("people") or {}
    rec = people.get(name) or {"aff": 50.0, "ts": now, "why": "", "good": 0, "bad": 0}
    cur = _decay(rec, now)
    rec["aff"] = max(MIN, min(MAX, cur + float(delta)))
    rec["ts"] = now
    if why:
        rec["why"] = why
    if delta > 0:
        rec["good"] = int(rec.get("good") or 0) + 1
    elif delta < 0:
        rec["bad"] = int(rec.get("bad") or 0) + 1
    people[name] = rec
    d["people"] = people
    d["updated"] = now
    _save(d)


def _entries(now: float):
    d = _load()
    out = []
    for name, rec in (d.get("people") or {}).items():
        if not isinstance(rec, dict):
            continue
        v = _decay(rec, now)
        out.append((name, v, str(rec.get("why") or "")))
    return out


def line() -> str:
    """给模型的一句态度提示（没有明显偏好时返回空串）。"""
    now = time.time()
    ents = _entries(now)
    if not ents:
        return ""
    liked = [e for e in ents if e[1] >= GOOD_EDGE]
    disliked = [e for e in ents if e[1] <= BAD_EDGE]
    liked.sort(key=lambda e: -e[1])
    disliked.sort(key=lambda e: e[1])
    parts = []
    if liked[:2]:
        parts.append("对 " + "、".join("「%s」" % e[0] for e in liked[:2]) +
                     " 印象不错（他们夸过你/常叫你），语气可以热一点")
    if disliked[:2]:
        parts.append("对 " + "、".join("「%s」" % e[0] for e in disliked[:2]) +
                     " 有点介意（说过你不好听的），别主动贴上去，回得冷淡一点也行")
    if not parts:
        return ""
    return ("【你对群里某些人的态度】（只是语气冷热，不影响该不该回）\n"
            + "；".join(parts) + "。别把这条直接说出来。")
