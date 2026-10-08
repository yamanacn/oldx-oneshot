"""脚本共用：定位 ffmpeg、按输出帧率顺序读帧、读写含中文路径的图片。"""
import re
import shutil
import subprocess
import sys
from fractions import Fraction

import cv2
import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        sys.exit("找不到 ffmpeg：把 ffmpeg 加入 PATH，或 pip install imageio-ffmpeg")


def parse_fps(value):
    """'30'、'29.97'、'30000/1001' -> Fraction。"""
    return Fraction(str(value)).limit_denominator(100000)


def media_info(path):
    """用 `ffmpeg -i` 读容器时长与是否有音轨；细节用 probe_media.py。"""
    err = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None
    return {"duration": duration,
            "has_video": bool(re.search(r"Stream #.*Video:", err)),
            "has_audio": bool(re.search(r"Stream #.*Audio:", err))}


def imread(path):
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        sys.exit(f"无法读取图片：{path}")
    return img


def imwrite(path, img):
    ext = "." + str(path).rsplit(".", 1)[-1]
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        sys.exit(f"无法编码图片：{path}")
    buf.tofile(str(path))


def fit_image(img, size, fit="contain", bg=(16, 16, 16)):
    """等比放入画布：contain 留边，cover 裁切。"""
    W, H = size
    h, w = img.shape[:2]
    scale = min(W / w, H / h) if fit == "contain" else max(W / w, H / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LANCZOS4
    img = cv2.resize(img, (nw, nh), interpolation=interp)
    canvas = np.full((H, W, 3), bg, dtype=np.uint8)
    x, y = (W - nw) // 2, (H - nh) // 2
    sx, sy = max(0, -x), max(0, -y)
    dx, dy = max(0, x), max(0, y)
    cw, ch = min(nw - sx, W - dx), min(nh - sy, H - dy)
    canvas[dy:dy + ch, dx:dx + cw] = img[sy:sy + ch, sx:sx + cw]
    return canvas


class FrameReader:
    """顺序读取 BGR 帧。第 n 帧对应源时间 start + n/fps（由 ffmpeg 的 fps 滤镜按
    源时间戳取帧，可变帧率源也成立）；显示方向由 ffmpeg 自动旋转处理。"""

    def __init__(self, path, size, fps, start=0.0, fit="cover", bg=(16, 16, 16)):
        self.path, self.size = str(path), size
        W, H = size
        if fit == "cover":
            geo = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
        else:
            color = "0x%02x%02x%02x" % (bg[2], bg[1], bg[0])
            geo = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                   f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={color}")
        cmd = [ffmpeg_exe(), "-v", "error", "-ss", f"{float(start):.6f}", "-i", self.path, "-an",
               "-vf", f"fps={fps.numerator}/{fps.denominator},{geo},setsar=1",
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=10 ** 8)
        self.nbytes = W * H * 3
        self.last = None

    def read(self):
        """返回下一帧；源耗尽返回 None（self.last 保留最后一帧）。"""
        buf = self.proc.stdout.read(self.nbytes)
        if len(buf) < self.nbytes:
            return None
        W, H = self.size
        self.last = np.frombuffer(buf, dtype=np.uint8).reshape(H, W, 3)
        return self.last

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.stdout.close()
