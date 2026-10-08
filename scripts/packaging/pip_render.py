"""人物全屏 <-> 右下角圆形小窗的参考合成器。

同一条人物视频、同一个时钟、原声单轨：成片第 n 帧永远取源时间 source_start + n/fps，
缩放、遮罩、隐藏都不重置媒体位置。画面状态只由帧号决定，可分段重渲。

用法：
  python pip_render.py spec.json --out out.mp4
  python pip_render.py spec.json --out sample.mp4 --start 10 --end 22      # 只渲样段
  python pip_render.py spec.json --stills 11.8,12.0,12.25,12.5 --stills-dir qc   # 只出静帧

spec.json（路径相对 spec 所在目录；时间为成片秒）：
{
  "presenter": "talk.mp4",
  "size": [1920, 1080], "fps": "30",
  "source_start": 0,                  // 成片 0 秒对应的源时间
  "duration": null,                   // 省略则到源片结束
  "pip": {"corner": "bottom_right", "diameter": 0.2, "margin": 0.03, "transition": 0.5,
          "focus": [0.5, 0.38], "focus_size": 0.55, "border": 0,
          "border_color": [255, 255, 255], "hide_fade": 0.2},
  "segments": [
    {"start": 12.0, "end": 20.0, "mode": "content_pip",  "content": "flow.png"},
    {"start": 20.0, "end": 26.0, "mode": "content_full", "content": "demo.mp4", "content_start": 3.0},
    {"start": 26.0, "end": 31.0, "mode": "content_pip",  "content": "summary.png", "fit": "contain"}
  ],
  "overlays": [                       // 可选：文字卡、框选、图片、PNG 序列，字段见 overlay_kit.py
    {"type": "text", "text": "第 37 张", "start": 9.9, "end": 12.5, "pos": [0.22, 0.4], "anim": "rise"},
    {"type": "box", "rect": [0.55, 0.3, 0.2, 0.12], "start": 22.0, "end": 25.0, "layer": "content"}
  ]
}
- 未列出的区间是人物全屏。首尾相接的内容段连成一组：组内小窗不来回缩放；
  content_full 夹在 content_pip 之间时小窗淡出／淡入，位于组的首尾时硬切。
- diameter、margin、focus_size 是输出短边的比例；focus 是头肩中心在人物画面中的归一化坐标。
  margin_x、margin_y 可分别覆盖水平、垂直边距（竖屏要让小窗上移避开平台底部说明区时用 margin_y）。
- 小窗默认没有描边，也没有阴影；border 设成大于 0 的数（按 1080 短边计的像素）才画描边。
- content 为图片（默认 contain）或视频（默认 cover，不取其声音）；为 null 时用纯色占位排版。
- overlays 里 layer 为 top 的项盖在所有画面之上（人物全屏时的关键词、字幕）；layer 为 content 的项
  画在内容层上，圆形小窗仍在它上面（内容图上的框选、标注）。复杂的图表动画先用别的工具导出成
  视频（作为 content）或 PNG 序列（作为 sequence 叠加项）再交给本脚本。
- 只处理一段连续的人物源区间。口播经过剪辑时，先按剪辑映射导出连续的人物视频再交给本脚本，或逐区间渲染。
"""
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

import overlay_kit
from _common import FrameReader, ffmpeg_exe, fit_image, imread, imwrite, media_info, parse_fps

PIP_DEFAULTS = {"corner": "bottom_right", "diameter": 0.2, "margin": 0.03, "transition": 0.5,
                "focus": [0.5, 0.38], "focus_size": 0.55, "border": 0,
                "border_color": [255, 255, 255], "hide_fade": 0.2,
                "margin_x": None, "margin_y": None}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
BG = (16, 16, 16)
EPS = 1e-6


def ease(x):
    x = min(1.0, max(0.0, x))
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def load_spec(path):
    path = Path(path)
    spec = json.loads(path.read_text(encoding="utf-8-sig"))
    base = path.parent
    spec["presenter"] = str((base / spec["presenter"]).resolve())
    spec["pip"] = {**PIP_DEFAULTS, **spec.get("pip", {})}
    segs = sorted(spec.get("segments", []), key=lambda s: s["start"])
    for i, s in enumerate(segs):
        if s["mode"] not in ("content_pip", "content_full"):
            sys.exit(f"段 {i}：mode 只能是 content_pip 或 content_full，收到 {s['mode']}")
        if s["end"] <= s["start"]:
            sys.exit(f"段 {i}：end 必须大于 start")
        if i and s["start"] < segs[i - 1]["end"] - EPS:
            sys.exit(f"段 {i} 与前一段时间重叠")
        c = s.get("content")
        if c:
            s["content"] = str((base / c).resolve())
            if not Path(s["content"]).exists():
                sys.exit(f"段 {i}：内容文件不存在 {s['content']}")
            s["_kind"] = "image" if Path(c).suffix.lower() in IMAGE_EXT else "video"
        else:
            s["_kind"] = "blank"
    # 首尾相接的段连成一组
    run = 0
    for i, s in enumerate(segs):
        if i and abs(s["start"] - segs[i - 1]["end"]) > EPS:
            run += 1
        s["_run"] = run
    spec["segments"] = segs
    spec["_base"] = base
    return spec


