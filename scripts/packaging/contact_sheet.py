"""抽帧拼联系表，每格标注时间。用于看内容与布局；运动、口型和音画同步仍要播放连续片段。

用法：
  python contact_sheet.py <视频> --every 6 --out sheet.jpg
  python contact_sheet.py <视频> --times 12.0,12.2,12.4,12.6 --out pip_in.jpg
超过 --per-sheet 格时自动拆成 sheet_01.jpg、sheet_02.jpg……
"""
import argparse
import math
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

from _common import ffmpeg_exe, imwrite, media_info


def grab(path, t, width):
    cmd = [ffmpeg_exe(), "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
           "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"]
    data = subprocess.run(cmd, capture_output=True).stdout
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None


def label(img, t):
    text = f"{int(t // 60):02d}:{t % 60:06.3f}"
    cv2.rectangle(img, (0, 0), (150, 26), (0, 0, 0), -1)
    cv2.putText(img, text, (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    return img


def main():
    ap = argparse.ArgumentParser(description="抽帧拼联系表")
    ap.add_argument("video")
    ap.add_argument("--out", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--every", type=float, help="每隔多少秒取一帧")
    g.add_argument("--times", help="逗号分隔的时间点（秒）")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float)
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--width", type=int, default=480, help="每格宽度")
    ap.add_argument("--per-sheet", type=int, default=40)
    a = ap.parse_args()

    if a.times:
        times = [float(x) for x in a.times.split(",") if x.strip()]
    else:
        end = a.end if a.end is not None else media_info(a.video)["duration"]
        if end is None:
            sys.exit("读不到时长，请用 --end 或 --times")
        times = [a.start + i * a.every for i in range(int(math.ceil((end - a.start) / a.every)))]

    cells = []
    for t in times:
        img = grab(a.video, t, a.width)
        if img is None:
            print(f"跳过 {t:.3f}s：取不到帧", file=sys.stderr)
            continue
        cells.append(label(img, t))
    if not cells:
        sys.exit("没有取到任何帧")

    out = Path(a.out)
    sheets = [cells[i:i + a.per_sheet] for i in range(0, len(cells), a.per_sheet)]
    for n, group in enumerate(sheets, 1):
        h, w = group[0].shape[:2]
        cols = min(a.cols, len(group))
        rows = math.ceil(len(group) / cols)
        sheet = np.zeros((rows * h, cols * w, 3), np.uint8)
        for i, img in enumerate(group):
            r, c = divmod(i, cols)
            sheet[r * h:(r + 1) * h, c * w:(c + 1) * w] = cv2.resize(img, (w, h))
        path = out if len(sheets) == 1 else out.with_name(f"{out.stem}_{n:02d}{out.suffix}")
        imwrite(path, sheet)
        print(f"{path}  {len(group)} 帧")


if __name__ == "__main__":
    main()
