"""拼接（avatar/finalize.py）的离线测试：每段开头裁掉几帧、同时裁掉同样长的开头静音，口型不错位；后面的步骤按成片时间轴换算。
  python tests/avatar/test_finalize.py
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
FIN = os.path.join(SCRIPTS, "avatar", "finalize.py")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(SCRIPTS, "avatar"))
import _util          # noqa: E402
import common as AC   # noqa: E402

TMP = tempfile.mkdtemp(prefix="finalize_test_")
SR, FPS = 48000, 24
cases = []


def check(name, ok, extra=""):
    print("✓" if ok else "✗", name, "" if ok else extra)
    cases.append(bool(ok))


# 三段配音：每段 = 开头静音 lead + 一段音 2.4 秒 + 结尾静音 0.3 秒。第 3 段开头静音只有 0.2 秒（只能裁 2 帧）
LEADS = [0.1, 0.3, 0.2]
TONE, TAIL = 2.4, 0.3


def build_project(wd):
    os.makedirs(os.path.join(wd, "avatar", "clips"))
    os.makedirs(os.path.join(wd, "out"))
    parts, segs, words, t = [], [], [], 0.0
    for k, lead in enumerate(LEADS):
        tt = np.arange(int(TONE * SR)) / SR
        parts += [np.zeros(int(lead * SR)), np.sin(tt * 2 * np.pi * (200 + 80 * k)) * 9000, np.zeros(int(TAIL * SR))]
        d = lead + TONE + TAIL
        segs.append({"i": k + 1, "start": round(t, 3), "end": round(t + d, 3), "duration": round(d, 3), "start_kind": "停顿", "end_kind": "停顿",
                     "audio": "", "words": [{"word": "词%d" % k, "start": lead, "end": lead + TONE}]})
        words.append({"word": "词%d" % k, "text": "词%d" % k, "start": round(t + lead, 3), "end": round(t + lead + TONE, 3)})
        t += d
    x = np.concatenate(parts).astype(np.int16)
    w = wave.open(os.path.join(wd, "out", "merged.wav"), "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(x.tobytes())
    w.close()
    res = {}
    for s in segs:                                     # 云端片段：比音频长一点，画面在动
        p = os.path.join(wd, "avatar", "clips", "seg_%02d.mp4" % s["i"])
        subprocess.run([_util.ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=256x448:rate=24:duration=%.3f" % (s["duration"] + 0.3), "-c:v", "libx264", "-pix_fmt", "yuv420p", p], check=True)
        res[str(s["i"])] = {"file": p}
    json.dump({"max_seconds": 14, "total_seconds": round(t, 3), "segments": segs}, open(os.path.join(wd, "avatar", "plan.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"width": 256, "height": 448}, open(os.path.join(wd, "avatar", "image_info.json"), "w"))
    json.dump({"results": res}, open(os.path.join(wd, "avatar", "result.json"), "w"))
    json.dump({"words": words, "pauses": [{"start": round(segs[1]["start"] - TAIL, 3), "end": round(segs[1]["start"] + LEADS[1], 3)}],
               "total_seconds": round(t, 3)}, open(os.path.join(wd, "out", "words.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return segs, t


def run(*args):
    # 手搓的分段计划没有音频指纹，所以加 --legacy（指纹核对见 test_audio_guard.py）
    r = subprocess.run([sys.executable, FIN, *args, "--legacy", "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        j = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
    except ValueError:
        j = {}
    return r, j


def onsets(video):
    wav = video + ".wav"
    subprocess.run([_util.ffmpeg_exe(), "-y", "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", wav], check=True)
    w = wave.open(wav)
    x = np.abs(np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32))
    w.close()
    hop = SR // 100
    loud = np.array([x[i:i + hop].mean() > 1500 for i in range(0, len(x) - hop, hop)])
    return [j / 100 for j in range(1, len(loud)) if loud[j] and not loud[j - 1]], len(x) / SR


wd = os.path.join(TMP, "proj")
segs, total = build_project(wd)
r, j = run("--workdir", wd)
od = os.path.join(wd, "avatar")
check("拼接成功，检查项都过", r.returncode == 0 and all(c["ok"] for c in j.get("checks", [])), r.stderr[-400:])
check("每段开头裁掉的帧：第 1 段不裁，开头静音够长的裁 4 帧，只有 0.2 秒静音的只裁 2 帧（不剪到字头）",
      j.get("head_frames") == [0, 4, 2], j.get("head_frames"))
want = total - 6 / FPS
check("成片时长 = 配音时长 - 裁掉的开头", abs(j.get("final_seconds", 0) - want) < 1e-3 and abs((_util.media_seconds(os.path.join(od, "final_avatar.mp4")) or 0) - want) < 0.06,
      (j.get("final_seconds"), want))
fin = json.load(open(os.path.join(od, "finalize.json"), encoding="utf-8"))
ons, _ = onsets(os.path.join(od, "final_avatar.mp4"))
exp = [fin["segments"][k]["start"] + LEADS[k] - fin["segments"][k]["head"] for k in range(3)]
check("声音也裁掉了同样长的开头静音：每段开口的时刻和换算出来的一致（口型不错位）",
      len(ons) == 3 and all(abs(a - b) < 0.03 for a, b in zip(ons, exp)), (ons, exp))
wf = json.load(open(os.path.join(od, "words_final.json"), encoding="utf-8"))
check("words_final.json：逐词时间换算到成片时间轴", all(abs(w["start"] - e) < 0.002 for w, e in zip(wf["words"], exp)), [w["start"] for w in wf["words"]])
check("words_final.json：裁掉的开头静音从停顿里扣掉", abs((wf["pauses"][0]["end"] - wf["pauses"][0]["start"]) - (TAIL + LEADS[1] - 4 / FPS)) < 0.002, wf["pauses"])
vf = os.path.join(od, "voice_final.wav")
check("voice_final.wav：和成片等长，开口时刻和成片一致（后期包装用）", os.path.isfile(vf) and abs(_util.wav_seconds(vf) - want) < 0.01)
fs = AC.final_segments(od, json.load(open(os.path.join(od, "plan.json"), encoding="utf-8")))
check("final_segments：各段在成片上的起止和 finalize.json 一致", [round(s["start"], 3) for s in fs] == [round(s["start"], 3) for s in fin["segments"]]
      and fs[1]["head"] == round(4 / FPS, 4))

import qc_frames as Q   # noqa: E402
Q.run(wd, quiet=True)
qc = json.load(open(os.path.join(od, "qc_frames.json"), encoding="utf-8"))
check("qc_frames：接缝位置按成片时间轴（不是配音时间轴）", [s["cut"] for s in qc["seams"]] == [round(s["end"], 2) for s in fin["segments"][:-1]],
      ([s["cut"] for s in qc["seams"]], [s["end"] for s in fin["segments"]]))

r, j = run("--workdir", wd, "--head-frames", "0")
check("--head-frames 0：和以前一样不裁，成片时长等于配音", r.returncode == 0 and j.get("head_frames") == [0, 0, 0] and abs(j.get("final_seconds", 0) - total) < 1e-3)

# ---------------------------------------------------------------- 片段比音频短：点名停下
p2 = os.path.join(od, "clips", "seg_02.mp4")
subprocess.run([_util.ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=256x448:rate=24:duration=%.3f" % (segs[1]["duration"] - 0.5),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", p2], check=True)
r, j = run("--workdir", wd)
check("某段视频比音频短：退出码 1，点名是第 2 段，next 给出重跑这一段的命令",
      r.returncode == 1 and "段2" in (j.get("error") or "") and "--only 2" in (j.get("next") or "") and [x["i"] for x in j.get("short_clips", [])] == [2],
      (j.get("error"), j.get("next")))

print("\n通过 %d/%d" % (sum(cases), len(cases)))
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if all(cases) else 1)
