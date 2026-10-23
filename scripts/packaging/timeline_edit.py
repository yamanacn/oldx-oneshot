"""二次剪辑：在人物视频的时间轴上插"空档"，给满屏动画、外部演示片段、片尾留出时间。人声本身不剪、不变速、不改字。

  python timeline_edit.py --workdir <项目目录> --gap "after:下一个阶段:1.5" --gap "after:两段视频吧:20:hide" --tail 3
  python timeline_edit.py --workdir <项目目录> --gap "at:33.333:20:hide" --tail 3 --out <目录>

为什么需要它：跳剪去掉了说话之间的停顿，动画时间又跟着逐词时间走——人物被满屏动画盖住的那几秒，
下一句一开口动画就被切走（实测："下一个阶段"满屏色块没演完就换场）。插一段空档：
- 画面：切点前一帧定格（这段时间人物应当被满屏动画盖住或隐藏，所以看不见定格）；
- 声音：补静音；
- 逐词时间：切点之后的全部后移；字幕跟着走（:hide 的空档里不显示字幕，例如插外部演示片段时）。

--gap 的写法（可以给多个）：
  after:<口播里的几个字>[#第几次]:<秒>[:hide]   在这几个字说完、下一个字开口之前的停顿中间切开
  at:<秒>:<秒>[:hide]                         在人物视频（剪辑前）的这一秒切开
--tail <秒>   片尾在最后一帧定格多久（放片尾卡）

人物视频的来源：项目里有跳剪成片（avatar/final_jumpcut.mp4 + jumpcut.json）就用它，逐词时间按剪辑映射换算；
否则用 avatar/final_avatar.mp4 + avatar/words_final.json。也可以用 --presenter/--words 直接指定。
产出（默认 <项目目录>/package_src/）：presenter_edit.mp4、voice_edit.wav、words_edit.json（含 edit.gaps：每个空档在成片上的起止）。
之后：remotion_new.py --workdir <项目目录> 会自动用这三个文件（或 --project … --presenter … 手动指定）。
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2

from _common import ffmpeg_exe, media_info

STRIP = re.compile(r"[\s，。、！？!?；;：:“”\"'（）()\-—…,.·]")


def run(args):
    r = subprocess.run([ffmpeg_exe(), "-y", "-v", "error", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        sys.exit("ffmpeg 失败：" + r.stderr[-500:])


def load_source(a):
    wd = Path(a.workdir) if a.workdir else None
    if a.presenter:
        words = json.loads(Path(a.words).read_text(encoding="utf-8-sig"))
        return Path(a.presenter), words.get("words", words), None
    jc, jj = wd / "avatar" / "final_jumpcut.mp4", wd / "avatar" / "jumpcut.json"
    wf = wd / "avatar" / "words_final.json"
    if not wf.is_file():
        wf = wd / "out" / "words.json"
    words = json.loads(wf.read_text(encoding="utf-8"))["words"]
    if jc.is_file() and jj.is_file() and not a.no_jumpcut:
        jm = json.loads(jj.read_text(encoding="utf-8"))
        def mp(t):
            for s in jm["map"]:
                if s["src_start"] - 1e-3 <= t <= s["src_end"] + 1e-3:
                    return s["out_start"] + t - s["src_start"]
            for s in jm["map"]:
                if t < s["src_start"]:
                    return s["out_start"]
            return jm["out_seconds"]
        words = [dict(w, start=round(mp(w["start"]), 3), end=round(mp(w["end"]), 3)) for w in words]
        return jc, words, "final_jumpcut.mp4"
    return wd / "avatar" / "final_avatar.mp4", words, "final_avatar.mp4"


def locate(words, key, n):
    """key 第 n 次出现的最后一个字说完的时刻、下一个字开口的时刻"""
    chars, ends, starts = [], [], []
    for i, w in enumerate(words):
        txt = STRIP.sub("", w.get("text") or w.get("word") or "").lower()
        for k, ch in enumerate(txt):
            chars.append(ch)
            ends.append((i, w["start"] + (w["end"] - w["start"]) * (k + 1) / len(txt)))
    joined, k = "".join(chars), STRIP.sub("", key).lower()
    pos = -1
    for _ in range(n):
        pos = joined.find(k, pos + 1)
        if pos < 0:
            total = joined.count(k)
            sys.exit(f"逐词时间里找不到「{key}」（第 {n} 次）：这几个字一共出现 {total} 次" + ("，去掉 #序号或改小" if total else "，识别文字可能有同音字，换几个字试试"))
    last = pos + len(k) - 1
    wi, t_end = ends[last]
    nxt = words[wi + 1]["start"] if wi + 1 < len(words) else t_end
    return t_end, max(t_end, nxt)


def main():
    ap = argparse.ArgumentParser(description="在人物视频时间轴上插空档（满屏动画、外部片段、片尾）")
    ap.add_argument("--workdir")
    ap.add_argument("--presenter")
    ap.add_argument("--words")
    ap.add_argument("--gap", action="append", default=[])
    ap.add_argument("--tail", type=float, default=0.0)
    ap.add_argument("--out")
    ap.add_argument("--no-jumpcut", action="store_true", help="不用跳剪成片，用没剪停顿的 final_avatar.mp4")
    a = ap.parse_args()
    if not a.workdir and not (a.presenter and a.words):
        sys.exit("需要 --workdir，或者同时给 --presenter 和 --words")
    src, words, src_name = load_source(a)
    info = media_info(src)
    cap = cv2.VideoCapture(str(src))
    fps = round(cap.get(cv2.CAP_PROP_FPS) or 24)
    cap.release()
    if not info.get("duration"):
        sys.exit(f"读不到人物视频：{src}")
    dur_src = info["duration"]

    gaps = []
    for g in a.gap:
        parts = g.split(":")
        hide = parts[-1] == "hide"
        if hide:
            parts = parts[:-1]
        if parts[0] == "after":
            key, n = parts[1], 1
            if "#" in key:
                key, n = key.split("#")[0], int(key.split("#")[1])
            t_end, t_next = locate(words, key, n)
            at = (t_end + t_next) / 2
            note = f"after {key}"
        elif parts[0] == "at":
            at, note = float(parts[1]), "at"
        else:
            sys.exit(f"看不懂的 --gap：{g}")
        frame = int(round(at * fps))
        gaps.append({"frame": frame, "dur": float(parts[-1]), "hide_captions": hide, "note": note})
    gaps.sort(key=lambda x: x["frame"])

    def shift(t):
        return t + sum(g["dur"] for g in gaps if t >= g["frame"] / fps - 1e-6)
    new_words = [dict(w, start=round(shift(w["start"]), 3), end=round(shift(w["end"]), 3)) for w in words]
    edit_gaps, acc = [], 0.0
    for g in gaps:
        start = g["frame"] / fps + acc
        edit_gaps.append({"start": round(start, 3), "end": round(start + g["dur"], 3), "dur": g["dur"], "src_at": round(g["frame"] / fps, 3),
                          "hide_captions": g["hide_captions"], "note": g["note"]})
        acc += g["dur"]
    total = dur_src + acc + a.tail

    out = Path(a.out) if a.out else Path(a.workdir) / "package_src"
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="tl_"))
    E = ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-r", str(fps), "-video_track_timescale", str(fps * 512)]
    parts, prev = [], 0
    cuts = [g["frame"] for g in gaps] + [None]
    holds = [g["dur"] for g in gaps] + [a.tail]
    for i, f in enumerate(cuts):
        if f is None or f > prev:   # 空档在第 0 帧（片头）时，它前面没有画面，不出这一段
            sel = f"gte(n\\,{prev})" + (f"*lt(n\\,{f})" if f is not None else "")
            seg = tmp / f"p{i}.mp4"
            run(["-i", str(src), "-an", "-vf", f"select='{sel}',setpts=N/{fps}/TB", *E, str(seg)])
            parts.append(seg)
        if holds[i] > 0:
            png = tmp / f"f{i}.png"
            if f is not None:
                run(["-i", str(src), "-vf", f"select='eq(n\\,{max(0, f - 1)})'", "-frames:v", "1", str(png)])
            else:
                run(["-sseof", "-0.1", "-i", str(src), "-frames:v", "1", "-update", "1", str(png)])
            hold = tmp / f"h{i}.mp4"
            run(["-loop", "1", "-i", str(png), "-t", f"{holds[i]:.3f}", *E, str(hold)])
            parts.append(hold)
        if f is not None:
            prev = f
    (tmp / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    run(["-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"), "-c", "copy", str(out / "presenter_edit.mp4")])
    # 声音：同样的切点插静音（这里不重编码人声，只拼接 PCM）
    fa, labels, prev_t = [], [], 0.0
    for i, f in enumerate(cuts):
        end = f"{f / fps:.6f}" if f is not None else None
        fmt = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        if f is not None and f / fps <= prev_t:   # 片头空档：前面没有声音，直接生成静音
            fa.append(f"aevalsrc=0:s=48000:d={holds[i]:.3f},{fmt}[a{i}]")
        else:
            trim = f"atrim={prev_t:.6f}:{end}" if end else f"atrim=start={prev_t:.6f}"
            fa.append(f"[0:a]{trim},asetpts=PTS-STARTPTS,{fmt},apad=pad_dur={holds[i]:.3f}[a{i}]")
        labels.append(f"[a{i}]")
        if f is not None:
            prev_t = f / fps
    fc = ";".join(fa) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=0:a=1,aresample=48000[a]"
    run(["-i", str(src), "-filter_complex", fc, "-map", "[a]", "-ac", "2", str(out / "voice_edit.wav")])
    edit = {"source": src_name or str(src), "fps": fps, "tail": a.tail, "gaps": edit_gaps, "total": round(total, 3)}
    (out / "words_edit.json").write_text(json.dumps({"words": new_words, "edit": edit}, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)
    got = media_info(out / "presenter_edit.mp4")["duration"]
    print(json.dumps({"out": str(out), "source": edit["source"], "gaps": edit_gaps, "tail": a.tail, "total": round(total, 3),
                      "presenter_seconds": got, "next": f"python scripts/packaging/remotion_new.py --workdir {a.workdir}" if a.workdir else None},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
