"""把一条口播的素材放进 Remotion 工程（由 assets/remotion-template 复制出来的目录）。

  python remotion_prepare.py <工程目录> --presenter 人物.mp4 --voice 原声.wav --words words.json [--subs subs.json] [--copy 文案.txt]

做四件事：
- 人物视频复制到 public/presenter.mp4；原声转成双声道 48k 写到 public/voice.wav
  （单声道文件进了 Remotion 会被压低 3 分贝，所以原声和音效都存成双声道）；
- 逐词时间复制到 src/data/words.json；
- 字幕写成 src/data/captions.json（没给 --subs 就现生成：有 --copy 用 captions_from_copy.py 按原文一句一条、保留标点；没有才用 subs_from_words.py）；
- 画面尺寸、帧率、时长、音效音量写进 src/data/film.json。音效音量按原声的典型峰值算：
  音效文件的峰值都是 1，乘上这个音量后比人声低 --sfx-db 分贝（默认 15）。
public/sfx 下没有音效文件时合成一套（落定、提示、划过、敲键）；public/fonts/sub.ttf 不存在时复制一个字幕字体进去
（--font 指定，否则在本机找阿里巴巴普惠体，再不行用黑体）。
"""
import argparse
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import cv2
import numpy as np

from _common import ffmpeg_exe

SR = 48000
HERE = Path(__file__).parent


def _env(n, attack=0.004, decay=18.0):
    t = np.arange(n) / SR
    return np.minimum(1, t / attack) * np.exp(-decay * t)


def sfx_pop():
    n = int(0.09 * SR)
    t = np.arange(n) / SR
    f = 620 * np.exp(-14 * t) + 190
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, decay=34)


def sfx_ding():
    n = int(0.42 * SR)
    t = np.arange(n) / SR
    return (np.sin(2 * np.pi * 1318 * t) + 0.45 * np.sin(2 * np.pi * 2637 * t)) * _env(n, decay=9)


def sfx_whoosh(up):
    n = int(0.3 * SR)
    noise = np.random.default_rng(7).standard_normal(n)
    out, lo = np.zeros(n), 0.0
    for i in range(n):                                    # 一阶低通，截止频率随时间扫动
        p = i / n if up else 1 - i / n
        lo += (0.02 + 0.22 * p) * (noise[i] - lo)
        out[i] = lo
    return out * np.sin(np.pi * np.arange(n) / n) ** 1.5


def sfx_key(seed):
    n = int(0.03 * SR)
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SR
    return (rng.standard_normal(n) * 0.5 + np.sin(2 * np.pi * (1900 + 300 * rng.random()) * t)) * _env(n, attack=0.001, decay=160)


def write_sfx(folder):
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    sounds = {"pop": sfx_pop(), "ding": sfx_ding(), "whoosh_in": sfx_whoosh(True), "whoosh_out": sfx_whoosh(False),
              "key0": sfx_key(0), "key1": sfx_key(1), "key2": sfx_key(2)}
    for name, x in sounds.items():
        f = folder / f"{name}.wav"
        if f.exists():
            continue
        x = x / (np.abs(x).max() + 1e-9)                   # 峰值统一成 1，响度在工程里用音量控制
        x = np.concatenate([x, np.zeros(int(0.05 * SR))])
        o = wave.open(str(f), "wb")
        o.setnchannels(2), o.setsampwidth(2), o.setframerate(SR)
        o.writeframes(np.repeat((x * 32767).astype(np.int16), 2).tobytes())
        o.close()
        made.append(name)
    return made


