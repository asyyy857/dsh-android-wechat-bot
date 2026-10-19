# -*- coding: utf-8 -*-
"""从群聊流水里自动学「能接得起的话题词」（★ 2026-10-18 用户要求）。

用户原话：「这个跟记忆库一样自动更新吧，只要群里就是能接得起他们话题的，
他们会彼此聊天的，你都可以注意注意，然后更新到里边」。

做法（纯本地统计，不花 token）：
  · 每 `RUN_EVERY` 秒看一次最近 `HOURS` 小时的 chat_log（跳过机器人自己发的）；
  · 抽 2~4 字中文片段，统计「出现在多少条消息 / 多少个不同的人」；
  · 只收 **至少 2 个人、至少 3 条消息** 都出现过的词 —— 这是"大家在聊"的信号；
    一个人的口头禅、昵称、门派名都不算；
  · 写到 data/兴趣词表.json；brain.score_topic 命中时加分，**每条最多 +2**
    （防止叠加把分数打爆）；7 天没再出现的词自动过期，总数上限 60。
  · 与静态 `INTEREST_WORDS` 互斥：静态表里有的、或互相包含的，一律不学，
    免得「甘蔗」和「彩虹甘蔗」重复计分。

可回滚：删掉 data/兴趣词表.json 就回到"只有手写表"。
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict

from .paths import DATA_DIR

LEARN_FILE = DATA_DIR / "兴趣词表.json"
RUN_EVERY = 20 * 60        # 多久跑一次（秒）
HOURS = 6.0                # 看最近多久的聊天
MAX_WORDS = 60             # 学习表上限
EXPIRE_DAYS = 7            # 多少天没再出现就忘掉
MIN_MSGS = 4               # 至少出现在几条消息里（3 条会学进大量碎片）
MIN_SENDERS = 2            # 至少几个不同的人在说

# 停用字：含任何一个就不当候选（"以为/操作/自己/消息"这类泛词全在这条被挡掉）
STOP_CHARS = set(
    "的了是我你他她它们这那有没不也就都还又很太吧吗呢啊哦嗯哈呵嘿呀嘛啦么个和在就说着过"
    "要会能可到对给让被把从里上下中前后天时点分秒先再才又怎啥哪谁")
NOISE = set("""
图片 表情 表情包 消息 自己 知道 感觉 已经 以为 操作 现在 什么 怎么 就是 可以 不是 这个
那个 一下 时候 今天 明天 昨天 真的 然后 还是 因为 所以 但是 如果 我们 你们 他们 她们
大家 哈哈 哈哈哈 呵呵 嘿嘿 嗯嗯 哦哦 好的 好嘞 没事 没有 这样 那样 多少 有点
助手 综合 笔记 保险 问答 生成 概率 冷却 材料 恶心 图片 红包 转账 语音 视频 群公告 月份 二月 几月 以为 觉得 感觉 应该 可能 需要 希望 知道 看见 听到 时候 现在 以后 之前 之后 东西 事情 问题 地方 时间
""".split())
# 门派/流派名：用户 2026-10-07 明确要求不许作为接话理由（昵称常以门派名打头）
SCHOOLS = set("龙吟 素问 潮光 铁衣 神相 九灵 血河 碎梦 玄机 沧澜 鸿音".split())


def _norm(x) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", str(x or ""))


def _candidates(text: str):
    out = set()
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        for n in (2, 3, 4):
            for i in range(len(seg) - n + 1):
                w = seg[i:i + n]
                if w in NOISE or w in SCHOOLS:
                    continue
                if any(c in STOP_CHARS for c in w):
                    continue
                if len(set(w)) == 1:
                    continue
                out.add(w)
    return out


def _sim(a: str, b: str) -> bool:
    """两个词是不是同一个名字（容 OCR 抖动）—— 简单包含/高相似判据。"""
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return min(len(a), len(b)) >= 2
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def _load_names() -> set:
    names = set()
    for fn, key in (("名字库.json", "names"), ("名字库.json", "noise"),
                    ("群成员前缀.json", "prefix")):
        try:
            d = json.loads((DATA_DIR / fn).read_text(encoding="utf-8")) or {}
            v = d.get(key)
            if isinstance(v, dict):
                v = list(v)
            for n in (v or []):
                k = _norm(n)
                if k:
                    names.add(k)
        except Exception:
            continue
    return names


def learn(log=None, hours: float = HOURS) -> int:
    """跑一轮学习，返回新增词数（异常一律吞掉，绝不影响机器人）。"""
    def say(level, msg):
        try:
            if log is not None:
                getattr(log, level)(msg)
        except Exception:
            pass
    try:
        chat = json.loads((DATA_DIR / "chat_log.json").read_text(encoding="utf-8"))
        msgs = chat.get("messages") if isinstance(chat, dict) else chat
    except Exception:
        return 0
    now = time.time()
    since = now - float(hours) * 3600
    names = _load_names()
    try:
        from .brain import INTEREST_WORDS
    except Exception:
        INTEREST_WORDS = {}
    static = [w for w in INTEREST_WORDS if len(w) >= 2]
    hits = defaultdict(int)
    senders = defaultdict(set)
    talk_names = set()
    for m in (msgs or []):
        if not isinstance(m, dict) or m.get("self"):
            continue
        if m.get("type") not in (None, "text"):
            continue
        t = _norm(m.get("text") or "")
        if len(t) < 4:
            continue
        ts = float(m.get("ts") or 0)
        if ts and ts < since:
            continue
        who = str(m.get("sender") or "")
        if who:
            talk_names.add(_norm(who))
        # 「@某某」里被点名的也是人（不一定发过言，比如「@嫌晚」）
        for mt in re.finditer(r"@([\u4e00-\u9fff]{2,3})", m.get("text") or ""):
            talk_names.add(_norm(mt.group(1)))
        for w in _candidates(t):
            hits[w] += 1
            senders[w].add(who)

    talk_names.discard("")
    names |= talk_names
    old = {}
    try:
        old = (json.loads(LEARN_FILE.read_text(encoding="utf-8")) or {}).get("words") or {}
    except Exception:
        pass
    words = {}
    cut = now - EXPIRE_DAYS * 86400
    for w, d in old.items():
        try:
            ts = float(d.get("ts") or 0)
        except Exception:
            ts = 0
        if ts >= cut:
            words[w] = d

    # 先把够格的候选按长度从长到短排；短碎片（是某个更长候选的子串、频次还差不多）
    # 直接丢掉 —— 否则「酒店民宿」会同时学进「店民」「务目」这类跨词碎片。
    cand = [w for w, n in hits.items()
            if n >= MIN_MSGS and len(senders[w]) >= MIN_SENDERS]
    cand.sort(key=len, reverse=True)
    kept = []
    for w in cand:
        if any(w != k and w in k and hits[k] >= hits[w] - 1 for k in kept):
            continue
        kept.append(w)
    added = []
    for w in kept:
        n = hits[w]
        if w.startswith("图片") or "图片" in w:
            continue
        if any(w == s for s in static):
            continue
        if any((w in s or s in w) for s in static):
            continue
        if any((w in nm or nm in w) for nm in names):
            continue
        # 与人名高度相似 = 被讨论的群友：可以学，但只给 1 分（别让人名主导触发）
        _is_person = any(_sim(w, nm) for nm in names)
        sc = 1 if _is_person else (2 if (n >= 5 and len(senders[w]) >= 3) else 1)
        cur = words.get(w)
        if cur:
            cur["hits"] = max(int(cur.get("hits") or 0), n)
            cur["senders"] = max(int(cur.get("senders") or 0), len(senders[w]))
            cur["score"] = max(int(cur.get("score") or 1), sc)
            cur["ts"] = now
        else:
            words[w] = {"score": sc, "hits": n,
                        "senders": len(senders[w]), "ts": now}
            added.append(w)

    if len(words) > MAX_WORDS:
        keep = sorted(words.items(),
                      key=lambda kv: (-int(kv[1].get("hits") or 0),
                                      -float(kv[1].get("ts") or 0)))[:MAX_WORDS]
        words = dict(keep)

    try:
        LEARN_FILE.write_text(json.dumps(
            {"updated": now, "words": words}, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        say("warning", "兴趣词表写入失败：%r" % (exc,))
        return 0

    if added:
        show = "、".join(added[:12]) + ("…" if len(added) > 12 else "")
        say("info", "兴趣词表自动学习：新增 %d 个词（%s），现有 %d 个"
            % (len(added), show, len(words)))
    return len(added)


def maybe_learn(log=None) -> None:
    """按 RUN_EVERY 的节奏跑（用文件里的 updated 判断，重启也不会立刻重跑）。"""
    try:
        d = json.loads(LEARN_FILE.read_text(encoding="utf-8")) or {}
        if time.time() - float(d.get("updated") or 0) < RUN_EVERY:
            return
    except Exception:
        pass
    try:
        learn(log=log)
    except Exception as exc:
        try:
            if log is not None:
                log.warning("兴趣词表自动学习跳过：%r" % (exc,))
        except Exception:
            pass
