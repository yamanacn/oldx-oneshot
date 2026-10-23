"""本技能脚本共用的小工具：找 ffmpeg、读媒体时长、读写含中文路径的图片。

    import _util
    ff = _util.ffmpeg_exe()                    # 找不到时按 _cli 的约定以退出码 7（deps）退出
    secs = _util.media_seconds(path)           # 读不到返回 None
    _util.imwrite(path, img) / _util.imread(path)
    _util.file_sha(path) / _util.check_audio(...)   # 音频指纹：后一步核对前一步的音频没被改过（见 AUDIO_EDIT_RULE）

cv2 只在读写图片时才导入，配音阶段的脚本不需要装 OpenCV。
"""
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _cli  # noqa: E402

FFMPEG_HINT = "找不到 ffmpeg：运行 scripts/setup_env.py 自动安装，或把 ffmpeg 加入 PATH（pip install imageio-ffmpeg 也可以）"
_ffmpeg_cache = []


def ffmpeg_exe():
    """PATH 里的 ffmpeg 优先，其次用 imageio-ffmpeg 自带的那份。"""
    if _ffmpeg_cache:
        return _ffmpeg_cache[0]
    exe = shutil.which("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            exe = None
    if not exe:
        _cli.fail(_cli.DEPS, FFMPEG_HINT)
    _ffmpeg_cache.append(exe)
    return exe


def media_seconds(path):
    """用 `ffmpeg -i` 读容器时长（秒），读不到返回 None。"""
    err = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None


# ---------------------------------------------------------------- 音频指纹：合成 → 对齐 → 分段 → 生成，每一步核对上一步的音频没被改过
AUDIO_EDIT_RULE = ("音频只能由配音阶段的 synth.py 生成，本技能不做调速：要改就重新合成（synth.py --yes），"
                   "再依次跑 align_words.py --yes、plan_segments.py。不要在对齐或分段之后再改音频，"
                   "否则停顿和切点对不上，生成的视频会有明显的停顿。")


def file_sha(path):
    """文件内容的指纹（sha256 前 16 位）。"""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()[:16]


def wav_seconds(path):
    import wave
    w = wave.open(str(path))
    try:
        return w.getnframes() / float(w.getframerate())
    finally:
        w.close()


def check_audio(path, expect_sha, expect_seconds, stage, redo):
    """path 的指纹要等于 stage 那一步记下的 expect_sha，否则以退出码 1 停下。
    旧项目没有记指纹（expect_sha 为 None）时跳过，返回 False；核对通过返回 True。"""
    if not expect_sha:
        return False
    if not os.path.isfile(path):
        _cli.fail(_cli.ERROR, "找不到 %s（%s时记下的音频）。%s" % (path, stage, redo))
    if file_sha(path) == expect_sha:
        return True
    try:
        now = wav_seconds(path)
    except Exception:
        now = None
    if now is not None and expect_seconds:
        how = "时长 %.1f 秒 → %.1f 秒" % (expect_seconds, now)
        if abs(now / expect_seconds - 1) > 0.02:
            how += "，像是被调过速（本技能不做调速）"
    else:
        how = "内容变了"
    _cli.fail(_cli.ERROR, "%s 在%s之后被改过（%s）。%s\n%s" % (path, stage, how, AUDIO_EDIT_RULE, redo))


# ---------------------------------------------------------------- 停顿检测（逐词对齐用）
def detect_pauses(wav_path, min_len=0.25, edges=False):
    """连续 ≥min_len 秒的低能量段（每 20ms 一格，低于 95 分位音量的 5%）。
    edges=False 时开头、结尾的静音不算（逐词对齐用）；edges=True 时也算上。"""
    import wave
    import numpy as np
    w = wave.open(str(wav_path))
    try:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        sr = w.getframerate()
    finally:
        w.close()
    hop = int(sr * 0.02)
    n = (len(x) - hop * 2) // hop
    rms = np.array([np.sqrt(np.mean(x[i * hop:i * hop + hop * 2] ** 2)) for i in range(n)])
    quiet = rms < 0.05 * np.percentile(rms, 95)
    spans, s = [], None
    for i, q in enumerate(quiet):
        if q and s is None:
            s = i
        if (not q) and s is not None:
            if (s > 0 or edges) and (i - s) * 0.02 >= min_len:
                spans.append({"start": round(s * 0.02, 3), "end": round(i * 0.02, 3)})
            s = None
    if edges and s is not None and (n - s) * 0.02 >= min_len:
        spans.append({"start": round(s * 0.02, 3), "end": round(len(x) / sr, 3)})
    return spans


def imwrite(path, img):
    """cv2.imwrite 在带中文的路径上会悄悄失败（不报错、也不写文件），改成先编码再写出。"""
    import cv2
    ok, buf = cv2.imencode(os.path.splitext(str(path))[1] or ".png", img)
    if not ok:
        raise RuntimeError("图片编码失败：%s" % path)
    buf.tofile(str(path))
    return True


def imread(path):
    """cv2.imread 读不了带中文的路径。读不到返回 None。"""
    import cv2
    import numpy as np
    try:
        return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    except OSError:
        return None