def voice_peak(path):
    w = wave.open(str(path))
    if w.getsampwidth() != 2:
        sys.exit("原声需要是 16 位 wav")
    x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    w.close()
    return float(np.percentile(np.abs(x), 99.5))          # 人声的典型峰值


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--presenter", required=True)
    ap.add_argument("--voice", required=True)
    ap.add_argument("--words", required=True)
    ap.add_argument("--subs")
    ap.add_argument("--copy")
    ap.add_argument("--max-chars", type=int, help="字幕一条最多多少字（按原文做时默认 22，横屏；竖屏用 14 左右）")
    ap.add_argument("--sfx-db", type=float, default=15.0)
    ap.add_argument("--font", help="字幕字体文件（ttf/otf）")
    a = ap.parse_args()

    proj = Path(a.project)
    pub, data = proj / "public", proj / "src" / "data"
    if not (proj / "package.json").is_file():
        sys.exit(f"{proj} 不是 Remotion 工程目录（先把 assets/remotion-template 复制过来）")
    pub.mkdir(exist_ok=True), data.mkdir(parents=True, exist_ok=True)
    shutil.copy2(a.presenter, pub / "presenter.mp4")
    subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-i", a.voice, "-af", "pan=stereo|c0=c0|c1=c0", "-ar", str(SR),
                    "-c:a", "pcm_s16le", str(pub / "voice.wav")], check=True)
    words = json.loads(Path(a.words).read_text(encoding="utf-8-sig"))
    if isinstance(words, list):
        words = {"words": words}
    (data / "words.json").write_text(json.dumps({"words": words["words"]}, ensure_ascii=False), encoding="utf-8")

    subs_path = Path(a.subs) if a.subs else data / "_subs.json"
    if not a.subs:
        if a.copy:      # 有原文案：一句一条、保留标点（字幕规范见 references/packaging/design.md）
            cmd = [sys.executable, str(HERE / "captions_from_copy.py"), a.words, "--copy", a.copy, "--out", str(subs_path), "--max-chars", str(a.max_chars or 22)]
        else:           # 没有原文案的退路：按识别结果和停顿分行，没有标点
            cmd = [sys.executable, str(HERE / "subs_from_words.py"), a.words, "--out", str(subs_path), "--max-chars", str(a.max_chars or 15)]
        subprocess.run(cmd, check=True, capture_output=True)
    subs = json.loads(subs_path.read_text(encoding="utf-8"))
    caps = [{"text": s["text"], "startMs": round(s["start"] * 1000), "endMs": round(s["end"] * 1000), "timestampMs": None,
             "confidence": None} for s in subs]
    (data / "captions.json").write_text(json.dumps(caps, ensure_ascii=False, indent=1), encoding="utf-8")
    if not a.subs:
        subs_path.unlink()

    cap = cv2.VideoCapture(str(a.presenter))
    info = {"width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": cap.get(cv2.CAP_PROP_FPS)}
    info["duration"] = cap.get(cv2.CAP_PROP_FRAME_COUNT) / info["fps"]
    cap.release()
    w = wave.open(str(pub / "voice.wav"))
    voice_seconds = w.getnframes() / w.getframerate()
    w.close()
    peak = voice_peak(pub / "voice.wav")
    film_path = data / "film.json"
    film = json.loads(film_path.read_text(encoding="utf-8")) if film_path.is_file() else {}
    film.update(width=info["width"], height=info["height"], fps=round(info["fps"]),
                duration=round(min(info["duration"], voice_seconds), 3),
                sfxVolume=round(peak * 10 ** (-a.sfx_db / 20), 4))
    film_path.write_text(json.dumps(film, ensure_ascii=False, indent=1), encoding="utf-8")
    made = write_sfx(pub / "sfx")
    font = pub / "fonts" / "sub.ttf"
    if a.font or not font.exists():
        home = Path.home() / "AppData/Local/Microsoft/Windows/Fonts"
        cands = [a.font] if a.font else [home / "AlibabaPuHuiTi-3-85-Bold.ttf", "C:/Windows/Fonts/AlibabaPuHuiTi-3-85-Bold.ttf", "C:/Windows/Fonts/simhei.ttf"]
        src = next((Path(c) for c in cands if c and Path(c).is_file()), None)
        if src is None:
            sys.exit("找不到字幕字体，用 --font 指定一个 ttf/otf 文件")
        font.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, font)
        film["subtitleFont"] = src.name
    print(json.dumps({"film": film, "captions": len(caps), "voice_peak": round(peak, 4), "sfx_below_voice_db": a.sfx_db,
                      "sfx_made": made, "font": film.get("subtitleFont")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
