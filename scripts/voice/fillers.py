"""去掉情绪标签引出来的多余语气词（嘿、哼、哎、哈……）。

合成时句首的情绪标签（尤其 [happy]、[curious]）有时会让模型在句子前面多"呼"出一声，文案里并没有这个字。
做法：对每一段合成结果做一次识别，和原文逐字比对，找出"只多不少"的语气词；
再按音频能量找到这一声的起止，把它连同多出来的停顿剪掉；最后再识别一遍确认：语气词没了、原文的字一个没少。
剪不干净（验证没通过）就保留原样并报告，不冒险。

识别给的词边界有 0.1–0.2 秒的误差，所以只用它定位"大概在哪"，起止点都落在音频能量的静音处或谷底。
"""
import difflib
import hashlib
import json
import os
import re

import numpy as np

import asr
import common as C

# 只处理这些字：语气词、叹词。文案里本来就有的不会动（比对只看"多出来"的部分）
FILLERS = set("哎嘿哼哈呵嗯哦喝唉哟嘻咳呃额啊呀哇噢喔嘘嗨")
GAP_CAP = 0.5        # 剪掉一声之后，前后停顿合计最多保留多少秒
PRE_KEEP = 0.30      # 其中，这一声前面的静音最多保留多少秒
HOP = 0.01


def find_inserted(heard_words, want_text):
    """识别结果里"原文没有的语气词"。返回 [{"i": 词序号, "text": 字, "begin", "end"}]，以及原文里没被听到的字数。"""
    want = asr.norm_text(want_text)
    chars, owner = [], []
    for k, w in enumerate(heard_words):
        t = asr.norm_text(w["word"])
        chars.extend(t)
        owner.extend([k] * len(t))
    heard = "".join(chars)
    sm = difflib.SequenceMatcher(None, want, heard, autojunk=False)
    found, lost = [], 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "delete":                 # 只数整个听不到的字；同音字、数字写法这类"替换"是识别误差，每次都会抖，不算
            lost += i2 - i1
        if op == "insert" and all(c in FILLERS for c in heard[j1:j2]):
            for k in sorted(set(owner[j1:j2])):
                w = heard_words[k]
                if all(c in FILLERS for c in asr.norm_text(w["word"])):
                    found.append({"i": k, "text": w["word"], "begin": w["begin"], "end": w["end"]})
    return found, lost


def _env(x):
    n = int(HOP * C.SAMPLE_RATE)
    m = len(x) // n
    e = np.sqrt((x[:m * n].astype(np.float32).reshape(m, n) ** 2).mean(1))
    return np.convolve(e, np.ones(3) / 3, mode="same")


def _candidates(env, begin, end, thr):
    """这一声可能的起点、终点（都落在能量低谷或静音处）。返回按"剪得越短越先试"排好的 (起点, 终点) 帧号。

    句首多出来的一声前面有静音，起点就是这段静音的末尾；问句后面拖出来的尾音（"什么意思啊"）前面没有静音，
    起点只能是词与词之间的能量低谷。识别给的位置只是个大概，所以起点终点各给几个，剪完用识别验证来选。"""
    n = len(env)
    f = min(n - 1, int(begin / HOP))
    p95 = np.percentile(env, 95)
    starts = []
    a = f
    while a > 0 and env[a] >= thr:       # 从识别给的位置往回找到能量降下来的地方
        a -= 1
    if (f - a) * HOP <= 0.5:
        starts.append(a)
    lo, hi = max(1, f - int(0.35 / HOP)), min(n - 2, f + int(0.05 / HOP))
    for i in range(lo, hi):
        if env[i] <= env[i - 1] and env[i] <= env[i + 1] and env[i] == env[max(0, i - 2):i + 3].min():
            starts.append(i)
    top = min(n - 2, int((max(end, begin) + 0.5) / HOP))
    ends = []
    for i in range(max(1, f - int(0.1 / HOP)), top):
        if env[i] <= env[i - 1] and env[i] <= env[i + 1] and env[i] < 0.12 * p95 and env[i] == env[max(0, i - 2):i + 3].min():
            ends.append(i)
    starts = _runs_edge(sorted(set(starts)), last=True)        # 连成一片的静音只留一个点：起点留最靠后的，终点留最靠前的
    ends = _runs_edge(ends, last=False)
    pairs = sorted({(a, b) for a in starts for b in ends
                    if 0.05 <= (b - a) * HOP <= 1.2 and a * HOP <= begin + 0.05 and b * HOP >= begin + 0.08}, key=lambda ab: ab[1] - ab[0])
    return pairs[:10]


