"""不靠人眼和耳朵也能做的声画检查：原声是否原样保留、小窗里的人物是否在动、每次进退场的过渡抽帧。

用法：python av_check.py spec.json 成片.mp4 [--out-dir qc]
输出 JSON，三项检查：
  audio        成片人声与人物源对应区间逐样本比对：相关系数和时间偏移。相关 ≥0.98 且偏移 ≤0.02 秒记为 passed。
               加了配乐或音效时相关会下降，这时只看偏移，并在交付里说明。
  pip_motion   每个圆形小窗停稳的区间里，对比小窗区域相邻帧：源片这时人物在动而小窗不动，记为 needs_fix（小窗卡住）。
  transitions  每次小窗进场、退场各抽 6 帧拼成一张图，路径写在结果里——**用看图工具打开逐张看**：
               形状是否正圆、有没有黑边或露底、有没有挡住内容里的重点。
这些检查替代不了人看成片：口型是否逐字对准、节奏是否舒服、字是否读得完，仍要交给人播放确认。
"""
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

import pip_render as pr
from _common import ffmpeg_exe, imwrite, media_info, parse_fps


def pcm(path, start, dur, sr=16000):
    cmd = [ffmpeg_exe(), "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(path),
           "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.int16).astype(np.float32)


def grab(path, t, size=None):
    vf = ["-vf", f"scale={size[0]}:{size[1]}"] if size else []
    cmd = [ffmpeg_exe(), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1", *vf,
           "-f", "image2pipe", "-vcodec", "png", "-"]
    data = subprocess.run(cmd, capture_output=True).stdout
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None


def check_audio(spec, video, dur):
    src0 = float(spec.get("source_start", 0.0))
    n = min(dur, 120.0)                                   # 取开头最多 2 分钟
    a, b = pcm(spec["presenter"], src0, n), pcm(video, 0.0, n)
    m = min(len(a), len(b))
    if m < 16000:
        return {"status": "not_run", "reason": "成片或人物源没有足够的音频"}
    a, b = a[:m], b[:m]
    step = 8
    c = np.correlate(a[::step], b[::step], "full")
    lag = (int(c.argmax()) - (len(a[::step]) - 1)) * step / 16000
    corr = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else 0.0
    ok = corr >= 0.98 and abs(lag) <= 0.02
    return {"status": "passed" if ok else "needs_fix", "correlation": round(corr, 4), "offset_seconds": round(lag, 3),
            "compared_seconds": round(m / 16000, 1)}


def check_pip_motion(spec, video, W, H):
    pip, segs = spec["pip"], spec["segments"]
    x, y, w, h, *_ = pr.geometry(1.0, W, H, pip)
    x0, y0, x1, y1 = int(x), int(y), int(x + w), int(y + h)
    src0, T = float(spec.get("source_start", 0.0)), pip["transition"]
    rows = []
    for s in segs:
        if s["mode"] != "content_pip":
            continue
        a, b = s["start"] + T + 0.1, s["end"] - T - 0.4
        if b <= a:
            continue
        moving_src, moving_pip, samples = 0, 0, 0
        for t in np.arange(a, b, max(1.0, (b - a) / 6)):
            f1, f2 = grab(video, t, (W, H)), grab(video, t + 0.3, (W, H))
            g1, g2 = grab(spec["presenter"], src0 + t, (480, 270)), grab(spec["presenter"], src0 + t + 0.3, (480, 270))
            if f1 is None or f2 is None or g1 is None or g2 is None:
                continue
            samples += 1
            d_pip = float(np.abs(f1[y0:y1, x0:x1].astype(np.float32) - f2[y0:y1, x0:x1]).mean())
            d_src = float(np.abs(g1.astype(np.float32) - g2).mean())
            moving_src += d_src > 0.8
            moving_pip += d_pip > 0.8
        status = "not_run" if not samples else "needs_fix" if moving_src >= 2 and moving_pip == 0 else "passed"
        rows.append({"segment": [s["start"], s["end"]], "status": status, "samples": samples,
                     "source_moving": int(moving_src), "pip_moving": int(moving_pip)})
    return rows


def transition_sheets(spec, video, out_dir, W, H):
    pip, segs = spec["pip"], spec["segments"]
    T = pip["transition"]
    runs = {}
    for s in segs:
        runs.setdefault(s["_run"], []).append(s)
    sheets = []
    for k in sorted(runs):
        r = runs[k]
        points = []
        if r[0]["mode"] == "content_pip":
            points.append(("in", r[0]["start"]))
        if r[-1]["mode"] == "content_pip":
            points.append(("out", r[-1]["end"] - T))
        for name, t0 in points:
            times = [t0 - 0.1 + i * (T + 0.2) / 5 for i in range(6)]
            cells = []
            for t in times:
                f = grab(video, t, (480, round(480 * H / W)))
                if f is None:
                    continue
                cv2.rectangle(f, (0, 0), (120, 24), (0, 0, 0), -1)
                cv2.putText(f, f"{t:.2f}s", (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
                cells.append(f)
            if cells:
                path = Path(out_dir) / f"transition_{name}_{t0:08.2f}.jpg"
                imwrite(path, np.hstack(cells))
                sheets.append(str(path))
    return sheets


def main():
    ap = argparse.ArgumentParser(description="原声、小窗运动、过渡抽帧检查")
    ap.add_argument("spec")
    ap.add_argument("video")
    ap.add_argument("--out-dir", default="qc")
    a = ap.parse_args()
    if not Path(a.video).is_file():
        sys.exit(f"成片不存在：{a.video}")
    spec = pr.load_spec(a.spec)
    W, H = spec["size"]
    info = media_info(a.video)
    if info["duration"] is None:
        sys.exit(f"读不到成片时长：{a.video}")
    Path(a.out_dir).mkdir(parents=True, exist_ok=True)
    result = {"video": str(a.video), "duration": info["duration"],
              "audio": check_audio(spec, a.video, info["duration"]) if info["has_audio"] else {"status": "needs_fix", "reason": "成片没有音轨"},
              "pip_motion": check_pip_motion(spec, a.video, W, H),
              "transitions": transition_sheets(spec, a.video, a.out_dir, W, H),
              "left_for_human": ["口型是否逐字对准", "节奏和停留是否舒服", "屏上文字在手机上是否读得完", "整体观感"]}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
