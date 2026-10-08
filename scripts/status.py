"""看一个口播项目走到哪一步了：配音、数字人、包装各缺什么，下一步该做什么。不联网，不改任何文件。

  python scripts/status.py                 # 最近一个项目
  python scripts/status.py --dir <workdir>
  python scripts/status.py --list          # 列出所有项目

输出 JSON：stage 是下一步该做的阶段（voice / avatar / packaging / done），next 是一句话的下一步。
"""
import argparse
import json
import os
import sys

ROOT = os.environ.get("DH_WORKSPACE", "").strip() or os.path.join(os.path.expanduser("~"), ".digital-human")
PROJECTS = os.path.join(ROOT, "projects")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def has(d, *parts):
    return os.path.isfile(os.path.join(d, *parts))


def load(d, *parts):
    try:
        return json.load(open(os.path.join(d, *parts), encoding="utf-8"))
    except (OSError, ValueError):
        return None


def status(d):
    meta = load(d, "meta.json") or {}
    words = load(d, "out", "words.json") or {}
    anomalies = load(d, "avatar", "anomalies.json")
    pkg = os.path.join(d, "package")
    finals = sorted(f for f in os.listdir(os.path.join(d, "final")) if f.lower().endswith(".mp4")) if os.path.isdir(os.path.join(d, "final")) else []
    voice = {"copy": has(d, "copy.txt"), "voice": has(d, "voice.json"), "paragraphs": has(d, "paragraphs.json"),
             "audio": has(d, "out", "merged.wav"), "words": has(d, "out", "words.json")}
    avatar = {"image": has(d, "avatar", "image.png"), "plan": has(d, "avatar", "plan.json"), "prompts": has(d, "avatar", "prompt_src.json"),
              "video": has(d, "avatar", "final_avatar.mp4"), "qa": has(d, "avatar", "qa.json")}
    packaging = {"style": has(pkg, "STYLE.md"), "project": has(pkg, "package.json"), "prepared": has(pkg, "src", "data", "film.json"),
                 "render": has(pkg, "out", "main.mp4"), "final": bool(finals)}
    if not (voice["audio"] and voice["words"]):
        stage = "voice"
        nxt = next(n for k, n in (("copy", "收文案并建立项目"), ("voice", "复刻音色（或沿用默认音色）"), ("paragraphs", "通读文案并拆段"),
                                  ("audio", "合成配音"), ("words", "逐词对齐")) if not voice[k])
    elif not avatar["video"]:
        stage = "avatar"
        nxt = next(n for k, n in (("image", "向用户要口播图片并处理"), ("plan", "分段"), ("prompts", "写提示词"),
                                  ("video", "预览、确认费用后提交生成，再拼接")) if not avatar[k])
    elif not packaging["final"]:
        stage = "packaging"
        nxt = next(n for k, n in (("style", "细化风格、设计版式和画面内容"), ("project", "起包装工程（remotion_new.py）"),
                                  ("prepared", "放素材（remotion_new.py 会一并做）"), ("render", "写画面编排，先静帧和样段，再渲全片"),
                                  ("final", "质检通过后把成片放到 final/")) if not packaging[k])
        if avatar["video"] and not avatar["qa"]:
            nxt = "人物视频还没做内容质检（qa_video.py），先做；然后" + nxt
    else:
        stage, nxt = "done", "三个阶段都已完成；用户要改哪一段就从那一阶段重做"
    return {"project_dir": d, "chars": meta.get("chars"), "seconds": words.get("total_seconds"),
            "voice": voice, "avatar": {**avatar, "anomalies": len(anomalies.get("anomalies", anomalies) if isinstance(anomalies, dict) else anomalies or [])},
            "packaging": {**packaging, "final_files": finals}, "stage": stage, "next": nxt}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        rows = []
        for name in sorted(os.listdir(PROJECTS)) if os.path.isdir(PROJECTS) else []:
            d = os.path.join(PROJECTS, name)
            if has(d, "meta.json"):
                s = status(d)
                head = open(os.path.join(d, "copy.txt"), encoding="utf-8").read(24).replace("\n", " ") if has(d, "copy.txt") else ""
                rows.append({"id": name, "copy": head, "seconds": s["seconds"], "stage": s["stage"]})
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    d = a.dir
    if not d:
        latest = load(ROOT, "latest.json")
        if not latest:
            sys.exit("还没有任何项目：从收文案开始（scripts/voice/project.py init）")
        d = latest["dir"]
    print(json.dumps(status(d), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
