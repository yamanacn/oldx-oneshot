"""遮盖人物视频里的异常时间段：异常前让人物缩到圆形小窗（或暂时隐藏），主画面换成要点卡，异常过后再回到全屏。
适用于数字人视频里出现的灯光变色、背景多出东西、嘴不动等问题——不重新生成，用包装把它盖住。

两步：
  1) 规划遮盖区间
     python cover_plan.py plan --anomalies anomalies.json --words words.json --out covers.json
        [--protect 0-3 --protect 41.5-44] [--lead 1.5] [--max-ratio 0.4]
     anomalies.json：{"duration": 秒, "anomalies": [{"start", "end", "type", "cover": "content_pip"|"content_full", "detail"}]}
                     数字人技能的 qa_video.py 会写出这个文件；也可以手写。
     words.json：逐词时间（配音技能的 out/words.json）。用来把区间对齐到句子开头，并取出每个区间里说的话。
     规则：区间起点至少比异常早 --lead 秒，再往前对齐到句子开头（最多提前 5 秒）；终点对齐到异常结束后的下一句开头；
          间隔不足 2 秒的区间合并，不足 3 秒的延长；--protect 的时间段（默认片头 0–3 秒）不遮盖，
          异常落在里面就列入 not_coverable（只能重跑）；遮盖总时长超过全片 --max-ratio 时建议重跑而不是遮盖。
  2) 写要点卡，生成合成用的 spec
     看 covers.json 里每个区间的 sentences，为它写一个标题和 1–4 条要点，存成 cards.json：
       {"C1": {"title": "第二件：出镜", "points": [{"text": "一张照片 → 开口说话", "after": "一张照片"},
                                                 {"text": "不用绿幕", "at": 30.1}]}}
     after 是这条要点出现前说到的词（按逐词时间定位），at 是直接给成片秒数；都不写就在区间里均匀出现。
     想用自己做好的图或视频当主画面，就给这个区间写 "content": "文件路径"，可以不写 title 和 points。
     python cover_plan.py spec --covers covers.json --cards cards.json --words words.json --presenter 人物.mp4 --out spec.json
     然后：python pip_render.py spec.json --out 成片.mp4 ；python av_check.py spec.json 成片.mp4
"""
import argparse
import json
import re
import sys
from pathlib import Path

import _common  # noqa: F401
from _common import media_info

END_PUNCT = "。！？!?"
STRIP = re.compile(r"[\s，。、！？!?；;：:“”\"'（）()\-—…,.]")
MAX_SNAP = 5.0


def load_words(path):
    d = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    ws = d["words"] if isinstance(d, dict) else d
    return [{"raw": (w.get("text") or w.get("word") or ""), "start": float(w["start"]), "end": float(w["end"])} for w in ws]


def sentences(words, pause=0.6):
    """按句末标点或较长停顿切句：[{start, end, text}]。"""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        raw = w["raw"].strip()
        if nxt is None or (raw and raw[-1] in END_PUNCT) or nxt["start"] - w["end"] >= pause:
            out.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": "".join(x["raw"] for x in cur).strip()})
            cur = []
    return out


def cmd_plan(a):
    an = json.loads(Path(a.anomalies).read_text(encoding="utf-8-sig"))
    words = load_words(a.words)
    sents = sentences(words)
    dur = float(an.get("duration") or (words[-1]["end"] if words else 0))
    starts = [s["start"] for s in sents]
    protect = [(0.0, 3.0)] if a.protect is None else [tuple(float(x) for x in p.split("-")) for p in a.protect]
    covers, skipped = [], []
    for x in an.get("anomalies", []):
        s, e = float(x["start"]), float(x["end"])
        if any(s < pb and e > pa for pa, pb in protect):
            skipped.append({**x, "reason": "落在不遮盖的时间段里（片头或指定要人物全屏的地方），只能重跑这一段"})
            continue
        want = s - a.lead
        cand = [t for t in starts if t <= want and want - t <= MAX_SNAP]
        cs = max(cand) if cand else max(0.0, want)
        for pa, pb in protect:                               # 起点不压进保护区
            if pa <= cs < pb:
                cs = pb
        after = [t for t in starts if t >= e + 0.5 and t - e <= MAX_SNAP]
        ce = min(after) if after else min(dur, e + 1.0)
        covers.append({"start": cs, "end": ce, "anomalies": [x]})
    covers.sort(key=lambda c: c["start"])
    merged = []
    for c in covers:
        if merged and c["start"] - merged[-1]["end"] < 2.0:
            merged[-1]["end"] = max(merged[-1]["end"], c["end"])
            merged[-1]["anomalies"] += c["anomalies"]
        else:
            merged.append(c)
    for c in merged:
        if c["end"] - c["start"] < 3.0:
            later = [t for t in starts if t >= c["start"] + 3.0]
            c["end"] = min(dur, later[0] if later and later[0] - c["start"] <= 3.0 + MAX_SNAP else c["start"] + 3.0)
    out = []
    for i, c in enumerate(merged, 1):
        full = [[round(max(c["start"], float(x["start"]) - 0.3), 2), round(min(c["end"], float(x["end"]) + 0.3), 2)]
                for x in c["anomalies"] if x.get("cover") == "content_full"]
        out.append({"id": "C%d" % i, "start": round(c["start"], 2), "end": round(c["end"], 2), "hide_presenter": full,
                    "sentences": [s for s in sents if s["end"] > c["start"] and s["start"] < c["end"]],
                    "anomalies": c["anomalies"]})
    total = sum(c["end"] - c["start"] for c in out)
    ratio = total / dur if dur else 0.0
    res = {"duration": dur, "cover_seconds": round(total, 2), "cover_ratio": round(ratio, 3),
           "recommend": "rerun" if ratio > a.max_ratio or skipped else "cover" if out else "nothing_to_cover",
           "covers": out, "not_coverable": skipped}
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": a.out, "covers": [[c["id"], c["start"], c["end"]] for c in out],
                      "cover_ratio": res["cover_ratio"], "recommend": res["recommend"],
                      "not_coverable": len(skipped)}, ensure_ascii=False, indent=2))
    if skipped:
        print("有 %d 处异常落在不遮盖的时间段里（见 not_coverable），这些只能重跑对应的段。" % len(skipped))
    if ratio > a.max_ratio:
        print("遮盖时长超过全片的 %.0f%%：成片会变成以图形为主，建议重跑问题段而不是遮盖。" % (a.max_ratio * 100))


