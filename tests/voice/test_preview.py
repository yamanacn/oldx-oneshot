"""配音阶段“默认只预览、加 --yes 才花钱”的离线测试（不联网、不需要密钥）。  python tests/voice/test_preview.py"""
import json
import os
import subprocess
import sys
import tempfile
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
VOICE = os.path.join(HERE, "..", "..", "scripts", "voice")
cases = []


def check(name, ok, extra=""):
    print("✓" if ok else "✗", name, extra)
    cases.append(bool(ok))


def run(script, *args, home=None):
    env = dict(os.environ)
    env["DH_WORKSPACE"] = home or tempfile.mkdtemp()
    env["DASHSCOPE_API_KEY"] = ""                       # 确保拿不到真密钥：预览不该需要密钥
    env["USERPROFILE"] = env["HOME"] = tempfile.mkdtemp()
    r = subprocess.run([sys.executable, os.path.join(VOICE, script), *args, "--json"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    last = (r.stdout.strip().splitlines() or ["{}"])[-1]
    try:
        j = json.loads(last)
    except ValueError:
        j = {}
    return r, j


def write_wav(path, seconds, sr=16000):
    x = (np.sin(np.arange(int(seconds * sr)) / sr * 2 * np.pi * 220) * 8000).astype(np.int16)
    w = wave.open(path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes(x.tobytes())
    w.close()


wd = tempfile.mkdtemp()
json.dump({"voice_id": "test-voice"}, open(os.path.join(wd, "voice.json"), "w", encoding="utf-8"))
json.dump({"paragraphs": ["第一段话。", "第二段话？"]}, open(os.path.join(wd, "paragraphs.json"), "w", encoding="utf-8"), ensure_ascii=False)

r, j = run("synth.py", "--workdir", wd)
check("synth 不加 --yes：只预览，不联网、不要密钥、不产生 out/", r.returncode == 0 and j.get("preview") is True
      and j.get("to_synthesize") == [1, 2] and not os.path.isdir(os.path.join(wd, "out")), r.stderr[-200:])
r, j = run("synth.py", "--workdir", wd, "--yes")
check("synth --yes：真的要执行（这里没有密钥，退出码 3）", r.returncode == 3 and j.get("exit_name") == "no_key")

r, j = run("align_words.py", "--workdir", wd)
check("align_words 缺 timeline.json：退出码 1，说清楚要先合成", r.returncode == 1 and "synth.py --yes" in (j.get("error") or ""))
os.makedirs(os.path.join(wd, "out"))
json.dump({"total_seconds": 3.0, "segments": [{"i": 1, "start": 0, "end": 1.5}, {"i": 2, "start": 1.8, "end": 3.0}]},
          open(os.path.join(wd, "out", "timeline.json"), "w", encoding="utf-8"))
r, j = run("align_words.py", "--workdir", wd)
check("align_words：timeline.json 没有音频指纹（手写的）就停下，提示重新合成或加 --legacy",
      r.returncode == 1 and "--legacy" in (j.get("error") or "") and "synth.py" in (j.get("next") or ""))
r, j = run("align_words.py", "--workdir", wd, "--legacy")
check("align_words 不加 --yes：只预览，不产生 words.json", r.returncode == 0 and j.get("preview") is True and j.get("segments") == 2
      and not os.path.isfile(os.path.join(wd, "out", "words.json")))

r, j = run("verify_audio.py", "--workdir", wd)
check("verify_audio 缺 words.json 又没加 --yes：不自动去联网对齐，退出码 1", r.returncode == 1 and "align_words.py --yes" in (j.get("error") or ""))

sample = os.path.join(tempfile.mkdtemp(), "样本.wav")
write_wav(sample, 6.0)
home = tempfile.mkdtemp()
wd2 = tempfile.mkdtemp()
r, j = run("clone_voice.py", "--workdir", wd2, "--sample", sample, home=home)
check("clone_voice 给了样本、不加 --yes：只做本地检查，不上传、不写 voice.json", r.returncode == 0 and j.get("preview") is True
      and abs((j.get("sample_seconds") or 0) - 6.0) < 0.2 and not os.path.isfile(os.path.join(wd2, "voice.json")), r.stderr[-200:])
write_wav(sample, 3.0)
r, j = run("clone_voice.py", "--workdir", wd2, "--sample", sample, home=home)
check("clone_voice 样本太短：退出码 5（要用户重新给）", r.returncode == 5 and j.get("exit_name") == "need_user")
json.dump({"voice_id": "default-voice"}, open(os.path.join(home, "voice.json"), "w", encoding="utf-8"))
r, j = run("clone_voice.py", "--workdir", wd2, home=home)
check("clone_voice 沿用默认音色：不花钱，不需要 --yes", r.returncode == 0 and j.get("action") == "reuse_default"
      and os.path.isfile(os.path.join(wd2, "voice.json")))

print("\n通过 %d/%d" % (sum(cases), len(cases)))
sys.exit(0 if all(cases) else 1)
