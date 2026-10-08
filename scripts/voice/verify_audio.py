"""核对合成结果：文字有没有读错，以及各段的语速/音高是否一致。

  python verify_audio.py --workdir <项目目录>

文字核对用百炼 qwen-audio-3.1-asr-flash：把每段音频识别回文字，和原文比较（去标点、统一数字写法后）。
需要先有 out/words.json（由 align_words.py 生成，里面存了每段的识别文字）；没有会自动先跑一遍。
音高需要 librosa，没装就跳过这一项并说明。
一致性判断是经验阈值（语速偏差 >25%、音高均值偏差 >25 Hz 会标出），只是提示，最终以耳朵为准。
"""
import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import asr


def wav_info(p):
    w = wave.open(p)
    try:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        return x, w.getframerate()
    finally:
        w.close()


def f0_mean(x, sr):
    try:
        import librosa
    except Exception:
        return None
    y = librosa.resample(x, orig_sr=sr, target_sr=16000) if sr != 16000 else x
    f0, _, _ = librosa.pyin(y, fmin=65, fmax=400, sr=16000, frame_length=1024)
    f = f0[~np.isnan(f0)]
    return float(f.mean()) if len(f) else None


def active_seconds(x, sr):
    hop = int(sr * 0.02)
    rms = np.sqrt(np.array([np.mean(x[i:i + hop * 2] ** 2) for i in range(0, len(x) - hop * 2, hop)]))
    if not len(rms):
        return 0.0
    return float((rms > 0.05 * np.percentile(rms, 95)).sum() * 0.02)


TAG_WORDS = ["excited", "happy", "sad", "curious", "angry", "surprised", "calm", "sleepy", "gentle"]


def spoken_tags(heard, want, extra=()):
    """情绪标签有时会被原样念出来（如句中的 [happy] 被念成 Happy），而文字相似度几乎不受影响，所以单独查：
    识别文字里某个标签单词出现的次数比原文多，就算被念出来了。返回 [(单词, 听到次数, 原文次数)]。"""
    out = []
    for w in list(dict.fromkeys(list(TAG_WORDS) + [x for x in extra if x])):
        pat = r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(w)
        n1, n2 = len(re.findall(pat, heard, re.I)), len(re.findall(pat, want, re.I))
        if n1 > n2:
            out.append((w, n1, n2))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--out", default="out", help="结果文件夹名（--variant 出的版本用 out_<名字>）")
    a = ap.parse_args()
    out = os.path.join(a.workdir, a.out)
    wj = os.path.join(out, "words.json")
    if not os.path.isfile(wj):
        print("没有 words.json，先做逐词对齐……")
        r = subprocess.run([sys.executable, os.path.join(HERE, "align_words.py"), "--workdir", a.workdir, "--out", a.out])
        if r.returncode != 0:
            sys.exit(r.returncode)
    words = json.load(open(wj, encoding="utf-8"))
    tl = json.load(open(os.path.join(out, "timeline.json"), encoding="utf-8"))
    heard = {s["i"]: s["asr_text"] for s in words["segments"]}
    rows = []
    for s in tl["segments"]:
        x, sr = wav_info(os.path.join(out, "seg_%02d.wav" % s["i"]))
        act = active_seconds(x, sr)
        h, w = asr.norm_text(heard.get(s["i"], "")), asr.norm_text(s["text"])
        rows.append({"i": s["i"], "chars": s["chars"], "seconds": round(len(x) / sr, 1),
                     "chars_per_sec": round(s["chars"] / act, 2) if act else None, "f0": f0_mean(x, sr),
                     "match": round(difflib.SequenceMatcher(None, h, w).ratio(), 3), "heard_len": len(h), "want_len": len(w)})
    print("段  字数  时长s  字/秒(有声)  音高Hz  文字相似度")
    for r in rows:
        print("%-3d %-5d %-6s %-12s %-7s %.3f（识别%d/原文%d字）" % (
            r["i"], r["chars"], r["seconds"], r["chars_per_sec"], "-" if r["f0"] is None else "%.0f" % r["f0"],
            r["match"], r["heard_len"], r["want_len"]))
    flags = []
    extra = []
    ej = os.path.join(a.workdir, "emotions.json")
    if os.path.isfile(ej):
        extra = [re.sub(r"[\[\]]", "", t.get("tag", "")) for t in json.load(open(ej, encoding="utf-8")).get("tags", [])]
    for s in tl["segments"]:
        for w, n1, n2 in spoken_tags(heard.get(s["i"], ""), s["text"], extra):
            flags.append("段%d 里情绪标签被念出来了：%s（听到 %d 次，原文 %d 次）——去掉这一处标签后重合成这一段" % (s["i"], w, n1, n2))
    import fillers
    for s in tl["segments"]:                # 标签引出来的多余语气词（嘿、哼、哎……）：合并前会自动剪，这里查还有没有漏网的
        ws = [{"word": w["word"], "begin": w["start"], "end": w["end"]} for w in words["words"] if w.get("seg") == s["i"]]
        for g in fillers.find_inserted(ws, s["text"])[0]:
            flags.append("段%d 的 %.1f 秒处多出一声“%s”（原文没有，多半是句首情绪标签引出来的）——默认的合成流程会自动剪掉；这一处是没剪干净或用了 --keep-fillers，请用户听一下" % (s["i"], g["begin"], g["text"]))
    for r in rows:
        if r["match"] < 0.9:
            flags.append("段%d 文字相似度 %.2f，可能读错或漏读，请听这一段" % (r["i"], r["match"]))
    rates = [r["chars_per_sec"] for r in rows if r["chars_per_sec"]]
    if len(rates) > 1:
        med = float(np.median(rates))
        for r in rows:
            if r["chars_per_sec"] and abs(r["chars_per_sec"] - med) / med > 0.25:
                flags.append("段%d 语速 %.1f 字/秒，偏离中位数 %.1f 超过 25%%" % (r["i"], r["chars_per_sec"], med))
    f0s = [r["f0"] for r in rows if r["f0"]]
    if len(f0s) > 1:
        med = float(np.median(f0s))
        for r in rows:
            if r["f0"] and abs(r["f0"] - med) > 25:
                flags.append("段%d 音高均值 %.0f Hz，偏离中位数 %.0f Hz 超过 25 Hz" % (r["i"], r["f0"], med))
    elif not f0s:
        flags.append("音高一致性：未检查（未安装 librosa）")
    print()
    print("需要注意：" if flags else "没有发现需要标出的问题（仍建议试听）。")
    for f in flags:
        print(" -", f)
    json.dump({"rows": rows, "flags": flags}, open(os.path.join(out, "verify.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