def check_spec(spec):
    """返回不阻断渲染、但应在交付前处理的问题。"""
    warns, segs, T = [], spec["segments"], spec["pip"]["transition"]
    runs = {}
    for s in segs:
        runs.setdefault(s["_run"], []).append(s)
    ordered = [runs[k] for k in sorted(runs)]
    for i, r in enumerate(ordered):
        a, b = r[0]["start"], r[-1]["end"]
        animated = (r[0]["mode"] == "content_pip") + (r[-1]["mode"] == "content_pip")
        if animated and b - a < max(3.0, T * animated + 1.0):
            warns.append(f"{a:.2f}–{b:.2f}s 的内容组不足 3 秒，小窗停不稳；改用叠加或延长")
        if i and a - ordered[i - 1][-1]["end"] < 2.0:
            warns.append(f"{ordered[i - 1][-1]['end']:.2f}–{a:.2f}s 人物只回到全屏不足 2 秒，"
                         f"考虑把前后两组接起来保持小窗")
    return warns


def state_at(t, segs, pip):
    """返回 (p, alpha, seg)。p：0 全屏矩形 -> 1 圆窗；alpha：人物不透明度。"""
    seg = next((s for s in segs if s["start"] - EPS <= t < s["end"] - EPS), None)
    if seg is None:
        return 0.0, 1.0, None
    run = [s for s in segs if s["_run"] == seg["_run"]]
    i = run.index(seg)
    T, F = pip["transition"], pip["hide_fade"]
    p_in = ease((t - run[0]["start"]) / T) if run[0]["mode"] == "content_pip" and T > 0 else 1.0
    p_out = ease((run[-1]["end"] - t) / T) if run[-1]["mode"] == "content_pip" and T > 0 else 1.0
    alpha = 1.0
    if seg["mode"] == "content_full":
        alpha = 0.0
        if F > 0 and i > 0 and run[i - 1]["mode"] == "content_pip":
            alpha = max(alpha, 1 - (t - seg["start"]) / F)
        if F > 0 and i < len(run) - 1 and run[i + 1]["mode"] == "content_pip":
            alpha = max(alpha, 1 - (seg["end"] - t) / F)
    return min(p_in, p_out), min(1.0, max(0.0, alpha)), seg


def geometry(p, W, H, pip):
    """窗口矩形、圆角半径、等比缩放系数 k 与源画面取景中心。全程等比，不挤压人脸。"""
    short = min(W, H)
    D = pip["diameter"] * short
    mx = (pip["margin"] if pip.get("margin_x") is None else pip["margin_x"]) * short
    my = (pip["margin"] if pip.get("margin_y") is None else pip["margin_y"]) * short
    cx = W - mx - D / 2 if "right" in pip["corner"] else mx + D / 2
    cy = H - my - D / 2 if "bottom" in pip["corner"] else my + D / 2
    w, h = W + (D - W) * p, H + (D - H) * p
    wcx, wcy = W / 2 + (cx - W / 2) * p, H / 2 + (cy - H / 2) * p
    S = pip["focus_size"] * short
    k = max(math.exp(p * math.log(D / S)), w / W, h / H)   # 取景不得超出源画面
    cw, ch = w / k, h / k
    fx, fy = pip["focus"][0] * W, pip["focus"][1] * H
    scx = min(max(W / 2 + (fx - W / 2) * p, cw / 2), W - cw / 2)
    scy = min(max(H / 2 + (fy - H / 2) * p, ch / 2), H - ch / 2)
    return wcx - w / 2, wcy - h / 2, w, h, p * min(w, h) / 2, k, scx, scy


_mask_cache = {}


