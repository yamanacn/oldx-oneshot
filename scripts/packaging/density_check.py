"""量画面空不空：每一帧里“和底色一样”的面积占多少、最大的一块空地有多大；可以和参考片比，也可以把两边的帧并排拼成一张图。

用法：
  python density_check.py <成片或样段> --every 8
  python density_check.py <成片> --times 16.5,42,80 --ref <参考片> --ref-every 5 --side-by-side cmp.jpg
  python density_check.py --images out/still_*.png            # 直接量静帧

它回答的是“这一帧是不是明显偏空”，用来提醒，不是硬指标：
  blank      和底色接近的像素占整帧的比例（底色取这一帧里最常见的颜色）
  dead_zone  最大的一块全是底色的矩形占整帧的比例——一大片什么都没有的地方
人物全屏的帧底色不成片，两个数都会很低，不用看。
给了 --ref 时，阈值取参考片各帧的中位数再放宽一点；没给时用默认值（blank > 0.80 或 dead_zone > 0.30 记为偏空）。
量出来偏空不等于一定要加东西——留白可以是设计——但要能说出理由；说不出就是漏了。
"""
import argparse
import glob
import json
import sys
from pathlib import Path

import cv2
import numpy as np

import _common  # noqa: F401  统一 stdout 编码
from _common import imwrite, media_info
from contact_sheet import grab


def read_image(path):
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)   # 中文路径也能读


def measure(img, tol=14):
    """返回 (blank, dead_zone)。先缩小再量，细小的纹理（点阵、噪点）不算内容"""
    small = cv2.resize(img, (160, 90), interpolation=cv2.INTER_AREA)
    q = (small // 16).reshape(-1, 3)
    keys, counts = np.unique(q, axis=0, return_counts=True)
    bg = keys[counts.argmax()].astype(np.int32) * 16 + 8
    mask = (np.abs(small.astype(np.int32) - bg).max(axis=2) <= tol)
    blank = float(mask.mean())
    # 最大的全空矩形（按行累计高度，再求每一行的最大矩形）
    h, w = mask.shape
    heights = np.zeros(w, np.int32)
    best = 0
    for y in range(h):
        heights = np.where(mask[y], heights + 1, 0)
        stack = []
        for x in range(w + 1):
            cur = heights[x] if x < w else 0
            start = x
            while stack and stack[-1][1] >= cur:
                sx, sh = stack.pop()
                best = max(best, sh * (x - sx))
                start = sx
            stack.append((start, cur))
    return round(blank, 3), round(float(best) / (h * w), 3)


def frames_of(video, times, every):
    if not times:
        dur = media_info(video)["duration"]
        times = [round(t, 2) for t in np.arange(every / 2, dur, every)]
    out = []
    for t in times:
        f = grab(video, t, 960)
        if f is not None:
            out.append((t, f))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", nargs="?")
    ap.add_argument("--images", nargs="*", help="直接量这些图片（可以用通配符）")
    ap.add_argument("--times", help="逗号分隔的秒数")
    ap.add_argument("--every", type=float, default=8.0)
    ap.add_argument("--ref", help="参考片")
    ap.add_argument("--ref-times")
    ap.add_argument("--ref-every", type=float, default=5.0)
    ap.add_argument("--side-by-side", help="把参考片的帧和自己的帧并排拼成这张图")
    ap.add_argument("--out", help="把结果写成 JSON")
    a = ap.parse_args()

    if a.images:
        paths = [p for pat in a.images for p in sorted(glob.glob(pat))]
        mine = [(Path(p).name, read_image(p)) for p in paths]
    elif a.video:
        mine = frames_of(a.video, [float(x) for x in a.times.split(",")] if a.times else None, a.every)
    else:
        sys.exit("给一个视频，或用 --images 给一组静帧")
    rows = [{"frame": k, **dict(zip(("blank", "dead_zone"), measure(f)))} for k, f in mine]

    lim_blank, lim_dead, ref_rows = 0.80, 0.30, []
    if a.ref:
        ref = frames_of(a.ref, [float(x) for x in a.ref_times.split(",")] if a.ref_times else None, a.ref_every)
        ref_rows = [{"frame": k, **dict(zip(("blank", "dead_zone"), measure(f)))} for k, f in ref]
        if ref_rows:
            lim_blank = round(float(np.median([r["blank"] for r in ref_rows])) + 0.08, 3)
            lim_dead = round(float(np.median([r["dead_zone"] for r in ref_rows])) + 0.08, 3)
    for r in rows:
        full = r["blank"] < 0.25          # 底色不成片：多半是人物全屏
        r["sparse"] = bool((not full) and (r["blank"] > lim_blank or r["dead_zone"] > lim_dead))
    result = {"limit": {"blank": lim_blank, "dead_zone": lim_dead, "from": "reference" if ref_rows else "default"},
              "reference": {"frames": len(ref_rows), "blank_median": float(np.median([r["blank"] for r in ref_rows])) if ref_rows else None,
                            "dead_zone_median": float(np.median([r["dead_zone"] for r in ref_rows])) if ref_rows else None},
              "frames": rows, "sparse": [r["frame"] for r in rows if r["sparse"]]}

    if a.side_by_side and a.ref and mine:
        n = min(len(mine), len(ref), 8)
        pick = lambda xs: [xs[int(i * (len(xs) - 1) / max(1, n - 1))] for i in range(n)]
        cell = lambda f: cv2.resize(f, (640, 360), interpolation=cv2.INTER_AREA)
        left = np.vstack([cell(f) for _, f in pick(ref)])
        right = np.vstack([cell(f) for _, f in pick(mine)])
        sheet = np.hstack([left, np.full((left.shape[0], 12, 3), 255, np.uint8), right])
        cv2.putText(sheet, "REFERENCE", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        cv2.putText(sheet, "MINE", (662, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        imwrite(a.side_by_side, sheet)
        result["side_by_side"] = str(a.side_by_side)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
