"""叠加层：文字卡、框选、图片、图片序列，带进出动画。由 pip_render.py 按 spec 的 "overlays" 调用。

每个叠加项（时间为成片秒，位置和尺寸是输出画面的比例）：
  公共字段  start、end、fade（进出各多少秒，默认 0.25）、anim（fade｜rise｜pop，默认 fade）、
            layer（top：盖在所有画面之上，默认；content：画在内容层上，圆形小窗仍在它上面，
            只在 content_pip／content_full 段内可见）、pos [x, y]、anchor（center｜left｜right｜
            top｜bottom｜top_left 等，默认 center）
  text      text、size（按 1080 短边计的字号，默认 56）、color、bg（底色，null 为无底）、
            bg_alpha、padding、radius、max_width（占画面宽度的比例，超过自动换行，默认 0.7）、
            stroke（描边像素，无底色时保证可读）、font（字体文件，省略时自动找中文字体）
  box       rect [x, y, w, h]、color、thickness、radius、fill_alpha（框内填充透明度，默认 0）
  image     file（PNG 透明通道会保留）、width（占画面宽度的比例）
  sequence  dir（按文件名排序的 PNG 序列，从 start 起按成片帧率逐帧播放，播完停在最后一帧）；
            其他工具（Remotion、AE、自己写的绘图代码）做好的动画导出成 PNG 序列后从这里进来

颜色写 [R, G, B]。文字用 Pillow 画（OpenCV 画不了中文）：pip install pillow。
环境变量 BROLL_FONT 可指定字体文件。
"""
import os
import sys
from pathlib import Path

import cv2
import numpy as np

FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]
ANCHORS = {"center": (0.5, 0.5), "left": (0.0, 0.5), "right": (1.0, 0.5), "top": (0.5, 0.0), "bottom": (0.5, 1.0),
           "top_left": (0.0, 0.0), "top_right": (1.0, 0.0), "bottom_left": (0.0, 1.0), "bottom_right": (1.0, 1.0)}
KINDS = ("text", "box", "image", "sequence")


def find_font(explicit=None):
    for p in [explicit, os.environ.get("BROLL_FONT")] + FONT_CANDIDATES:
        if p and Path(p).is_file():
            return str(p)
    sys.exit("找不到中文字体：用叠加项的 font 字段或环境变量 BROLL_FONT 指定一个字体文件（.ttf/.ttc/.otf）")


def ease(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def _rounded(w, h, r, color, alpha):
    """圆角矩形 BGRA。"""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=tuple(color) + (int(255 * alpha),))
    return im


def render_text(o, W, H):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        sys.exit("文字叠加需要 Pillow：pip install pillow")
    k = min(W, H) / 1080
    size = max(8, round(o.get("size", 56) * k))
    font = ImageFont.truetype(find_font(o.get("font")), size)
    pad = round(o.get("padding", 22) * k)
    stroke = round(o.get("stroke", 0 if o.get("bg", [20, 20, 20]) else 4) * k)
    maxw = o.get("max_width", 0.7) * W - 2 * pad
    lines = []
    for para in str(o["text"]).split("\n"):       # 按像素宽度逐字换行（中文没有空格可断）
        cur = ""
        for ch in para:
            if cur and font.getlength(cur + ch) > maxw:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        lines.append(cur)
    lh = round(size * 1.32)
    tw = max(int(font.getlength(s)) for s in lines) if lines else 0
    w, h = tw + 2 * pad + 2 * stroke, lh * len(lines) + 2 * pad
    bg = o.get("bg", [20, 20, 20])
    im = _rounded(w, h, round(o.get("radius", 14) * k), bg, o.get("bg_alpha", 0.82)) if bg else Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    color = tuple(o.get("color", [255, 255, 255])) + (255,)
    for i, s in enumerate(lines):
        x = (w - font.getlength(s)) / 2
        d.text((x, pad + i * lh + (lh - size) / 2 - size * 0.08), s, font=font, fill=color,
               stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGBA2BGRA)


def render_box(o, W, H):
    from PIL import Image, ImageDraw
    k = min(W, H) / 1080
    x, y, w, h = o["rect"]
    w, h = max(2, round(w * W)), max(2, round(h * H))
    th = max(1, round(o.get("thickness", 6) * k))
    color = tuple(o.get("color", [255, 214, 10]))
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([0, 0, w - 1, h - 1], radius=round(o.get("radius", 12) * k),
                                         fill=color + (int(255 * o.get("fill_alpha", 0.0)),),
                                         outline=color + (255,), width=th)
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGBA2BGRA)


def load_rgba(path, target_w=None):
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        sys.exit(f"无法读取图片：{path}")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    if target_w and img.shape[1] != target_w:
        s = target_w / img.shape[1]
        img = cv2.resize(img, (target_w, max(1, round(img.shape[0] * s))),
                         interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LANCZOS4)
    return img