def _runs_edge(idx, last):
    out, run = [], []
    for i in idx:
        if run and i != run[-1] + 1:
            out.append(run[-1] if last else run[0]); run = []
        run.append(i)
    if run:
        out.append(run[-1] if last else run[0])
    return out


def _cut(x, env, a, b, thr):
    """剪掉 [a, b) 帧：把它前后的静音收紧到合计 GAP_CAP 秒以内。返回新音频。"""
    sr = C.SAMPLE_RATE
    pre_start = a
    while pre_start > 0 and env[pre_start - 1] < thr:
        pre_start -= 1
    post_end = b
    while post_end < len(env) - 1 and env[post_end] < thr:
        post_end += 1
    pre_len, post_len = (a - pre_start) * HOP, (post_end - b) * HOP
    pre_keep = min(pre_len, PRE_KEEP) if pre_start > 0 else 0.0
    post_keep = min(post_len, max(0.1, GAP_CAP - pre_keep)) if post_len > 0 else 0.0
    s0 = int((pre_start + 0) * HOP * sr + pre_keep * sr)
    s1 = int(post_end * HOP * sr - post_keep * sr)
    s1 = max(s1, int(b * HOP * sr))
    left, right = x[:s0].astype(np.float32), x[s1:].astype(np.float32)
    f = int(0.01 * sr)
    if len(left) >= f and len(right) >= f:               # 接口处做 10ms 淡出淡入，不留爆音
        left[-f:] *= np.linspace(1, 0, f)
        right[:f] *= np.linspace(0, 1, f)
    return np.concatenate([left, right]).astype(np.int16)


def clean(wav_path, want_text, key, cache_dir, label, write_wav, read_wav):
    """处理一段合成结果。返回 (用来拼接的 wav 路径, 说明列表)。没有多余语气词就原样返回。"""
    base = os.path.splitext(os.path.basename(wav_path))[0]
    rec = os.path.join(cache_dir, base + ".fillers.json")
    out = os.path.join(cache_dir, base + ".clean.wav")
    if os.path.isfile(rec):
        r = json.load(open(rec, encoding="utf-8"))
        return (out if r["status"] == "ok" else wav_path), r["notes"]
    res = asr.transcribe(wav_path, key, cache_dir)
    found, lost0 = find_inserted(res["words"], want_text)
    if not found:
        json.dump({"status": "none", "notes": []}, open(rec, "w", encoding="utf-8"), ensure_ascii=False)
        return wav_path, []
    x, sr = read_wav(wav_path)
    notes, any_done = [], False
    for f in sorted(found, key=lambda f: -f["begin"]):          # 从后往前剪，前面的时间不受影响
        env = _env(x)
        thr = max(1.0, 0.04 * np.percentile(env, 95))
        pairs = _candidates(env, f["begin"], f["end"], thr)
        where = "%.1f 秒处“%s”" % (f["begin"], f["text"])
        done = False
        for a, b in pairs:
            y = _cut(x, env, a, b, thr)
            tmp = os.path.join(cache_dir, base + ".try.wav")
            write_wav(tmp, y, sr)
            again = asr.transcribe(tmp, key, cache_dir)
            left, lost1 = find_inserted(again["words"], want_text)
            if len(left) < len(found) and lost1 <= lost0:
                x, found, lost0, done = y, left, lost1, True
                break
        os.path.isfile(os.path.join(cache_dir, base + ".try.wav")) and os.remove(os.path.join(cache_dir, base + ".try.wav"))
        notes.append("%s：%s" % (label, where + ("，已剪掉" if done else "，没剪干净，保留原样")))
        any_done |= done
    if any_done:
        write_wav(out, x, sr)
    json.dump({"status": "ok" if any_done else "partial", "notes": notes}, open(rec, "w", encoding="utf-8"), ensure_ascii=False)
    return (out if any_done else wav_path), notes
