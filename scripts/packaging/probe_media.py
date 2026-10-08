"""探测源媒体：时长、显示尺寸与方向、帧率特征、音轨。输出 JSON。

用法：python probe_media.py <媒体文件> [--frames 300]
依赖 PyAV（pip install av）。没有 PyAV 时改用：
  ffprobe -v error -show_format -show_streams -of json <媒体文件>
"""
import argparse
import json
import sys

import _common  # noqa: F401  统一 stdout 编码

try:
    import av
except ImportError:
    sys.exit(__doc__)


def rotation_of(stream, frame):
    """显示旋转角（度）。不同 PyAV 版本暴露位置不同，逐个尝试。"""
    for getter in (lambda: frame.rotation, lambda: stream.side_data.get("DISPLAYMATRIX"),
                   lambda: stream.metadata.get("rotate")):
        try:
            v = getter()
            if v is not None:
                return int(round(float(v))) % 360
        except Exception:
            pass
    return 0


def probe(path, sample_frames):
    out = {"path": str(path), "video": [], "audio": [], "subtitles": 0}
    with av.open(str(path)) as c:
        out["container"] = c.format.name
        out["duration"] = round(c.duration / av.time_base, 3) if c.duration else None
        for s in c.streams:
            if s.type == "audio":
                out["audio"].append({
                    "index": s.index, "codec": s.codec_context.name, "sample_rate": s.rate,
                    "channels": s.codec_context.channels,
                    "duration": round(float(s.duration * s.time_base), 3) if s.duration else None})
            elif s.type == "subtitle":
                out["subtitles"] += 1
        for s in c.streams.video:
            # 取前若干帧的时间戳，判断是否可变帧率
            pts, first = [], None
            c.seek(0)
            for frame in c.decode(s):
                first = first or frame
                if frame.pts is not None:
                    pts.append(float(frame.pts * s.time_base))
                if len(pts) >= sample_frames:
                    break
            gaps = sorted(round(b - a, 5) for a, b in zip(pts, pts[1:]))
            rot = rotation_of(s, first) if first else 0
            w, h = s.codec_context.width, s.codec_context.height
            sar = s.sample_aspect_ratio or 1
            dw, dh = round(w * float(sar)), h
            if rot in (90, 270):
                dw, dh = dh, dw
            info = {
                "index": s.index, "codec": s.codec_context.name,
                "coded_size": [w, h], "rotation": rot, "display_size": [dw, dh],
                "display_aspect": round(dw / dh, 4) if dh else None,
                "orientation": "portrait" if dh > dw else "landscape" if dw > dh else "square",
                "avg_fps": round(float(s.average_rate), 4) if s.average_rate else None,
                "nominal_fps": round(float(s.base_rate), 4) if s.base_rate else None,
                "frames": s.frames or None,
                "duration": round(float(s.duration * s.time_base), 3) if s.duration else None,
                "start_time": pts[0] if pts else None,
            }
            if gaps:
                info["frame_gap_ms"] = {"min": round(gaps[0] * 1000, 2),
                                        "median": round(gaps[len(gaps) // 2] * 1000, 2),
                                        "max": round(gaps[-1] * 1000, 2), "sampled": len(pts)}
                # 帧间隔相差超过 2ms 视为可变帧率：按源时间戳取帧，不要用帧号除名义帧率
                info["variable_frame_rate"] = gaps[-1] - gaps[0] > 0.002
            out["video"].append(info)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="探测源媒体并输出 JSON")
    ap.add_argument("media")
    ap.add_argument("--frames", type=int, default=300, help="用于判断可变帧率的采样帧数")
    a = ap.parse_args()
    print(json.dumps(probe(a.media, a.frames), ensure_ascii=False, indent=2))
