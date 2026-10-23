"""演示录屏的预处理：裁掉浏览器边框 / 任务栏 → 缩到指定宽度、转成恒定帧率 → 敏感区域在转码时就烘焙高斯模糊。
工程里只放处理后的文件，明文的 Key、账号、余额、历史对话不进工程，也不进交付文件夹。

  python demo_prep.py spec.json
  python demo_prep.py spec.json --check 12.5,18,23.4      # 处理完在这几个时刻抽帧拼图，检查遮挡对不对（打码最容易出错的是弹窗出现的那几秒）

spec.json：
  {"src": "原始录屏.mp4", "out": "工程/public/assets/demo_xxx.mp4",
   "crop": [x, y, w, h],            源视频像素：要保留的区域（4K 录屏裁掉标签页、地址栏、任务栏）
   "width": 2880,                   输出宽度：比成片宽一些，推近 1.5–2 倍时字仍然清楚（1920 宽的成片用 2880）
   "fps": 60,                       输出帧率：录屏多是可变帧率，转成恒定帧率才能按时间精确取帧
   "masks": [{"rect": [x, y, w, h], "from": 秒, "to": 秒}]}
                                    遮挡区：坐标写在"输出缩到 1920 宽"的坐标系里（看 1920 宽的抽帧量），
                                    from/to 不写就是全程；弹窗出现、页面切换时遮挡区要分时段写
经验（2026-10 一条教程片）：
  - 新建 API Key 的弹窗、"企业级-共享"列表里的明文 Key、右上角余额和头像、侧栏历史对话，这几类每次都要遮；
  - 弹窗在某一秒出现时，页面底下原本的遮挡区可能落在弹窗上、盖住要看的按钮：按弹窗出现的时刻把遮挡区拆开，只遮弹窗外的部分；
  - 加载白屏、页面闪一下的几秒不用剪：在合成里用变速锚点跳过（两个锚点成片时间相同即可）。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from _common import ffmpeg_exe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--check", help="逗号分隔的时刻（秒）：处理完在这几个时刻抽帧拼成 <out>_check.jpg")
    a = ap.parse_args()
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    cx, cy, cw, ch = spec["crop"]
    W = int(spec.get("width", 1920))
    H = int(round(ch * W / cw / 2) * 2)
    fps = int(spec.get("fps", 60))
    k = W / 1920
    chain = [f"[0:v]fps={fps},crop={cw}:{ch}:{cx}:{cy},scale={W}:{H}:flags=lanczos,setsar=1[b0]"]
    cur = "b0"
    for i, m in enumerate(spec.get("masks", [])):
        x, y, w, h = [int(round(v * k)) for v in m["rect"]]
        x, y = max(0, x), max(0, y)
        w, h = min(w, W - x), min(h, H - y)
        if w <= 0 or h <= 0:
            sys.exit(f"遮挡区 {i} 落在画面外：{m['rect']}")
        en = f":enable='between(t,{m['from']},{m['to']})'" if "from" in m else ""
        chain.append(f"[{cur}]split[s{i}a][s{i}b];[s{i}b]crop={w}:{h}:{x}:{y},gblur=sigma={max(8, int(22 * k))}:steps=4[k{i}];"
                     f"[s{i}a][k{i}]overlay={x}:{y}{en}[b{i + 1}]")
        cur = f"b{i + 1}"
    out = Path(spec["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg_exe(), "-y", "-v", "error", "-i", spec["src"], "-filter_complex", ";".join(chain), "-map", f"[{cur}]", "-an",
                    "-c:v", "libx264", "-crf", "14", "-preset", "medium", "-pix_fmt", "yuv420p", "-r", str(fps), str(out)], check=True)
    res = {"out": str(out), "size": [W, H], "fps": fps, "masks": len(spec.get("masks", []))}
    if a.check:
        sheet = out.with_name(out.stem + "_check.jpg")
        subprocess.run([sys.executable, str(Path(__file__).with_name("contact_sheet.py")), str(out), "--times", a.check, "--cols", "2",
                        "--width", "960", "--out", str(sheet)], check=True, capture_output=True)
        res["check"] = str(sheet)
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
