"""给数字人分段：只设"每段最长 14 秒"这一条限制，切点优先落在句末的停顿上。

  python plan_segments.py --workdir <项目目录>          # 读 out/words.json 和 out/merged.wav
  python plan_segments.py --workdir <项目目录> --max 14

规则：
  * 全长不超过 14 秒 → 整段一次，不拆。
  * 否则段数从 ceil(总长/14) 起，在"停顿"处断开（取停顿中点）。停顿**直接从 merged.wav 的音量里检测**
    （连续 ≥0.25 秒的低能量段），不用 words.json 里记的那份——那份可能来自另一版音频（实际发生过：手工改过时间文件，
    切点全落在词中间）。
  * 选哪些停顿：句末（。！？）的停顿最好，其次逗号、顿号、冒号处的停顿，没有标点的停顿最后才用；同时尽量均分。
    多一段能让切点都落在句末、逗号处时，宁可多一段。
  * 某个段数下找不到合适的停顿使每段 ≤14 秒时，才依次放宽：先允许在句末标点处断（没有停顿），最后才允许在词边界断。
核对（不依赖文件里的指纹）：从配音里检测出的停顿，超过 20% 的中点落在某个词中间，就说明 words.json 和 merged.wav
不是同一份音频的结果（实测：正常 4%–7%；整体偏 0.2 秒 31%；音频被调过速、没重新对齐 48%–69%），以退出码 1 停下。
产物：<workdir>/avatar/plan.json、seg_NN.wav（从 merged.wav 精确切出的每段音频）。
plan.json 记下整条和每段音频的指纹，run_avatar.py / finalize.py 会再核对一次。
"""
import argparse
import json
import math
import os
import sys
import wave
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _cli  # noqa: E402
import _util  # noqa: E402

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

SENT_END = "。！？!?…"
CLAUSE = "，、：；,:;—"
RANK_COST = {"句末": 0.0, "逗号": 1.0, "无标点": 4.0}
SOURCE_COST = {"停顿": 0.0, "标点": 2.5, "词边界": 4.0}   # 放宽之后仍然优先真正的停顿：没有停顿的切点，口型和动作都还在动
DEV_W = 1.0          # 偏离均分位置（按每段平均长度算）的代价
EXTRA_SEG_W = 1.5    # 多一段的代价
MIN_SANITY = 1.0     # 只是防止切出不足 1 秒的碎片，不是规则
PAUSE_MIN = 0.25
MISMATCH_MAX = 0.20  # 停顿落在词中间的比例超过它，就是逐词时间和配音对不上


def punct_after(words, t):
    """t 之前最后一个词后面带的标点 → 句末 / 逗号 / 无标点"""
    prev = [w for w in words if w["end"] <= t + 0.05]
    if not prev:
        return "无标点"
    w = prev[-1]
    tail = (w.get("text") or "")[len(w.get("word") or ""):] or (w.get("text") or "")[-1:]
    if any(ch in SENT_END for ch in tail):
        return "句末"
    if any(ch in CLAUSE for ch in tail):
        return "逗号"
    return "无标点"


def candidates(words, pauses, tier):
    """(时间, 来源, 标点) 的候选切点"""
    c = [((p["start"] + p["end"]) / 2, "停顿", punct_after(words, (p["start"] + p["end"]) / 2)) for p in pauses]
    if tier >= 2:
        for w, nx in zip(words, words[1:]):
            kind = punct_after([w], w["end"])
            if kind != "无标点":
                c.append(((w["end"] + nx["start"]) / 2, "标点", kind))
    if tier >= 3:
        for w, nx in zip(words, words[1:]):
            c.append(((w["end"] + nx["start"]) / 2, "词边界", "无标点"))
    return sorted(set(c))


def best_cuts(total, n, cands, mx):
    """n 段时代价最小的一组切点（动态规划）：每段 1 秒到 mx 秒；代价 = 标点 + 偏离均分。找不到返回 None"""
    m, unit = len(cands), total / n
    INF = float("inf")
    cost = [[INF] * m for _ in range(n)]
    back = [[None] * m for _ in range(n)]
    for k in range(1, n):
        ideal = total * k / n
        for j, (t, src, kind) in enumerate(cands):
            here = RANK_COST[kind] + SOURCE_COST[src] + DEV_W * abs(t - ideal) / unit
            if k == 1:
                if MIN_SANITY <= t <= mx:
                    cost[k][j] = here
                continue
            for i in range(j):
                if cost[k - 1][i] < INF and MIN_SANITY <= t - cands[i][0] <= mx and cost[k - 1][i] + here < cost[k][j]:
                    cost[k][j], back[k][j] = cost[k - 1][i] + here, i
    ends = [(cost[n - 1][j], j) for j in range(m) if cost[n - 1][j] < INF and MIN_SANITY <= total - cands[j][0] <= mx]
    if not ends:
        return None
    c, j = min(ends)
    out, k = [], n - 1
    while j is not None:
        out.append(cands[j])
        j, k = back[k][j], k - 1
    return c, out[::-1]


