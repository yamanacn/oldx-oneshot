"""拼接各段数字人视频并质检：<workdir>/avatar/final_avatar.mp4。

  python finalize.py --workdir <项目目录>

做法：
  * 每段视频按各自的音频时长裁掉比音频多出来的帧（云端返回的视频会比音频略长），统一帧率后依次拼接；
  * 从第 2 段起，每段开头裁掉 --head-frames 帧（默认 4）：片段开头的几帧几乎不动，接在上一段后面会顿一下。
    同时从配音里裁掉同样长的一段开头静音，口型不错位；最多裁到这一段第一个字之前 0.08 秒，不剪到字头。
    所以成片比 merged.wav 略短（每个接缝约 0.17 秒）：各段在成片上的起止、配音时间和成片时间的对应写在
    avatar/finalize.json，换算到成片时间轴上的逐词时间写在 avatar/words_final.json（后期包装用它）。
    2026-10-14 同一份素材对比：接缝后画面不动的帧平均从 2.1 帧降到 0.2 帧。--head-frames 0 就是不裁。
  * 声音不用云端回传的音轨，直接用项目里的 out/merged.wav（原配音），避免二次压缩和时间漂移；
    裁过开头之后和成片对齐的那条配音另存为 avatar/voice_final.wav（无损），后期包装用它，不用 merged.wav；
  * 质检：分辨率、时长与配音差、黑帧和冻结候选。接缝和镜头的检查在 qa_video.py（调用 qc_frames.py）里做。
口型是否对准只能听看成片确认，脚本不判断。
"""
import argparse
import json
import os
import re
import subprocess
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _cli  # noqa: E402

import cv2

from _util import ffmpeg_exe as ffmpeg  # noqa: E402

HEAD_FRAMES = 4      # 片段开头几乎不动的帧数（实测 2–4 帧，少数段更长）
WORD_PAD = 0.08      # 裁开头时离第一个字至少留这么多


def head_cuts(segs, fps, head_frames):
    """每段开头裁掉几帧：第 1 段不裁；其余不超过 head_frames，也不超过第一个字之前 WORD_PAD 秒"""
    cuts = []
    for k, s in enumerate(segs):
        first = s["words"][0]["start"] if s.get("words") else 0.0
        cuts.append(0 if k == 0 or head_frames <= 0 else max(0, min(head_frames, int((first - WORD_PAD) * fps + 1e-6))))
    return cuts


def final_timeline(segs, cuts, fps):
    """成片上每段的起止，以及配音时间 → 成片时间的对应（map）"""
    rows, mapping, t = [], [], 0.0
    for s, n in zip(segs, cuts):
        h = n / fps
        d = s["duration"] - h
        rows.append({"i": s["i"], "start": round(t, 4), "end": round(t + d, 4), "duration": round(d, 4), "head": round(h, 4), "head_frames": n})
        mapping.append({"src_start": round(s["start"] + h, 4), "src_end": round(s["end"], 4), "out_start": round(t, 4)})
        t += d
    return rows, mapping, t


def write_voice_final(merged, mapping, dst):
    """按 map 从 merged.wav 精确切出各截拼起来：和 final_avatar.mp4 的声音一模一样，但不经过 AAC 压缩"""
    import wave
    import numpy as np
    w = wave.open(merged)
    sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
    x = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    w.close()
    parts = [x[int(round(m["src_start"] * sr)) * ch:int(round(m["src_end"] * sr)) * ch] for m in mapping]
    o = wave.open(dst, "wb")
    o.setnchannels(ch)
    o.setsampwidth(sw)
    o.setframerate(sr)
    o.writeframes(np.concatenate(parts).tobytes())
    o.close()


