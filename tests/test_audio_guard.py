"""合成之后每一步核对音频指纹的离线测试（不联网、不需要密钥）。本技能不做调速，--speed 已删掉。
  python tests/test_audio_guard.py

覆盖：synth.py 不再接受 --speed；对齐之前、分段之前、提交之前、成片之前（手动）改过音频都会被拦下；
旧项目没有指纹时跳过核对、不误拦。
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
SCRIPTS = os.path.join(HERE, "..", "scripts")
VOICE, AVATAR = os.path.join(SCRIPTS, "voice"), os.path.join(SCRIPTS, "avatar")
TMP = tempfile.mkdtemp(prefix="audio_guard_")
os.environ["DH_WORKSPACE"] = TMP
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, VOICE)
import _util                 # noqa: E402
import common as C           # noqa: E402  （配音阶段的 common；数字人阶段的脚本都用子进程跑，避免同名模块冲突）
import synth as S            # noqa: E402
import align_words as A      # noqa: E402

cases = []


def check(name, ok, extra=""):
    print("✓" if ok else "✗", name, "" if ok else extra)
    cases.append(bool(ok))


def speech(path, n_words=6, word=1.2, gap=0.4, sr=C.SAMPLE_RATE):
    """像说话的假配音：一段音、一段静音交替"""
    parts = []
    for k in range(n_words):
        t = np.arange(int(word * sr)) / sr
        parts.append((np.sin(t * 2 * np.pi * (200 + 30 * k)) * 9000).astype(np.int16))
        parts.append(np.zeros(int(gap * sr), np.int16))
    S.write_wav(path, np.concatenate(parts[:-1]), sr)
    return path


def fake_transcribe(path, key, cache_dir=None):
    """假识别：按能量把每段有声音的地方当成一个词"""
    w = wave.open(path)
    sr = w.getframerate()
    x = np.abs(np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32))
    w.close()
    hop = sr // 100
    on = [x[i:i + hop].max() > 1000 for i in range(0, len(x), hop)]
    words, k = [], 0
    while k < len(on):
        if on[k]:
            j = k
            while j < len(on) and on[j]:
                j += 1
            words.append({"text": "词，", "word": "词", "begin": k / 100.0, "end": j / 100.0})
            k = j
        else:
            k += 1
    return {"text": "".join(x["text"] for x in words), "words": words}


S.synth_text = lambda text, voice, key, cache, label, depth=0: (FAKE[0] if "第一" in text else FAKE[1], [], True)
C.require_key = lambda: "k"
C.update_meta = lambda *a, **k: None
A.asr.transcribe = fake_transcribe
FAKE = [speech(os.path.join(TMP, "fa.wav")), speech(os.path.join(TMP, "fb.wav"), n_words=5)]


def in_proc(mod, argv):
    sys.argv = argv
    try:
        mod.main()
        return 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1


def sub(script, *args):
    env = dict(os.environ, DASHSCOPE_API_KEY="", USERPROFILE=TMP, HOME=TMP)
    r = subprocess.run([sys.executable, script, *args, "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    try:
        j = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
    except ValueError:
        j = {}
    return r, j


def speed_up(path, speed=1.1):
    tmp = path + ".fast.wav"
    subprocess.run([_util.ffmpeg_exe(), "-y", "-v", "error", "-i", path, "-filter:a", "atempo=%.2f" % speed,
                    "-ar", str(C.SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", tmp], check=True)
    os.replace(tmp, path)


def project(name):
    wd = os.path.join(TMP, name)
    os.makedirs(wd)
    json.dump({"voice_id": "voice-x"}, open(os.path.join(wd, "voice.json"), "w"))
    json.dump({"paragraphs": ["第一段。", "第二段。"]}, open(os.path.join(wd, "paragraphs.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return wd


# ---------------------------------------------------------------- synth：不做调速
w2 = project("p_voice")
check("synth 正常合成", in_proc(S, ["synth.py", "--workdir", w2, "--keep-fillers", "--yes"]) == 0)
t2 = json.load(open(os.path.join(w2, "out", "timeline.json"), encoding="utf-8"))
check("timeline.json 记下整条、每段音频的指纹，不再有倍速", t2.get("audio_sha") == _util.file_sha(os.path.join(w2, "out", "merged.wav"))
      and all(s.get("audio_sha") for s in t2["segments"]) and "speed" not in t2)
r = subprocess.run([sys.executable, os.path.join(VOICE, "synth.py"), "--workdir", w2, "--speed", "1.1"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
check("synth 不再接受 --speed（调速功能已删掉）", r.returncode == 2 and "--speed" in r.stderr, r.stderr[-200:])
r = subprocess.run([sys.executable, os.path.join(HERE, "..", "scripts", "pipeline.py"), "voice", "--workdir", w2, "--speed", "1.1"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
check("pipeline voice 不再接受 --speed", r.returncode == 2 and "--speed" in r.stderr, r.stderr[-200:])

# ---------------------------------------------------------------- 合成之后、对齐之前改音频
wd = w2
out = os.path.join(wd, "out")
mp = os.path.join(out, "merged.wav")
keep = os.path.join(TMP, "merged_keep.wav")
shutil.copy2(mp, keep)
speed_up(mp)
r, j = sub(os.path.join(VOICE, "align_words.py"), "--workdir", wd)
check("合成后手动加速 merged.wav：align_words 拒绝（退出码 1，说清楚要从合成重来、本技能不做调速）",
      r.returncode == 1 and "被改过" in (j.get("error") or "") and "像是被调过速" in j["error"] and "不做调速" in j["error"]
      and "--speed" not in j["error"], r.stderr[-300:])
shutil.copy2(keep, mp)
seg1 = os.path.join(out, "seg_01.wav")
seg_keep = os.path.join(TMP, "seg1_keep.wav")
shutil.copy2(seg1, seg_keep)
speed_up(seg1)
r, j = sub(os.path.join(VOICE, "align_words.py"), "--workdir", wd)
check("合成后手动加速 out/seg_01.wav：align_words 也拒绝", r.returncode == 1 and "seg_01.wav" in (j.get("error") or ""))
shutil.copy2(seg_keep, seg1)

# ---------------------------------------------------------------- 对齐
check("音频没动过：align_words 正常对齐", in_proc(A, ["align_words.py", "--workdir", wd, "--yes"]) == 0)
wj = json.load(open(os.path.join(out, "words.json"), encoding="utf-8"))
check("words.json 记下音频指纹和真实总长", wj.get("audio_sha") == _util.file_sha(mp) and "speed" not in wj
      and abs(wj["total_seconds"] - _util.wav_seconds(mp)) < 0.01)

# ---------------------------------------------------------------- 对齐之后、分段之前改音频
speed_up(mp)
r, j = sub(os.path.join(AVATAR, "plan_segments.py"), "--workdir", wd)
check("对齐后手动加速 merged.wav：plan_segments 拒绝，不产生 plan.json",
      r.returncode == 1 and "逐词对齐之后被改过" in (j.get("error") or "") and not os.path.isfile(os.path.join(wd, "avatar", "plan.json")), r.stderr[-300:])
shutil.copy2(keep, mp)

# ---------------------------------------------------------------- 分段
r, j = sub(os.path.join(AVATAR, "plan_segments.py"), "--workdir", wd)
plan = json.load(open(os.path.join(wd, "avatar", "plan.json"), encoding="utf-8")) if r.returncode == 0 else {}
check("音频没动过：plan_segments 正常分段，记下整条和每段的指纹",
      r.returncode == 0 and plan.get("audio_sha") == _util.file_sha(mp) and "speed" not in plan
      and all(s.get("audio_sha") == _util.file_sha(s["audio"]) for s in plan["segments"]) and len(plan["segments"]) >= 2, r.stderr[-300:])
json.dump({"width": 512, "height": 512}, open(os.path.join(wd, "avatar", "image_info.json"), "w"))

# ---------------------------------------------------------------- 分段之后改音频（别的 agent 最容易犯的：先分段、再加速）
seg2 = plan["segments"][1]["audio"]
seg2_keep = os.path.join(TMP, "avatar_seg2_keep.wav")
shutil.copy2(seg2, seg2_keep)
speed_up(seg2)
r, j = sub(os.path.join(AVATAR, "run_avatar.py"), "--workdir", wd)
check("分段后手动加速 avatar/seg_02.wav：run_avatar 连预览都不给（退出码 1），更不会提交",
      r.returncode == 1 and "分段之后被改过" in (j.get("error") or "") and "像是被调过速" in j["error"]
      and not os.path.isfile(os.path.join(wd, "avatar", "result.json")), r.stderr[-300:])
shutil.copy2(seg2_keep, seg2)
speed_up(mp)
r, j = sub(os.path.join(AVATAR, "run_avatar.py"), "--workdir", wd)
check("分段后手动加速 merged.wav：run_avatar 也拒绝", r.returncode == 1 and "merged.wav" in (j.get("error") or ""))
os.makedirs(os.path.join(wd, "avatar", "clips"), exist_ok=True)
json.dump({"results": {}}, open(os.path.join(wd, "avatar", "result.json"), "w"))
r, j = sub(os.path.join(AVATAR, "finalize.py"), "--workdir", wd)
check("分段后手动加速 merged.wav：finalize 拒绝拼成片", r.returncode == 1 and "分段之后被改过" in (j.get("error") or ""), r.stderr[-300:])
shutil.copy2(keep, mp)
r, j = sub(os.path.join(AVATAR, "run_avatar.py"), "--workdir", wd)
check("音频改回原样：run_avatar 通过核对（往下走到“缺少提示词”才停）", r.returncode == 1 and "缺少提示词" in (j.get("error") or ""), r.stderr[-300:])

# ---------------------------------------------------------------- 旧项目：没有指纹就跳过，不误拦
for s in plan["segments"]:
    s.pop("audio_sha")
plan.pop("audio_sha")
json.dump(plan, open(os.path.join(wd, "avatar", "plan.json"), "w", encoding="utf-8"), ensure_ascii=False)
speed_up(seg2)
r, j = sub(os.path.join(AVATAR, "run_avatar.py"), "--workdir", wd)
check("没有指纹的 plan.json（旧版本或手工改过）：不再默默放过，退出码 1，提示重新分段或加 --legacy",
      r.returncode == 1 and "没有音频指纹" in (j.get("error") or "") and "--legacy" in j["error"], r.stdout[-300:])
r, j = sub(os.path.join(AVATAR, "run_avatar.py"), "--workdir", wd, "--legacy")
check("旧项目加 --legacy：跳过指纹核对并提示，往下走", r.returncode == 1 and "缺少提示词" in (j.get("error") or "") and "跳过" in r.stdout, r.stdout[-300:])

print("\n通过 %d/%d" % (sum(cases), len(cases)))
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if all(cases) else 1)
