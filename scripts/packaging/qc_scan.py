"""技术层巡检：黑帧、画面冻结、长静音、时长。输出 JSON。

用法：python qc_scan.py <成片> [--expect-duration 62.5]
结果是待人工确认的候选：静态图表或章节卡会被报成冻结，讲者停顿会被报成静音。
本脚本不检查原声是否错位、小窗是否卡住（用 av_check.py），也不检查遮挡和可读性（看抽帧）；口型是否对准要人播放确认。
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from _common import ffmpeg_exe, media_info


def scan(path, black_d, freeze_d, silence_d):
    if not Path(path).is_file():
        sys.exit(f"文件不存在：{path}")
    info = media_info(path)
    if not info["has_video"] or info["duration"] is None:
        sys.exit(f"读不到视频流或时长，无法巡检：{path}")
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostats", "-i", str(path),
           "-vf", f"blackdetect=d={black_d}:pix_th=0.10,freezedetect=n=-60dB:d={freeze_d}"]
    if info["has_audio"]:
        cmd += ["-af", f"silencedetect=n=-50dB:d={silence_d}"]
    cmd += ["-f", "null", "-"]
    err = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace").stderr

    black = [{"start": float(s), "end": float(e)} for s, e in
             re.findall(r"black_start:([\d.]+)\s+black_end:([\d.]+)", err)]
    f_starts = [float(x) for x in re.findall(r"freeze_start:\s*([\d.]+)", err)]
    f_ends = [float(x) for x in re.findall(r"freeze_end:\s*([\d.]+)", err)]
    freeze = [{"start": s, "end": f_ends[i] if i < len(f_ends) else info["duration"]}
              for i, s in enumerate(f_starts)]
    s_starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", err)]
    s_ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", err)]
    silence = [{"start": max(0.0, s), "end": s_ends[i] if i < len(s_ends) else info["duration"]}
               for i, s in enumerate(s_starts)]
    return {"path": str(path), "duration": info["duration"], "has_audio": info["has_audio"],
            "black": black, "freeze": freeze, "silence": silence}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="黑帧／冻结／静音巡检")
    ap.add_argument("video")
    ap.add_argument("--expect-duration", type=float, help="预期成片时长（秒），偏差超过 0.1s 会标出")
    ap.add_argument("--black", type=float, default=0.1, help="黑帧最短时长")
    ap.add_argument("--freeze", type=float, default=1.0, help="冻结最短时长")
    ap.add_argument("--silence", type=float, default=2.0, help="静音最短时长")
    a = ap.parse_args()
    r = scan(a.video, a.black, a.freeze, a.silence)
    if a.expect_duration is not None and r["duration"] is not None:
        r["duration_diff"] = round(r["duration"] - a.expect_duration, 3)
        r["duration_ok"] = abs(r["duration_diff"]) <= 0.1
    print(json.dumps(r, ensure_ascii=False, indent=2))