def remap_words(words_json, mapping):
    """words.json（配音时间轴）→ 成片时间轴。落在裁掉的开头静音里的停顿收短到保留的部分。"""
    def conv(t, side):
        for m in mapping:
            if m["src_start"] - 1e-6 <= t <= m["src_end"] + 1e-6:
                return round(m["out_start"] + t - m["src_start"], 3)
            if t < m["src_start"]:
                return round(m["out_start"], 3)
        last = mapping[-1]
        return round(last["out_start"] + last["src_end"] - last["src_start"], 3)
    d = json.load(open(words_json, encoding="utf-8"))
    for w in d.get("words", []):
        w["start"], w["end"] = conv(w["start"], "start"), conv(w["end"], "end")
    d["pauses"] = [dict(p, start=conv(p["start"], "start"), end=conv(p["end"], "end")) for p in d.get("pauses", [])]
    d["pauses"] = [p for p in d["pauses"] if p["end"] - p["start"] > 0.01]
    d["timeline"] = "final_avatar.mp4（拼接时裁掉了每段开头几帧，见 finalize.json）"
    last = mapping[-1]
    d["total_seconds"] = round(last["out_start"] + last["src_end"] - last["src_start"], 3)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--legacy", action="store_true", help="旧项目：plan.json 里没有音频指纹也继续")
    ap.add_argument("--head-frames", type=int, default=HEAD_FRAMES, help="从第 2 段起每段开头裁掉几帧（默认 %d，0 不裁）" % HEAD_FRAMES)
    a = ap.parse_args()
    od = os.path.join(a.workdir, "avatar")
    plan = json.load(open(os.path.join(od, "plan.json"), encoding="utf-8"))
    info = json.load(open(os.path.join(od, "image_info.json"), encoding="utf-8"))
    res = json.load(open(os.path.join(od, "result.json"), encoding="utf-8"))["results"]
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import common as C
    C.verify_plan_audio(a.workdir, plan, a.legacy)         # 成片用的 merged.wav 必须和分段时是同一份
    segs = plan["segments"]
    missing = [s["i"] for s in segs if str(s["i"]) not in res or not os.path.isfile(res[str(s["i"])]["file"])]
    if missing:
        sys.exit("这些段还没有视频：%s。先用 run_avatar.py 跑完。" % missing)
    audio = os.path.join(a.workdir, "out", "merged.wav")
    cuts = head_cuts(segs, a.fps, a.head_frames)
    short = []                                             # 片段比它的音频短：trim 只能裁不能补，后面所有段的口型都会提前
    for s, n in zip(segs, cuts):
        cap = cv2.VideoCapture(res[str(s["i"])]["file"])
        nf, cfps = cap.get(cv2.CAP_PROP_FRAME_COUNT), cap.get(cv2.CAP_PROP_FPS) or a.fps
        cap.release()
        have, need = nf / cfps, s["duration"]
        if have < need - 1.0 / a.fps:
            short.append((s["i"], have, need))
    if short:
        _cli.report(short_clips=[{"i": i, "clip_seconds": round(h, 3), "audio_seconds": round(n_, 3)} for i, h, n_ in short],
                    next="python scripts/avatar/run_avatar.py --workdir %s --only %s --seed random --yes（先不加 --yes 预览、报费用）"
                    % (a.workdir, " ".join(str(i) for i, _, _ in short)))
        _cli.fail(_cli.ERROR, "这些段的视频比它的音频短，拼进去后面的口型会全部提前：%s。这几段要重新生成（换种子），或者确认分段计划和片段是同一批"
                  % "；".join("段%d 视频 %.2f 秒 / 音频 %.2f 秒" % x for x in short))
    rows, mapping, total = final_timeline(segs, cuts, a.fps)
    w, h = info["width"], info["height"]
    ff = ffmpeg()
    cmd = [ff, "-y", "-v", "error"]
    for s in segs:
        cmd += ["-i", res[str(s["i"])]["file"]]
    cmd += ["-i", audio]
    parts, labels = [], []
    A = len(segs)
    for k, (s, n) in enumerate(zip(segs, cuts)):
        hd = n / a.fps
        parts.append("[%d:v]fps=%d,trim=start=%.4f:duration=%.4f,setpts=PTS-STARTPTS,scale=%d:%d[v%d]" % (k, a.fps, hd, s["duration"] - hd, w, h, k))
        parts.append("[%d:a]atrim=start=%.4f:end=%.4f,asetpts=PTS-STARTPTS[a%d]" % (A, s["start"] + hd, s["end"], k))
        labels.append("[v%d][a%d]" % (k, k))
    fc = ";".join(parts) + ";" + "".join(labels) + "concat=n=%d:v=1:a=1[v][a]" % len(segs)
    final = os.path.join(od, "final_avatar.mp4")
    script = os.path.join(od, "_finalize_filter.txt")
    open(script, "w", encoding="utf-8").write(fc)
    cmd += ["-filter_complex_script", script, "-map", "[v]", "-map", "[a]", "-t", "%.4f" % total,
            "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", final]
    try:
        subprocess.run(cmd, check=True)
    finally:
        os.remove(script)
    json.dump({"head_frames": a.head_frames, "fps": a.fps, "source_seconds": plan["total_seconds"], "final_seconds": round(total, 3),
               "removed_seconds": round(plan["total_seconds"] - total, 3), "segments": rows, "map": mapping},
              open(os.path.join(od, "finalize.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    write_voice_final(audio, mapping, os.path.join(od, "voice_final.wav"))      # 和成片完全对齐的配音（后期包装用）
    wj = os.path.join(a.workdir, "out", "words.json")
    if os.path.isfile(wj):
        json.dump(remap_words(wj, mapping), open(os.path.join(od, "words_final.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

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
    cap.release()
    print("成片：", final)
    if sum(cuts):
        print("每段开头裁掉的帧：%s（合计 %d 帧，成片比配音短 %.2f 秒；时间对应见 finalize.json）" % (cuts, sum(cuts), sum(cuts) / a.fps))
    for name, val, ok in checks:
        print("%s %s：%s" % ("✓" if ok else "✗", name, val))
    print("接缝跳变、接缝图（seams.png）、镜头推拉和全片总览图由 qa_video.py 生成（逐帧顺序解码，比跳着取帧准）。")
    print("未做：口型同步、手势细节、人物一致性——需要人看和听成片确认。")
    mp = os.path.join(a.workdir, "meta.json")        # 项目目录里写回进度，后续环节可读
    if os.path.isfile(mp):
        m = json.load(open(mp, encoding="utf-8"))
        m["avatar_video"] = final
        json.dump(m, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    _cli.report(final=final, head_frames=cuts, final_seconds=round(total, 3), finalize_json=os.path.join(od, "finalize.json"),
                checks=[{"name": n_, "value": v_, "ok": ok_} for n_, v_, ok_ in checks],
                next="qa_video.py（嘴、手、色彩、镜头、接缝、总览图）")
    if not all(ok for _, _, ok in checks):
        sys.exit(_cli.ERROR)


if __name__ == "__main__":
    _cli.run(main)
