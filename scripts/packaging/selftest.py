"""脚本自检：用 ffmpeg 现场生成一段带声音的测试视频，把探测、合成（小窗、叠加层、字幕）、静帧、
声画检查和技术巡检各跑一遍，核对关键结果。不联网，不需要任何素材。改过脚本后运行：

  python selftest.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

import _common
from _common import ffmpeg_exe

HERE = Path(__file__).resolve().parent
results = []


def run(*args, expect=0):
    r = subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != expect:
        print(r.stdout[-800:], r.stderr[-800:])
    return r


def check(name, ok, detail=""):
    results.append(ok)
    print("✓" if ok else "✗", name, detail)


def parse(r):
    try:
        return json.loads(r.stdout[r.stdout.index("{"):])
    except Exception:
        return {}


def main():
    d = Path(tempfile.mkdtemp(prefix="broll_selftest_"))
    talk = d / "talk.mp4"
    subprocess.run([ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=12",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=12", "-af", "tremolo=f=3:d=0.9",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(talk)], check=True)
    card = np.full((720, 1280, 3), (60, 40, 30), np.uint8)
    cv2.rectangle(card, (100, 100), (900, 500), (200, 200, 200), 4)
    _common.imwrite(d / "card.png", card)
    words = [{"text": "大家好，", "start": 0.2, "end": 1.0}, {"text": "今天", "start": 1.0, "end": 1.4},
             {"text": "聊聊", "start": 1.4, "end": 1.9}, {"text": "配图。", "start": 1.9, "end": 2.6},
             {"text": "先看", "start": 3.2, "end": 3.7}, {"text": "流成。", "start": 3.7, "end": 4.4}]
    (d / "words.json").write_text(json.dumps({"words": words}, ensure_ascii=False), encoding="utf-8")
    (d / "copy.txt").write_text("大家好，今天聊聊配图。先看流程。", encoding="utf-8")

    r = run(HERE / "subs_from_words.py", d / "words.json", "--copy", d / "copy.txt", "--out", d / "subs.json", "--pip-safe")
    subs = json.loads((d / "subs.json").read_text(encoding="utf-8")) if r.returncode == 0 else []
    check("字幕生成，并按原文案纠正错字（流成→流程）", bool(subs) and any("流程" in s["text"] for s in subs),
          str([s["text"] for s in subs]))

    (d / "copy2.txt").write_text("大家好，今天聊聊配图。先看流程，模型选 Opus 5.5！", encoding="utf-8")
    w2 = words + [{"text": "模型", "start": 4.6, "end": 5.0}, {"text": "选", "start": 5.0, "end": 5.2}, {"text": "Opus", "start": 5.2, "end": 5.6}, {"text": "55", "start": 5.6, "end": 6.0}]
    (d / "words2.json").write_text(json.dumps({"words": w2}, ensure_ascii=False), encoding="utf-8")
    r = run(HERE / "captions_from_copy.py", d / "words2.json", "--copy", d / "copy2.txt", "--out", d / "caps.json")
    caps = json.loads((d / "caps.json").read_text(encoding="utf-8")) if r.returncode == 0 else []
    texts = [c["text"] for c in caps]
    check("按原文做字幕：一句一条、保留逗号和小数点、去掉句末句号", texts == ["大家好，今天聊聊配图", "先看流程，模型选 Opus 5.5！"], str(texts))
    check("按原文做字幕：时间对得上、每条至少停留 0.6 秒", bool(caps) and abs(caps[0]["start"] - 0.14) < 0.1 and abs(caps[1]["start"] - 3.14) < 0.1 and all(c["end"] - c["start"] >= 0.6 for c in caps), str(caps))

    spec = {"presenter": "talk.mp4", "size": [1280, 720], "fps": "30",
            "pip": {"margin_y": 0.12},
            "segments": [{"start": 3.0, "end": 7.0, "mode": "content_pip", "content": "card.png"},
                         {"start": 7.0, "end": 9.0, "mode": "content_full", "content": None}],
            "overlays": [{"type": "text", "text": "关键词", "start": 0.5, "end": 2.5, "pos": [0.25, 0.3], "anim": "pop"},
                         {"type": "box", "rect": [0.1, 0.15, 0.3, 0.3], "start": 4.0, "end": 6.5, "layer": "content"}] + subs}
    (d / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")

    r = run(HERE / "pip_render.py", d / "spec.json", "--out", d / "out" / "final.mp4")
    j = parse(r)
    check("合成成功，输出目录自动创建，带原声", r.returncode == 0 and j.get("audio") is True and (d / "out" / "final.mp4").is_file())
    check("帧数与时长一致（12 秒 × 30 帧）", j.get("frames") == 360, str(j.get("frames")))

    r = run(HERE / "pip_render.py", d / "spec.json", "--stills", "1.5,5.0,8.0", "--stills-dir", d / "st")
    stills = parse(r).get("stills", [])
    check("只出静帧：3 张", len(stills) == 3)
    if len(stills) == 3:
        a, b = _common.imread(stills[0]), _common.imread(stills[1])
        src = cv2.VideoCapture(str(talk))
        src.set(cv2.CAP_PROP_POS_MSEC, 1500)
        ok, raw = src.read()
        check("人物全屏时叠加文字画在了画面上", ok and float(np.abs(a.astype(np.float32) - raw).mean()) > 0.5)
        # 5.0 秒：内容主画面 + 右下圆窗（上移 margin_y）；框选在内容层
        D = int(0.2 * 720)
        cy = 720 - int(0.12 * 720) - D // 2
        cx = 1280 - int(0.03 * 720) - D // 2
        inside, corner = b[cy, cx], b[cy - D // 2 + 3, cx - D // 2 + 3]
        check("圆形小窗在右下并按 margin_y 上移，四角露出内容层", not np.allclose(inside, (60, 40, 30), atol=12)
              and np.allclose(corner, (60, 40, 30), atol=12), f"中心 {inside.tolist()} 角 {corner.tolist()}")
        check("内容层上的框选可见", abs(int(b[int(0.15 * 720) + 2, int(0.25 * 1280)][1]) - 40) > 60)

    r = run(HERE / "av_check.py", d / "spec.json", d / "out" / "final.mp4", "--out-dir", d / "qc")
    j = parse(r)
    check("原声比对通过", j.get("audio", {}).get("status") == "passed", str(j.get("audio")))
    check("小窗运动检查通过", [x["status"] for x in j.get("pip_motion", [])] == ["passed"], str(j.get("pip_motion")))
    check("导出了进场过渡图", len(j.get("transitions", [])) >= 1 and all(Path(p).is_file() for p in j["transitions"]))

    r = run(HERE / "qc_scan.py", d / "out" / "final.mp4", "--expect-duration", "12")
    check("技术巡检：时长一致", parse(r).get("duration_ok") is True)
    r = run(HERE / "qc_scan.py", d / "nope.mp4", expect=1)
    check("技术巡检：文件不存在时报错而不是输出“无问题”", r.returncode != 0)
    r = run(HERE / "contact_sheet.py", d / "out" / "final.mp4", "--every", "3", "--out", d / "sheet.jpg")
    check("联系表", r.returncode == 0 and (d / "sheet.jpg").is_file())

    # 异常遮盖：5–7 秒有异常 → 区间对齐到句子开头；片头的异常不遮盖
    w2 = [{"text": "开场白。", "start": 0.2, "end": 1.6}, {"text": "先说", "start": 3.2, "end": 3.8},
          {"text": "第一点。", "start": 3.8, "end": 6.9}, {"text": "再说", "start": 7.9, "end": 8.4},
          {"text": "第二点。", "start": 8.4, "end": 11.5}]
    (d / "w2.json").write_text(json.dumps({"words": w2}, ensure_ascii=False), encoding="utf-8")
    (d / "an.json").write_text(json.dumps({"duration": 12, "anomalies": [
        {"start": 0.5, "end": 1.5, "type": "color", "cover": "content_pip"},
        {"start": 5.0, "end": 7.0, "type": "color", "cover": "content_pip"}]}), encoding="utf-8")
    r = run(HERE / "cover_plan.py", "plan", "--anomalies", d / "an.json", "--words", d / "w2.json", "--out", d / "covers.json")
    cv = json.loads((d / "covers.json").read_text(encoding="utf-8")) if r.returncode == 0 else {}
    c = (cv.get("covers") or [{}])[0]
    check("遮盖规划：起点对齐到句子开头 3.2 秒，终点对齐到下一句 7.9 秒；片头异常列为不可遮盖",
          c.get("start") == 3.2 and c.get("end") == 7.9 and len(cv.get("not_coverable", [])) == 1, str([c.get("start"), c.get("end")]))
    (d / "cards.json").write_text(json.dumps({"C1": {"title": "第一点", "points": [{"text": "要点", "after": "第一点"}]}},
                                             ensure_ascii=False), encoding="utf-8")
    r = run(HERE / "cover_plan.py", "spec", "--covers", d / "covers.json", "--cards", d / "cards.json", "--words", d / "w2.json",
            "--presenter", talk, "--out", d / "cover_spec.json")
    r2 = run(HERE / "pip_render.py", d / "cover_spec.json", "--stills", "5.5", "--stills-dir", d / "st2")
    st = parse(r2).get("stills", [])
    check("遮盖 spec 可以直接合成", r.returncode == 0 and len(st) == 1)

    print("\n通过 %d/%d（临时目录 %s）" % (sum(results), len(results), d))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
