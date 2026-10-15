"""分段（avatar/plan_segments.py）的离线测试：停顿以配音为准、切点优先句末、逐词时间和配音对不上会被拦下、缺指纹要 --legacy。
  python tests/avatar/test_plan_segments.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "..", "scripts")
PS = os.path.join(SCRIPTS, "avatar", "plan_segments.py")
sys.path.insert(0, SCRIPTS)
import _util  # noqa: E402

TMP = tempfile.mkdtemp(prefix="plan_test_")
SR = 48000
cases = []


def check(name, ok, extra=""):
    print("✓" if ok else "✗", name, "" if ok else extra)
    cases.append(bool(ok))


# 约 29 秒的配音：词与词之间 0.1 秒（不算停顿）。均分位置（约 9.7、19.4 秒）附近放没有标点的停顿（像换气），
# 离它们 1.5 秒左右放句末停顿，再放几处逗号停顿。好的分段应该选句末，而不是离均分最近的那两处换气。
SPECIAL = {8.0: ("。", 0.5), 9.7: ("", 0.45), 19.4: ("", 0.45), 21.0: ("。", 0.5), 4.0: ("，", 0.35), 13.5: ("，", 0.35), 25.0: ("，", 0.35)}


def build(wd, shift=0.0, sha=True):
    os.makedirs(os.path.join(wd, "out"), exist_ok=True)
    words, parts, t, k = [], [np.zeros(int(0.3 * SR))], 0.3, 0
    todo = sorted(SPECIAL)
    while t < 29.0:
        d = 0.5
        tt = np.arange(int(d * SR)) / SR
        parts.append(np.sin(tt * 2 * np.pi * (180 + 7 * (k % 9))) * 9000)
        punct, gap = "", 0.1
        if todo and t + d >= todo[0]:
            punct, gap = SPECIAL[todo.pop(0)]
        words.append({"seg": 1, "word": "词%d" % k, "text": "词%d%s" % (k, punct), "start": round(t, 3), "end": round(t + d, 3)})
        t += d
        parts.append(np.zeros(int(gap * SR)))
        t += gap
        k += 1
    parts.append(np.zeros(int(0.3 * SR)))
    x = np.concatenate(parts).astype(np.int16)
    mp = os.path.join(wd, "out", "merged.wav")
    w = wave.open(mp, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(x.tobytes())
    w.close()
    shifted = [dict(w, start=round(w["start"] + shift, 3), end=round(w["end"] + shift, 3)) for w in words]
    wj = {"words": shifted, "pauses": [{"start": w["start"] + 0.1, "end": w["end"] - 0.1} for w in shifted[5::7]],   # 故意写错的停顿：都在词中间
          "total_seconds": round(len(x) / SR, 3)}
    if sha:
        wj["audio_sha"] = _util.file_sha(mp)
    json.dump(wj, open(os.path.join(wd, "out", "words.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return words


def run(wd, *extra):
    r = subprocess.run([sys.executable, PS, "--workdir", wd, *extra, "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        j = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
    except ValueError:
        j = {}
    return r, j


wd = os.path.join(TMP, "good")
words = build(wd)
r, j = run(wd)
plan = json.load(open(os.path.join(wd, "avatar", "plan.json"), encoding="utf-8")) if r.returncode == 0 else {"segments": []}
cuts = [s["end"] for s in plan["segments"][:-1]]
print("   切点：", cuts, j.get("cut_punct"))
check("分段成功，每段不超过 14 秒", r.returncode == 0 and all(s["duration"] <= 14.0 + 1e-6 for s in plan["segments"]), r.stderr[-300:])
check("切点选句末的停顿（8 秒、21 秒附近），不选离均分最近的两处换气（9.7、19.4 秒）",
      len(cuts) == 2 and abs(cuts[0] - 8.3) < 0.6 and abs(cuts[1] - 21.3) < 0.6, cuts)
check("plan.json 记下每个切点的标点类型", [s.get("end_punct") for s in plan["segments"][:-1]] == ["句末", "句末"])
check("words.json 里故意写错的停顿（都在词中间）没被采用：切点都不在词中间",
      all(not (w["start"] + 0.03 < c < w["end"] - 0.03) for c in cuts for w in words))

wd2 = os.path.join(TMP, "shifted")
build(wd2, shift=0.5)
r, j = run(wd2)
check("逐词时间和配音错开 0.5 秒：退出码 1，说清楚要重新对齐，不出 plan.json",
      r.returncode == 1 and "对不上" in (j.get("error") or "") and "align_words" in j["error"] and not os.path.isfile(os.path.join(wd2, "avatar", "plan.json")),
      (j.get("error") or "")[:200])
check("机读结果里给出下一步要运行的命令", "align_words.py" in (j.get("next") or ""))

wd3 = os.path.join(TMP, "nosha")
build(wd3, sha=False)
r, j = run(wd3)
check("words.json 没有音频指纹（旧版本或手工改过）：退出码 1，提示重新对齐或加 --legacy", r.returncode == 1 and "--legacy" in (j.get("error") or ""))
r, j = run(wd3, "--legacy")
check("加 --legacy：照样做内容核对，正常分段", r.returncode == 0 and j.get("segments") == 3, (r.stderr[-200:], j.get("segments")))

print("\n通过 %d/%d" % (sum(cases), len(cases)))
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if all(cases) else 1)
