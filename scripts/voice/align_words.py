"""给合成好的配音打逐词时间：<workdir>/out/words.json（时间都在 merged.wav 的时间轴上，单位秒）。

  python align_words.py --workdir <项目目录>

做法：对每一段 seg_NN.wav 并发调用百炼 qwen-audio-3.1-asr-flash（最多 6 路），再按 timeline.json 里每段的
起点和首部裁剪量换算到合并后的时间轴。结果同时给出"停顿"（用音频能量检测，连续 ≥0.25 秒的低能量段），
供后续分段（只在停顿处断开）和提示词动作编排使用。
注意：识别返回的词首尾相接，停顿会被算进前一个词，所以停顿不能从词的间隙里找；本脚本已把被停顿拉长的词收短。

精度：与另一套本地识别工具对比，词首平均相差 67 毫秒、最大 150 毫秒（2026-10-03，一条 21 秒配音）。
返回的是"词"级而非字级；词内部的字如需时间，按词长平均分。
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _cli  # noqa: E402
import _util  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asr
import common as C

PAUSE_MIN = 0.25


def detect_pauses(wav_path):
    """合并配音里的停顿：连续 ≥PAUSE_MIN 秒的低能量段（低于 95 分位音量的 5%），开头结尾的静音不算。
    实现在 scripts/_util.py。"""
    return _util.detect_pauses(wav_path, PAUSE_MIN)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--out", default="out", help="结果文件夹名（--variant 出的版本用 out_<名字>）")
    ap.add_argument("--yes", action="store_true", help="确认识别（联网，会产生少量费用）。不加就只预览")
    ap.add_argument("--legacy", action="store_true", help="旧项目：timeline.json 里没有音频指纹也继续")
    a = ap.parse_args()
    out = os.path.join(a.workdir, a.out)
    tp = os.path.join(out, "timeline.json")
    if not os.path.isfile(tp):
        _cli.fail(_cli.ERROR, "没有 %s：先运行 synth.py --yes 合成配音" % tp)
    tl = json.load(open(tp, encoding="utf-8"))
    redo = "音频被改过就从合成重来：synth.py --yes（本技能不做调速）。"
    checked = _util.check_audio(os.path.join(out, "merged.wav"), tl.get("audio_sha"), tl.get("total_seconds"), "合成", redo)
    for s in tl["segments"]:
        _util.check_audio(os.path.join(out, "seg_%02d.wav" % s["i"]), s.get("audio_sha"), None, "合成", redo)
    if not checked:
        if not a.legacy:
            _cli.report(next="python scripts/voice/synth.py --workdir %s --yes" % a.workdir)
            _cli.fail(_cli.ERROR, "timeline.json 里没有音频指纹：它不是现在的 synth.py 写出来的（旧版本，或者被手工改过）。"
                      "重新运行 synth.py --yes（有缓存，几秒钟）；确定是旧项目要沿用，加 --legacy。不要手工编辑时间文件。")
        print("注意：--legacy：timeline.json 没有音频指纹，跳过“音频是否被改过”的核对。")
    if not a.yes:
        print("[预览] 要对 %d 段配音做语音识别（每段一次请求，联网，费用很少），得到逐词时间。确认后加 --yes。" % len(tl["segments"]))
        _cli.report(preview=True, segments=len(tl["segments"]), total_seconds=tl.get("total_seconds"))
        return
    key = C.require_key()
    cache = os.path.join(a.workdir, "cache")
    os.makedirs(cache, exist_ok=True)
    segs = tl["segments"]

    def one(s):
        return asr.transcribe(os.path.join(out, "seg_%02d.wav" % s["i"]), key, cache)

    errors, res = [], {}
    with ThreadPoolExecutor(max_workers=max(1, min(C.WORKERS, len(segs)))) as ex:
        futs = {ex.submit(one, s): s for s in segs}
        for f, s in futs.items():
            try:
                res[s["i"]] = f.result()
            except PermissionError as e:
                print(str(e))
                sys.exit(_cli.KEY_REJECTED)
            except Exception as e:
                errors.append("段%d：%s" % (s["i"], e))
    if errors:
        print("识别失败：")
        for e in errors:
            print("  ", e)
        _cli.report(failed_segments=errors)
        sys.exit(_cli.REMOTE_FAILED)

    words, seg_info = [], []
    for s in segs:
        r = res[s["i"]]
        shift = s["start"] - s.get("trim_start", 0.0)      # seg 内时间 -> 合并轴
        for w in r["words"]:
            b = max(s["start"], w["begin"] + shift)
            e = max(b, min(s["end"], w["end"] + shift))
            words.append({"seg": s["i"], "text": w["text"], "word": w["word"], "start": round(b, 3), "end": round(e, 3)})
        seg_info.append({"i": s["i"], "start": s["start"], "end": s["end"], "text": s["text"], "asr_text": r["text"]})
    # 识别返回的词是首尾相接的，说话中的停顿会被算进前一个词里，所以停顿改用音频能量检测，
    # 再把被停顿拉长的词收短到停顿开始处。
    pauses = detect_pauses(os.path.join(out, "merged.wav"))
    for ps in pauses:
        for w in words:
            if w["start"] < ps["start"] < w["end"] and ps["end"] <= w["end"] + 0.06:
                w["end"] = round(ps["start"], 3)
        prev = [w for w in words if w["start"] < ps["start"] - 0.01]
        nxt = [w for w in words if w["start"] >= ps["end"] - 0.01]
        ps["after"] = prev[-1]["word"] if prev else None
        ps["before"] = nxt[0]["word"] if nxt else None
    mp = os.path.join(out, "merged.wav")
    json.dump({"unit": "seconds", "model": C.ASR_MODEL, "total_seconds": round(_util.wav_seconds(mp), 3),
               "audio_sha": _util.file_sha(mp),
               "segments": seg_info, "words": words, "pauses": pauses},
              open(os.path.join(out, "words.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if a.out == "out":
        C.update_meta(a.workdir, words=os.path.join(out, "words.json"))
    print("逐词时间已写入 %s：%d 个词，%d 处停顿（≥%.2fs）" % (os.path.join(out, "words.json"), len(words), len(pauses), PAUSE_MIN))
    _cli.report(words_json=os.path.join(out, "words.json"), words=len(words), pauses=len(pauses), total_seconds=tl["total_seconds"])


if __name__ == "__main__":
    _cli.run(main)