def blit(frame, rgba, cx, cy, alpha=1.0):
    """把 BGRA 图以 (cx, cy) 为左上角合到 BGR 帧上（就地修改），超出画面的部分裁掉。"""
    H, W = frame.shape[:2]
    h, w = rgba.shape[:2]
    x0, y0 = int(round(cx)), int(round(cy))
    sx, sy = max(0, -x0), max(0, -y0)
    dx, dy = max(0, x0), max(0, y0)
    cw, ch = min(w - sx, W - dx), min(h - sy, H - dy)
    if cw <= 0 or ch <= 0 or alpha <= 0:
        return
    src = rgba[sy:sy + ch, sx:sx + cw]
    a = (src[..., 3:4].astype(np.float32) / 255.0) * alpha
    reg = frame[dy:dy + ch, dx:dx + cw].astype(np.float32)
    frame[dy:dy + ch, dx:dx + cw] = np.clip(reg * (1 - a) + src[..., :3].astype(np.float32) * a + 0.5, 0, 255)


class Overlay:
    def __init__(self, o, base, size, fps, idx):
        self.o, self.size, self.fps = o, size, fps
        W, H = size
        kind = o.get("type")
        if kind not in KINDS:
            sys.exit(f"叠加项 {idx}：type 只能是 {'/'.join(KINDS)}，收到 {kind}")
        for key in ("start", "end"):
            if key not in o:
                sys.exit(f"叠加项 {idx}：缺少 {key}")
        if o["end"] <= o["start"]:
            sys.exit(f"叠加项 {idx}：end 必须大于 start")
        self.start, self.end = float(o["start"]), float(o["end"])
        self.fade = float(o.get("fade", 0.25))
        self.anim = o.get("anim", "fade")
        self.layer = o.get("layer", "top")
        if self.layer not in ("top", "content"):
            sys.exit(f"叠加项 {idx}：layer 只能是 top 或 content")
        self.frames = None
        if kind == "text":
            self.img = render_text(o, W, H)
        elif kind == "box":
            self.img = render_box(o, W, H)
        elif kind == "image":
            p = (base / o["file"]).resolve()
            if not p.is_file():
                sys.exit(f"叠加项 {idx}：图片不存在 {p}")
            self.img = load_rgba(p, round(o["width"] * W) if o.get("width") else None)
        else:
            d = (base / o["dir"]).resolve()
            self.frames = sorted(d.glob("*.png")) if d.is_dir() else []
            if not self.frames:
                sys.exit(f"叠加项 {idx}：序列目录里没有 PNG：{d}")
            self.img = load_rgba(self.frames[0], round(o["width"] * W) if o.get("width") else None)
            self._cache = (0, self.img)
        if kind == "box":
            x, y = o["rect"][0] * W, o["rect"][1] * H
            self.x, self.y = x, y
        else:
            ax, ay = ANCHORS.get(o.get("anchor", "center"), (0.5, 0.5))
            px, py = o.get("pos", [0.5, 0.5] if kind != "sequence" or o.get("width") else [0.0, 0.0])
            if kind == "sequence" and "pos" not in o and not o.get("width"):
                ax = ay = 0.0                                   # 整幅序列：左上角对齐
            h, w = self.img.shape[:2]
            self.x, self.y = px * W - ax * w, py * H - ay * h

    def active(self, t):
        return self.start - 1e-6 <= t < self.end - 1e-6

    def draw(self, frame, t):
        if not self.active(t):
            return
        img = self.img
        if self.frames:
            i = min(len(self.frames) - 1, int((t - self.start) * self.fps + 1e-6))
            if self._cache[0] != i:
                self._cache = (i, load_rgba(self.frames[i], self.img.shape[1]))
            img = self._cache[1]
        F = self.fade
        a_in = ease((t - self.start) / F) if F > 0 else 1.0
        a_out = ease((self.end - t) / F) if F > 0 else 1.0
        a = min(a_in, a_out)
        x, y = self.x, self.y
        if self.anim == "rise":
            y += (1 - a_in) * 0.03 * self.size[1]
        elif self.anim == "pop" and a_in < 1:
            s = 0.85 + 0.15 * a_in
            h, w = img.shape[:2]
            img = cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_LINEAR)
            x, y = x + (w - img.shape[1]) / 2, y + (h - img.shape[0]) / 2
        blit(frame, img, x, y, a)


def prepare(items, base, size, fps):
    """把 spec["overlays"] 变成可绘制对象。fps 传浮点数。"""
    return [Overlay(o, Path(base), size, float(fps), i) for i, o in enumerate(items or [])]


def draw_layer(frame, overlays, t, layer):
    """在 frame 上画出某一层当前可见的叠加项；有东西要画时返回可写副本，否则原样返回。"""
    todo = [o for o in overlays if o.layer == layer and o.active(t)]
    if not todo:
        return frame
    out = frame.copy()
    for o in todo:
        o.draw(out, t)
    return out