def window_masks(x0, y0, bw, bh, wcx, wcy, w, h, r, border, cache_key=None):
    """抗锯齿圆角矩形：返回 (整体覆盖 A, 描边以内 inner)。r = 边长一半时是正圆。"""
    if cache_key in _mask_cache:
        return _mask_cache[cache_key]
    xs = np.abs(np.arange(x0, x0 + bw, dtype=np.float32) + 0.5 - wcx) - (w / 2 - r)
    ys = np.abs(np.arange(y0, y0 + bh, dtype=np.float32) + 0.5 - wcy) - (h / 2 - r)
    qx, qy = np.meshgrid(xs, ys)
    d = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - r
    A = np.clip(0.5 - d, 0, 1)
    inner = np.clip(0.5 - (d + border), 0, 1)
    if cache_key is not None:
        _mask_cache[cache_key] = (A, inner)
    return A, inner


def composite(content, pres, p, alpha, W, H, pip):
    if alpha <= 0:
        return content
    if p <= 0 and alpha >= 1:
        return pres
    x, y, w, h, r, k, scx, scy = geometry(p, W, H, pip)
    x0, y0 = max(0, math.floor(x)), max(0, math.floor(y))
    x1, y1 = min(W, math.ceil(x + w)), min(H, math.ceil(y + h))
    bw, bh = x1 - x0, y1 - y0
    # 大幅缩小时先做面积平均，避免双线性采样的锯齿
    f = 0.25 if k <= 0.3 else 0.5 if k <= 0.6 else 1.0
    src = cv2.resize(pres, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else pres
    ke = k / f
    M = np.float32([[ke, 0, 0.5 * ke - scx * k + (x + w / 2) - 0.5 - x0],
                    [0, ke, 0.5 * ke - scy * k + (y + h / 2) - 0.5 - y0]])
    warped = cv2.warpAffine(src, M, (bw, bh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    border = pip["border"] * p * min(W, H) / 1080
    A, inner = window_masks(x0, y0, bw, bh, x + w / 2, y + h / 2, w, h, r, border,
                            cache_key="pip" if p >= 1 else None)
    color = np.float32(pip["border_color"][::-1])
    layer = warped.astype(np.float32) * inner[..., None] + color * (A - inner)[..., None]
    out = content.copy()
    region = out[y0:y1, x0:x1].astype(np.float32)
    out[y0:y1, x0:x1] = np.clip(region * (1 - A * alpha)[..., None] + layer * alpha + 0.5, 0, 255)
    return out


class Contents:
    """按段提供内容层画面：图片缓存，视频按需打开并顺序读取。"""

    def __init__(self, size, fps):
        self.size, self.fps = size, fps
        self.images, self.readers, self.warnings = {}, {}, []
        self.blank = np.full((size[1], size[0], 3), BG, np.uint8)

    def frame(self, seg, t):
        if seg["_kind"] == "blank":
            return self.blank
        if seg["_kind"] == "image":
            key = (seg["content"], seg.get("fit", "contain"))
            if key not in self.images:
                self.images[key] = fit_image(imread(seg["content"]), self.size, key[1], BG)
            return self.images[key]
        key = id(seg)
        if key not in self.readers:
            start = seg.get("content_start", 0.0) + max(0.0, t - seg["start"])
            self.readers[key] = FrameReader(seg["content"], self.size, self.fps, start,
                                            seg.get("fit", "cover"), BG)
        rd = self.readers[key]
        img = rd.read()
        if img is None:
            if not seg.get("_exhausted"):
                seg["_exhausted"] = True
                self.warnings.append(f"内容视频在成片 {t:.2f}s 耗尽，之后停在最后一帧：{seg['content']}")
            return rd.last if rd.last is not None else self.blank
        return img

    def close(self):
        for rd in self.readers.values():
            rd.close()


def main():
    ap = argparse.ArgumentParser(description="人物全屏与圆形小窗的参考合成器")
    ap.add_argument("spec")
    ap.add_argument("--out", help="输出视频；省略时只出 --stills")
    ap.add_argument("--start", type=float, default=0.0, help="成片起点（秒）")
    ap.add_argument("--end", type=float, help="成片终点（秒，不含）")
    ap.add_argument("--stills", help="逗号分隔的成片时间点，另存 PNG")
    ap.add_argument("--stills-dir", default="stills")
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的输出")
    a = ap.parse_args()

    spec = load_spec(a.spec)
    W, H = spec["size"]
    fps = parse_fps(spec["fps"])
    pip, segs = spec["pip"], spec["segments"]
    src0 = float(spec.get("source_start", 0.0))
    info = media_info(spec["presenter"])
    if not info["has_video"]:
        sys.exit(f"人物源没有视频流：{spec['presenter']}")
    if not a.out and not a.stills:
        sys.exit("需要 --out 或 --stills")
    if a.out and Path(a.out).exists() and not a.overwrite:
        sys.exit(f"输出已存在，未覆盖：{a.out}（确认后加 --overwrite）")
    if a.out:
        Path(a.out).resolve().parent.mkdir(parents=True, exist_ok=True)
    overlays = overlay_kit.prepare(spec.get("overlays"), spec["_base"], (W, H), float(fps))

    def render(n, pres, contents):
        """成片第 n 帧：内容层（含 content 层叠加）→ 人物窗口 → top 层叠加。"""
        t = float(n / fps)
        p, alpha, seg = state_at(t, segs, pip)
        if seg is None:
            frame = pres
        else:
            content = overlay_kit.draw_layer(contents.frame(seg, t), overlays, t, "content")
            frame = composite(content, pres, p, alpha, W, H, pip)
        return overlay_kit.draw_layer(frame, overlays, t, "top")

    total = spec.get("duration")
    if total is None and info["duration"] is not None:
        total = info["duration"] - src0
    end = min(x for x in (a.end, total) if x is not None) if (a.end or total) else None
    n0 = math.ceil(a.start * fps - EPS)
    n1 = math.floor(end * fps + EPS) if end is not None else None
    warnings = check_spec(spec)
    if total is not None:
        warnings += [f"段 {s['start']:.2f}–{s['end']:.2f}s 超出成片时长 {total:.2f}s"
                     for s in segs if s["end"] > total + 0.05]

    stills = {}
    if a.stills:
        Path(a.stills_dir).mkdir(parents=True, exist_ok=True)
        stills = {round(float(x) * fps): float(x) for x in a.stills.split(",") if x.strip()}

    if stills and not a.out:
        # 只出静帧：每个时间点直接定位到对应源帧，不从片头逐帧合成
        saved = []
        for n in sorted(stills):
            rd = FrameReader(spec["presenter"], (W, H), fps, src0 + float(n / fps), "cover", BG)
            cs = Contents((W, H), fps)
            pres = rd.read()
            if pres is None:
                warnings.append(f"成片 {stills[n]:.3f}s 取不到人物帧（超出源片时长？）")
            else:
                path = Path(a.stills_dir) / f"still_{stills[n]:09.3f}.png"
                imwrite(path, render(n, pres, cs))
                saved.append(str(path))
            rd.close()
            cs.close()
            warnings += cs.warnings
        print(json.dumps({"out": None, "stills": saved, "warnings": warnings}, ensure_ascii=False, indent=2))
        return

    reader = FrameReader(spec["presenter"], (W, H), fps, src0 + float(n0 / fps), "cover", BG)
    contents = Contents((W, H), fps)
    writer = None
    if a.out:
        cmd = [ffmpeg_exe(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
               "-s", f"{W}x{H}", "-r", f"{fps.numerator}/{fps.denominator}", "-i", "-"]
        if info["has_audio"]:
            cmd += ["-ss", f"{src0 + float(n0 / fps):.6f}"]
            if n1 is not None:
                cmd += ["-t", f"{float((n1 - n0) / fps):.6f}"]
            cmd += ["-i", spec["presenter"], "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(a.crf), "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(a.out)]
        writer = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    n, saved = n0, []
    try:
        while n1 is None or n < n1:
            pres = reader.read()
            if pres is None:
                if n1 is not None and (n1 - n) / fps > 0.1:
                    warnings.append(f"人物视频在成片 {float(n / fps):.2f}s 提前结束，少于预期 {float(n1 / fps):.2f}s")
                break
            frame = render(n, pres, contents)
            if writer:
                writer.stdin.write(frame.tobytes())
            if n in stills:
                path = Path(a.stills_dir) / f"still_{stills[n]:09.3f}.png"
                imwrite(path, frame)
                saved.append(str(path))
            n += 1
    finally:
        reader.close()
        contents.close()
        if writer:
            writer.stdin.close()
            code = writer.wait()
            if code:
                sys.exit(f"ffmpeg 编码失败，退出码 {code}")

    print(json.dumps({
        "out": a.out, "frames": n - n0, "fps": str(fps),
        "output_range": [round(float(n0 / fps), 3), round(float(n / fps), 3)],
        "source_range": [round(src0 + float(n0 / fps), 3), round(src0 + float(n / fps), 3)],
        "audio": bool(a.out and info["has_audio"]), "stills": saved,
        "warnings": warnings + contents.warnings,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
