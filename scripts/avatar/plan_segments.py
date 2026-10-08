"""给数字人分段：只设"每段最长 14 秒"这一条限制，其余尽量少拆。

  python plan_segments.py --workdir <项目目录>          # 读 out/words.json 和 out/merged.wav
  python plan_segments.py --workdir <项目目录> --max 14

规则：
  * 全长不超过 14 秒 → 整段一次，不拆。
  * 否则段数 = ceil(总长/14)，并在"停顿"处断开（配音技能 align_words.py 找出的 ≥0.25 秒的停顿，取停顿中点），
    切点尽量靠近均分位置，这样不会剩下一小截尾巴。不设最短限制。
  * 某个段数下找不到合适的停顿使每段 ≤14 秒时，才依次放宽：先加一段，再允许在句末标点处断，最后才允许在词边界断。
    （宁可多一段，也不切在句子中间。）
产物：<workdir>/avatar/plan.json、seg_NN.wav（从 merged.wav 精确切出的每段音频）。
"""
import argparse
import json
import math
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

PUNCT = "，。！？；,.!?;"
MIN_SANITY = 1.0     # 只是防止切出不足 1 秒的碎片，不是规则


def candidates(words, pauses, tier):
    c = []
    for p in pauses:
        c.append(((p["start"] + p["end"]) / 2, "停顿"))
    if tier >= 2:
        for w, nx in zip(words, words[1:]):
            if any(ch in PUNCT for ch in w["text"][len(w["word"]):]):
                c.append(((w["end"] + nx["start"]) / 2, "标点"))
    if tier >= 3:
        for w, nx in zip(words, words[1:]):
            c.append(((w["end"] + nx["start"]) / 2, "词边界"))
    return sorted(set(c))


def pick_cuts(total, n, cands):
    cuts, prev = [], 0.0
    for k in range(1, n):
        ideal = total * k / n
        ok = [c for c in cands if c[0] > prev + MIN_SANITY and c[0] < total - MIN_SANITY]
        if not ok:
            return None
        best = min(ok, key=lambda c: abs(c[0] - ideal))
        cuts.append(best)
        prev = best[0]
    return cuts


def valid(total, cuts, mx):
    pts = [0.0] + [c[0] for c in cuts] + [total]
    d = [b - a for a, b in zip(pts, pts[1:])]
    return all(MIN_SANITY <= x <= mx + 1e-6 for x in d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--max", type=float, default=C.MAX_SEGMENT_SECONDS)
    a = ap.parse_args()
    out = os.path.join(a.workdir, "out")
    wj = json.load(open(os.path.join(out, "words.json"), encoding="utf-8"))
    words, pauses = wj["words"], wj["pauses"]
    wf = wave.open(os.path.join(out, "merged.wav"))
    sr, nfr = wf.getframerate(), wf.getnframes()
    audio = np.frombuffer(wf.readframes(nfr), np.int16)
    wf.close()
    total = nfr / sr

    cuts, tier_used = [], 0
    if total > a.max:
        n_min = math.ceil(total / a.max)
        found = None
        for tier in (1, 2, 3):
            cands = candidates(words, pauses, tier)
            for n in range(n_min, n_min + 6):
                cs = pick_cuts(total, n, cands)
                if cs is not None and valid(total, cs, a.max):
                    found = (cs, tier)
                    break
            if found:
                break
        if not found:
            sys.exit("找不到合适的切点让每段 ≤%.0f 秒（总长 %.1f 秒）。请检查 words.json" % (a.max, total))
        cuts, tier_used = found
    pts = [0.0] + [c[0] for c in cuts] + [total]
    kinds = ["开头"] + [c[1] for c in cuts] + ["结尾"]

    od = os.path.join(a.workdir, "avatar")
    os.makedirs(od, exist_ok=True)
    segs = []
    for i in range(len(pts) - 1):
        s, e = pts[i], pts[i + 1]
        ws = [w for w in words if s <= (w["start"] + w["end"]) / 2 < e]
        sl = audio[int(round(s * sr)):int(round(e * sr))]
        path = os.path.join(od, "seg_%02d.wav" % (i + 1))
        w = wave.open(path, "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(sl.tobytes())
        w.close()
        segs.append({"i": i + 1, "start": round(s, 3), "end": round(e, 3), "duration": round(e - s, 3),
                     "start_kind": kinds[i], "end_kind": kinds[i + 1], "audio": path,
                     "text_asr": "".join(x["text"] for x in ws),
                     "words": [{"word": x["word"], "start": round(x["start"] - s, 3), "end": round(x["end"] - s, 3)} for x in ws]})
    plan = {"max_seconds": a.max, "total_seconds": round(total, 3), "cut_tier": tier_used, "segments": segs}
    json.dump(plan, open(os.path.join(od, "plan.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    C_ = {0: "整段，无需拆分", 1: "全部在停顿处断开", 2: "有切点用了句末标点（该档段数下找不到合适停顿）", 3: "有切点用了词边界（找不到停顿和标点）"}
    print("总长 %.1f 秒，上限 %.0f 秒 → %d 段（%s）" % (total, a.max, len(segs), C_[tier_used]))
    for s in segs:
        print("段%d：%.2f–%.2fs（%.1fs） 起于%s 止于%s  %s…" % (s["i"], s["start"], s["end"], s["duration"], s["start_kind"], s["end_kind"], s["text_asr"][:18]))
    print("计划已写入", os.path.join(od, "plan.json"))


if __name__ == "__main__":
    main()
