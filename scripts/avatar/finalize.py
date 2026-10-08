"""拼接各段数字人视频并质检：<workdir>/avatar/final_avatar.mp4 + seams.png。

  python finalize.py --workdir <项目目录>

做法：
  * 每段视频按各自的音频时长裁掉比音频多出来的帧（云端返回的视频会比音频略长），统一帧率后依次拼接；
  * 声音不用云端回传的音轨，直接用项目里的 out/merged.wav（原配音），避免二次压缩和时间漂移；
  * 质检：分辨率、时长与配音差、黑帧和冻结候选，并在每个接缝前后各取两帧拼成 seams.png 供人工查看。
口型是否对准只能听看成片确认，脚本不判断。
"""
import argparse
import json
import os
import re
import subprocess
import sys

import cv2
import numpy as np




def _imwrite(path, img):
    """cv2.imwrite 在带中文的路径上会悄悄失败（不报错、也不写文件），改成先编码再用 numpy 写出"""
    ok, buf = cv2.imencode(os.path.splitext(str(path))[1] or ".png", img)
    if not ok:
        raise RuntimeError("图片编码失败：%s" % path)
    buf.tofile(str(path))
    return True

def ffmpeg():
    import shutil
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        sys.exit("找不到 ffmpeg：请安装 ffmpeg 并加入 PATH，或 pip install imageio-ffmpeg")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--fps", type=int, default=24)
    a = ap.parse_args()
    od = os.path.join(a.workdir, "avatar")
    plan = json.load(open(os.path.join(od, "plan.json"), encoding="utf-8"))
    info = json.load(open(os.path.join(od, "image_info.json"), encoding="utf-8"))
    res = json.load(open(os.path.join(od, "result.json"), encoding="utf-8"))["results"]
    segs = plan["segments"]
    missing = [s["i"] for s in segs if str(s["i"]) not in res or not os.path.isfile(res[str(s["i"])]["file"])]
    if missing:
        sys.exit("这些段还没有视频：%s。先用 run_avatar.py 跑完。" % missing)
    audio = os.path.join(a.workdir, "out", "merged.wav")
    total = plan["total_seconds"]
    w, h = info["width"], info["height"]
    ff = ffmpeg()
    cmd = [ff, "-y", "-v", "error"]
    for s in segs:
        cmd += ["-i", res[str(s["i"])]["file"]]
    cmd += ["-i", audio]
    parts = []
    for k, s in enumerate(segs):
        parts.append("[%d:v]fps=%d,trim=duration=%.4f,setpts=PTS-STARTPTS,scale=%d:%d[v%d]" % (k, a.fps, s["duration"], w, h, k))
    fc = ";".join(parts) + ";" + "".join("[v%d]" % k for k in range(len(segs))) + "concat=n=%d:v=1:a=0[v]" % len(segs)
    final = os.path.join(od, "final_avatar.mp4")
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "%d:a" % len(segs), "-t", "%.4f" % total,
            "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", final]
    subprocess.run(cmd, check=True)

    # ---- 质检
    cap = cv2.VideoCapture(final)
    fw, fh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS)
    vdur = n / fps if fps else 0
    checks = [("分辨率", "%d×%d" % (fw, fh), (fw, fh) == (w, h)),
              ("视频时长", "%.2fs（配音 %.2fs，差 %.2fs）" % (vdur, total, vdur - total), abs(vdur - total) <= 0.1)]
    r = subprocess.run([ff, "-i", final, "-an", "-vf", "blackdetect=d=0.1:pic_th=0.98,freezedetect=n=-60dB:d=1.0", "-f", "null", "-"],
                       capture_output=True)
    err = r.stderr.decode("utf-8", "replace")
    blacks = re.findall(r"black_start:([\d.]+)", err)
    freezes = re.findall(r"freeze_start: ([\d.]+)", err)
    checks.append(("黑帧", "无" if not blacks else "候选起点 " + ",".join(blacks), not blacks))
    checks.append(("冻结≥1秒", "无" if not freezes else "候选起点 " + ",".join(freezes), not freezes))
    # 接缝帧
    rows = []
    for s in segs[:-1]:
        cut = s["end"]
        row = []
        for dt in (-0.12, -0.04, 0.04, 0.12):
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round((cut + dt) * fps))))
            ok, fr = cap.read()
            if ok:
                fr = cv2.resize(fr, (480, int(480 * fh / fw)))
                cv2.putText(fr, "cut %.2fs %+.2f" % (cut, dt), (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                row.append(fr)
        if row:
            rows.append(np.hstack(row))
    if rows:
        _imwrite(os.path.join(od, "seams.png"), np.vstack(rows))
    cap.release()
    print("成片：", final)
    for name, val, ok in checks:
        print("%s %s：%s" % ("✓" if ok else "✗", name, val))
    if rows:
        print("接缝检查图（每行一个接缝，前后各两帧）：", os.path.join(od, "seams.png"))
    print("未做：口型同步、手势细节、人物一致性——需要人看和听成片确认。")
    mp = os.path.join(a.workdir, "meta.json")        # 项目目录里写回进度，后续环节可读
    if os.path.isfile(mp):
        m = json.load(open(mp, encoding="utf-8"))
        m["avatar_video"] = final
        json.dump(m, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if not all(ok for _, _, ok in checks):
        sys.exit(1)


if __name__ == "__main__":
    main()