def find_time(words, key, t0, t1):
    """在 [t0, t1] 里找说到 key 的时刻（key 的最后一个字说完时）。"""
    chars, ends = [], []
    for w in words:
        if w["end"] < t0 or w["start"] > t1:
            continue
        txt = STRIP.sub("", w["raw"])
        for k, ch in enumerate(txt):
            chars.append(ch)
            ends.append(w["start"] + (w["end"] - w["start"]) * (k + 1) / max(1, len(txt)))
    i = "".join(chars).lower().find(STRIP.sub("", key).lower())
    return None if i < 0 else ends[i + len(STRIP.sub("", key)) - 1]


def cmd_spec(a):
    plan = json.loads(Path(a.covers).read_text(encoding="utf-8-sig"))
    cards = json.loads(Path(a.cards).read_text(encoding="utf-8-sig"))
    words = load_words(a.words) if a.words else []
    if not Path(a.presenter).is_file():
        sys.exit(f"人物视频不存在：{a.presenter}")
    import cv2
    cap = cv2.VideoCapture(str(a.presenter))
    W, H, fps = int(cap.get(3)), int(cap.get(4)), cap.get(5)
    cap.release()
    if not W or not H:
        sys.exit(f"读不到人物视频的尺寸：{a.presenter}")
    segs, overlays, notes = [], [], []
    for c in plan["covers"]:
        card = cards.get(c["id"])
        if not card:
            sys.exit(f"cards.json 里缺少区间 {c['id']} 的要点卡（{c['start']}–{c['end']} 秒：{''.join(s['text'] for s in c['sentences'])[:40]}…）")
        content = card.get("content")
        cuts = sorted({c["start"], c["end"], *[t for span in c.get("hide_presenter", []) for t in span]})
        for s0, s1 in zip(cuts, cuts[1:]):
            hidden = any(h0 <= s0 and s1 <= h1 for h0, h1 in c.get("hide_presenter", []))
            segs.append({"start": s0, "end": s1, "mode": "content_full" if hidden else "content_pip", "content": content})
        if content:
            continue
        t_end = c["end"] - 0.3
        if card.get("title"):
            overlays.append({"type": "text", "text": card["title"], "start": round(c["start"] + 0.5, 2), "end": round(t_end, 2),
                             "pos": [0.42, 0.22], "size": 92, "bg": None, "stroke": 0, "color": [255, 214, 10],
                             "anim": "rise", "layer": "content", "max_width": 0.74})
        pts = card.get("points", [])[:4]
        if len(card.get("points", [])) > 4:
            notes.append(f"{c['id']}：要点超过 4 条，只用了前 4 条")
        for k, p in enumerate(pts):
            t = p.get("at")
            if t is None and p.get("after") and words:
                t = find_time(words, p["after"], c["start"], c["end"])
                if t is None:
                    notes.append(f"{c['id']}：在这个区间里没找到「{p['after']}」，这条要点改为均匀出现")
            if t is None:
                t = c["start"] + 1.2 + (c["end"] - c["start"] - 2.5) * k / max(1, len(pts))
            t = min(max(float(t), c["start"] + 0.6), t_end - 1.0)
            overlays.append({"type": "text", "text": p["text"], "start": round(t, 2), "end": round(t_end, 2),
                             "pos": [0.42, 0.42 + 0.135 * k], "size": 60, "bg": [45, 45, 60], "anim": "rise",
                             "layer": "content", "max_width": 0.74})
    out = Path(a.out)
    try:
        rel = Path(a.presenter).resolve().relative_to(out.resolve().parent).as_posix()
    except ValueError:
        rel = str(Path(a.presenter).resolve())
    spec = {"presenter": rel, "size": [W, H], "fps": str(round(fps, 3)).rstrip("0").rstrip("."),
            "pip": {"focus": [float(x) for x in a.focus.split(",")], "focus_size": a.focus_size},
            "segments": segs, "overlays": overlays}
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out), "segments": len(segs), "overlays": len(overlays), "notes": notes},
                     ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(description="规划并生成异常遮盖")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--anomalies", required=True)
    p.add_argument("--words", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--protect", action="append", help="不遮盖的时间段，如 0-3；可重复。不写时默认保护片头 0–3 秒")
    p.add_argument("--lead", type=float, default=1.5, help="至少提前多少秒让人物缩完")
    p.add_argument("--max-ratio", type=float, default=0.4)
    p.set_defaults(fn=cmd_plan)
    s = sub.add_parser("spec")
    s.add_argument("--covers", required=True)
    s.add_argument("--cards", required=True)
    s.add_argument("--presenter", required=True)
    s.add_argument("--words")
    s.add_argument("--out", required=True)
    s.add_argument("--focus", default="0.5,0.38", help="头肩中心在人物画面里的位置（先看一眼人物在哪）")
    s.add_argument("--focus-size", type=float, default=0.6)
    s.set_defaults(fn=cmd_spec)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