def mismatch(words, pauses):
    bad = [p for p in pauses if any(w["start"] + 0.03 < (p["start"] + p["end"]) / 2 < w["end"] - 0.03 for w in words)]
    return len(bad) / max(1, len(pauses)), bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--max", type=float, default=C.MAX_SEGMENT_SECONDS)
    ap.add_argument("--legacy", action="store_true", help="旧项目：words.json 里没有音频指纹也继续（内容核对照样做）")
    a = ap.parse_args()
    out = os.path.join(a.workdir, "out")
    wjp, mp = os.path.join(out, "words.json"), os.path.join(out, "merged.wav")
    if not os.path.isfile(wjp):
        _cli.report(next="python scripts/voice/align_words.py --workdir %s --yes" % a.workdir)
        _cli.fail(_cli.ERROR, "没有 %s：先运行 align_words.py --yes" % wjp)
    wj = json.load(open(wjp, encoding="utf-8"))
    words = wj["words"]
    if not _util.check_audio(mp, wj.get("audio_sha"), wj.get("total_seconds"), "逐词对齐",
                             "音频被改过就从合成重来：synth.py --yes，再跑 align_words.py --yes（本技能不做调速）。"):
        if not a.legacy:
            _cli.report(next="python scripts/voice/align_words.py --workdir %s --yes" % a.workdir)
            _cli.fail(_cli.ERROR, "words.json 里没有音频指纹：它不是现在的 align_words.py 写出来的（旧版本，或者被手工改过）。"
                      "重新运行 align_words.py --yes；确定是旧项目要沿用，加 --legacy。不要手工编辑时间文件。")
        print("注意：--legacy：words.json 没有音频指纹，只做内容核对。")
    tl = {}
    try:
        tl = json.load(open(os.path.join(out, "timeline.json"), encoding="utf-8"))
    except (OSError, ValueError):
        pass
    wf = wave.open(mp)
    sr, nfr = wf.getframerate(), wf.getnframes()
    audio = np.frombuffer(wf.readframes(nfr), np.int16)
    wf.close()
    total = nfr / sr
    pauses = _util.detect_pauses(mp, PAUSE_MIN)                  # 停顿以配音本身为准
    ratio, bad = mismatch(words, pauses)
    if ratio > MISMATCH_MAX:
        ex = "；".join("%.2f–%.2f 秒" % (p["start"], p["end"]) for p in bad[:3])
        _cli.report(mismatch_ratio=round(ratio, 2), next="python scripts/voice/align_words.py --workdir %s --yes" % a.workdir)
        _cli.fail(_cli.ERROR, "逐词时间和配音对不上：配音里的 %d 处停顿有 %d 处落在词中间（%.0f%%，正常不到 10%%），例如 %s。"
                  "words.json 不是这份 merged.wav 的对齐结果——重新运行 align_words.py --yes。不要手工编辑时间文件。"
                  % (len(pauses), len(bad), ratio * 100, ex))

    cuts, tier_used = [], 0
    if total > a.max:
        n_min = math.ceil(total / a.max)
        found = None
        for tier in (1, 2, 3):
            cands = candidates(words, pauses, tier)
            options = []
            for n in range(n_min, n_min + 6):
                r = best_cuts(total, n, cands, a.max)
                if r:
                    options.append((r[0] + EXTRA_SEG_W * (n - n_min), r[1]))
            if options:
                found = (min(options, key=lambda o: o[0])[1], tier)
                break
        if not found:
            _cli.fail(_cli.ERROR, "找不到合适的切点让每段 ≤%.0f 秒（总长 %.1f 秒）。请检查 words.json" % (a.max, total))
        cuts, tier_used = found
    pts = [0.0] + [c[0] for c in cuts] + [total]
    kinds = ["开头"] + [c[1] for c in cuts] + ["结尾"]
    puncts = [None] + [c[2] for c in cuts] + [None]

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
                     "start_kind": kinds[i], "end_kind": kinds[i + 1], "end_punct": puncts[i + 1], "audio": path,
                     "audio_sha": _util.file_sha(path), "text_asr": "".join(x["text"] for x in ws),
                     "words": [{"word": x["word"], "start": round(x["start"] - s, 3), "end": round(x["end"] - s, 3)} for x in ws]})
    stats = {k: sum(1 for c in cuts if c[2] == k) for k in RANK_COST}
    plan = {"max_seconds": a.max, "total_seconds": round(total, 3), "cut_tier": tier_used, "cut_punct": stats,
            "audio": mp, "audio_sha": _util.file_sha(mp), "segments": segs}
    json.dump(plan, open(os.path.join(od, "plan.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    C_ = {0: "整段，无需拆分", 1: "全部在停顿处断开", 2: "有切点用了句末标点（该档段数下找不到合适停顿）", 3: "有切点用了词边界（找不到停顿和标点）"}
    print("总长 %.1f 秒，上限 %.0f 秒 → %d 段（%s）；切点：句末 %d、逗号 %d、无标点 %d"
          % (total, a.max, len(segs), C_[tier_used], stats["句末"], stats["逗号"], stats["无标点"]))
    for s in segs:
        print("段%d：%.2f–%.2fs（%.1fs） 起于%s 止于%s%s  %s…" % (s["i"], s["start"], s["end"], s["duration"], s["start_kind"], s["end_kind"],
                                                       "（%s）" % s["end_punct"] if s["end_punct"] else "", s["text_asr"][:18]))
    print("计划已写入", os.path.join(od, "plan.json"))
    _cli.report(plan=os.path.join(od, "plan.json"), segments=len(segs), max_seconds=a.max, total_seconds=round(total, 3),
                cut_tier=tier_used, cut_punct=stats, mismatch_ratio=round(ratio, 2), durations=[s["duration"] for s in segs],
                next="写 avatar/prompt_src.json，再运行 python scripts/avatar/build_prompts.py --workdir %s" % a.workdir)


if __name__ == "__main__":
    _cli.run(main)
