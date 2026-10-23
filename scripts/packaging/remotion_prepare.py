"""把一条口播的素材放进 Remotion 工程（由 assets/remotion-template 复制出来的目录）。

  python remotion_prepare.py <工程目录> --presenter 人物.mp4 --voice 原声.wav --words words.json [--subs subs.json] [--copy 文案.txt]

做四件事：
- 人物视频复制到 public/presenter.mp4；原声转成双声道 48k 写到 public/voice.wav
  （单声道文件进了 Remotion 会被压低 3 分贝，所以原声和音效都存成双声道）；
- 逐词时间复制到 src/data/words.json；
- 字幕写成 src/data/captions.json（没给 --subs 就现生成：有 --copy 用 captions_from_copy.py 按原文一句一条、保留标点；没有才用 subs_from_words.py）；
- 画面尺寸、帧率、时长、音效音量写进 src/data/film.json。音效音量按原声的典型峰值算：
  音效文件的峰值都是 1，乘上这个音量后比人声低 --sfx-db 分贝（默认 15）。
  画面尺寸：--size 1920x1080 指定成片尺寸（竖屏人物做横屏片时必须给）；不给时沿用 film.json 里已有的尺寸，
  都没有才用人物视频的尺寸。重跑不会再把画面改回人物视频的尺寸。
- 字幕：src/data/captions.locked 存在（或给了 --keep-captions）时不重新生成，手改过的 captions.json 保留；
  逐词时间里带 edit.gaps（timeline_edit.py 产出）时，标了 hide_captions 的空档里不显示字幕。
- 帧率：--fps 60 指定成片帧率并记进 film.json（lockFps），以后重跑不会被改回人物视频的帧率（实测改成 60 后重跑被打回 24）。
- 锁定的字幕：captions.locked 存在时保留手改的 captions.json，但逐词时间变了（例如 timeline_edit 删插了空档）字幕的时间就全错了，
  所以生成字幕时把逐词时间的指纹记进 captions.meta.json，锁定时发现指纹对不上就在输出里报 captions_stale（不自动覆盖）。
- 背景音乐（--bgm 音乐文件）：从 --bgm-offset 秒处截成成片长度，写到 public/bgm.wav；音乐比成片短时自动按小节接长
  （开头原样、中段整小节循环、曲子原本的结尾对成片结尾，接缝处 0.25 秒等功率交叉淡化，最后 1.5 秒淡出）；音量写进 film.json 的 bgmGain，
  让音乐比人声低 --bgm-db 分贝（默认 12；以前默认 18，用户反馈太小）；用 librosa 测节拍写进 src/data/beats.json
  （tempo、beats、downbeats，成片时间轴上的秒）。工程里 lib/beats 的 snap/bar/beatInfo 读它。
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


def _rms_active(x):
    fr = 2048
    e = np.array([np.sqrt(np.mean(x[i:i + fr] ** 2)) for i in range(0, max(1, len(x) - fr), fr)])
    e = e[e > np.percentile(e, 40)] if len(e) > 10 else e
    return float(np.sqrt(np.mean(e ** 2))) + 1e-9


def loop_bed(src, duration, offset):
    """音乐比成片短时按小节接长：返回 (双声道 float 数组, 说明)；够长或测不出节拍时返回 None"""
    try:
        import librosa
    except ImportError:
        return None
    y, sr = librosa.load(str(src), sr=SR, mono=False)
    y = np.atleast_2d(y).T[int(offset * SR):]
    if len(y) / SR >= duration + 1:
        return None
    tempo, bt = librosa.beat.beat_track(y=librosa.to_mono(y.T), sr=SR, units="time")
    tempo = float(np.atleast_1d(tempo)[0])
    if tempo <= 0 or len(bt) < 16:
        return None
    bar, b0, X = 4 * 60 / tempo, float(bt[0]), int(0.25 * SR)
    nbars = int((len(y) / SR - b0) / bar)
    B = lambda n: int((b0 + n * bar) * SR)
    head_end, loop_a = max(4, int(nbars * 0.7)), max(2, int(nbars * 0.25))
    head, loop = y[:B(head_end)], y[B(loop_a):B(head_end)]
    best = None
    for k in range(0, 40):
        for n in range(loop_a + 1, nbars):
            tot = (len(head) + k * len(loop) + len(y) - B(n) - (k + 1) * X) / SR
            if tot <= duration + 1 and (best is None or duration + 1 - tot < best[0]):
                best = (duration + 1 - tot, k, n)
    if best is None:
        return None
    _, k, n = best
    t = np.linspace(0, np.pi / 2, X)[:, None]
    bed = head
    for p in [loop] * k + [y[B(n):]]:
        bed = np.concatenate([bed[:-X], bed[-X:] * np.cos(t) + p[:X] * np.sin(t), p[X:]])
    need = int((duration + 1) * SR)
    bed = np.concatenate([bed, np.zeros((max(0, need - len(bed)), bed.shape[1]))])[:need]
    f = int(1.5 * SR)
    bed[-f:] *= np.linspace(1, 0, f)[:, None]
    if bed.shape[1] == 1:
        bed = np.repeat(bed, 2, 1)
    return bed, f"音乐 {len(y) / SR:.1f} 秒比成片短，按小节（{tempo:.1f} BPM）循环中段 {k} 次接到 {duration:.1f} 秒，结尾用曲子原本的收尾"


def prepare_bgm(src, out_wav, beats_json, offset, duration, voice_wav, below_db):
    """截音乐（太短就按小节接长）、算音量（比人声低 below_db 分贝）、测节拍。返回 (bgmGain, beats, 接长说明)"""
    looped = loop_bed(src, duration, offset)
    if looped:
        bed, note = looped
        o = wave.open(str(out_wav), "wb")
        o.setnchannels(2), o.setsampwidth(2), o.setframerate(SR)
        o.writeframes((np.clip(bed, -1, 1) * 32767).astype(np.int16).tobytes())
        o.close()
        src, offset = out_wav, 0.0          # 节拍按接长后的音乐测，直接对齐成片
    else:
        note = None
        subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-ss", f"{offset:.3f}", "-i", str(src), "-t", f"{duration + 1:.3f}",
                        "-ac", "2", "-ar", str(SR), "-c:a", "pcm_s16le", str(out_wav)], check=True)
    def read(p):
        w = wave.open(str(p))
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        ch = w.getnchannels()
        w.close()
        return x.reshape(-1, ch).mean(1)
    gain = _rms_active(read(voice_wav)) / _rms_active(read(out_wav)) * 10 ** (-below_db / 20)
    beats = {"tempo": 0, "offset": offset, "beats": [], "downbeats": []}
    try:
        import librosa
        y, sr = librosa.load(str(src), sr=22050, mono=True)
        tempo, bt = librosa.beat.beat_track(y=y, sr=sr, units="time")
        on = librosa.onset.onset_strength(y=y, sr=sr)
        ot = librosa.times_like(on, sr=sr)
        strength = np.interp(bt, ot, on)
        phase = int(np.argmax([strength[i::4].mean() for i in range(4)])) if len(bt) >= 8 else 0
        to_film = lambda xs: [round(float(v) - offset, 3) for v in xs if 0 <= float(v) - offset <= duration + 1]
        beats = {"tempo": round(float(np.atleast_1d(tempo)[0]), 1), "offset": offset, "beats": to_film(bt), "downbeats": to_film(bt[phase::4])}
    except Exception as e:                                   # 没装 librosa 也能用，只是没有节拍
        beats["error"] = f"节拍没测出来：{e}"
    beats_json.write_text(json.dumps(beats, ensure_ascii=False), encoding="utf-8")
    return round(gain, 4), beats, note


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
    ap.add_argument("--size", help="成片尺寸，例如 1920x1080（竖屏人物做横屏片时必须给）")
    ap.add_argument("--fps", type=int, help="成片帧率，例如 60（记进 film.json，重跑不会被改回人物视频的帧率）")
    ap.add_argument("--keep-captions", action="store_true", help="不重新生成字幕，保留手改过的 captions.json")
    ap.add_argument("--bgm", help="背景音乐文件")
    ap.add_argument("--bgm-db", type=float, default=12.0, help="音乐比人声低多少分贝（默认 12）")
    ap.add_argument("--bgm-offset", type=float, default=0.0, help="从音乐的第几秒开始用（让音乐的高潮对上某句话）")
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
    (data / "words.json").write_text(json.dumps({"words": words["words"], **({"edit": words["edit"]} if words.get("edit") else {})}, ensure_ascii=False), encoding="utf-8")

    keep = a.keep_captions or (data / "captions.locked").exists()
    import hashlib
    words_sha = hashlib.sha1(json.dumps(words["words"], sort_keys=True).encode("utf-8")).hexdigest()[:12]
    meta_path = data / "captions.meta.json"
    old_sha = json.loads(meta_path.read_text(encoding="utf-8")).get("words_sha") if meta_path.is_file() else None
    captions_stale = bool(keep and (data / "captions.json").is_file() and old_sha and old_sha != words_sha)
    subs_path = Path(a.subs) if a.subs else data / "_subs.json"
    if keep and (data / "captions.json").is_file():
        caps = json.loads((data / "captions.json").read_text(encoding="utf-8"))
    elif not a.subs:
        if a.copy:      # 有原文案：一句一条、保留标点（字幕规范见 references/packaging/design.md）
            cmd = [sys.executable, str(HERE / "captions_from_copy.py"), a.words, "--copy", a.copy, "--out", str(subs_path), "--max-chars", str(a.max_chars or 22)]
        else:           # 没有原文案的退路：按识别结果和停顿分行，没有标点
            cmd = [sys.executable, str(HERE / "subs_from_words.py"), a.words, "--out", str(subs_path), "--max-chars", str(a.max_chars or 15)]
        subprocess.run(cmd, check=True, capture_output=True)
    if not (keep and (data / "captions.json").is_file()):
        subs = json.loads(subs_path.read_text(encoding="utf-8"))
        caps = [{"text": s["text"], "startMs": round(s["start"] * 1000), "endMs": round(s["end"] * 1000), "timestampMs": None,
                 "confidence": None} for s in subs]
        # 标了 hide_captions 的空档（插外部片段时）里不显示字幕：跨进空档的那条截到空档开始
        for g in (words.get("edit") or {}).get("gaps", []):
            if g.get("hide_captions"):
                gs, ge = g["start"] * 1000, g["end"] * 1000
                for c in caps:
                    if c["startMs"] < gs < c["endMs"]:
                        c["endMs"] = round(gs - 30)
                caps = [c for c in caps if not (gs <= c["startMs"] < ge)]
        (data / "captions.json").write_text(json.dumps(caps, ensure_ascii=False, indent=1), encoding="utf-8")
        meta_path.write_text(json.dumps({"words_sha": words_sha}), encoding="utf-8")
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
    if a.size:
        sw, sh = (int(v) for v in a.size.lower().split("x"))
    elif film.get("width") and film.get("lockSize"):
        sw, sh = film["width"], film["height"]
    else:
        sw, sh = info["width"], info["height"]
    fps = a.fps or (film.get("fps") if film.get("lockFps") else None) or round(info["fps"])
    film.update(width=sw, height=sh, lockSize=bool(a.size or film.get("lockSize")), presenter={"width": info["width"], "height": info["height"]}, fps=fps,
                lockFps=bool(a.fps or film.get("lockFps")),
                duration=round(min(info["duration"], voice_seconds), 3),
                sfxVolume=round(peak * 10 ** (-a.sfx_db / 20), 4))
    film_path.write_text(json.dumps(film, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.bgm:
        film["bgmGain"], beats, bgm_note = prepare_bgm(a.bgm, pub / "bgm.wav", data / "beats.json", a.bgm_offset, film["duration"], pub / "voice.wav", a.bgm_db)
        film["bgm"] = {"file": Path(a.bgm).name, "offset": a.bgm_offset, "belowVoiceDb": a.bgm_db, "tempo": beats.get("tempo"), **({"looped": bgm_note} if bgm_note else {})}
        film_path.write_text(json.dumps(film, ensure_ascii=False, indent=1), encoding="utf-8")
    elif not (data / "beats.json").exists():
        (data / "beats.json").write_text(json.dumps({"tempo": 0, "offset": 0, "beats": [], "downbeats": []}), encoding="utf-8")
    if not (data / "aliases.json").exists():
        (data / "aliases.json").write_text("{}", encoding="utf-8")
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
    if captions_stale:
        print("注意：captions.locked 锁定的字幕是按旧的逐词时间做的（逐词时间已经变了，多半是空档改过），字幕时间会对不上。"
              "删掉 src/data/captions.locked 重跑让它重新生成，再把手改的地方重新改一遍。", file=sys.stderr)
    print(json.dumps({"film": film, "captions": len(caps), "captions_kept": bool(keep), "captions_stale": captions_stale, "voice_peak": round(peak, 4), "sfx_below_voice_db": a.sfx_db,
                      "sfx_made": made, "font": film.get("subtitleFont")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
