"""给一个口播项目起包装工程：复制模板、接上共用的依赖、放进素材。一条命令做完。

  python scripts/packaging/remotion_new.py --workdir <项目目录>             # 用项目里的人物视频、配音、逐词时间
  python scripts/packaging/remotion_new.py --workdir <项目目录> --check     # 只看依赖装没装，不动任何东西
  python scripts/packaging/remotion_new.py --project <任意目录> --presenter 人物.mp4 --voice 原声.wav --words words.json

做三件事：
1. 把 assets/remotion-template 复制到 <项目目录>/package（已存在就不覆盖，只补缺的固定文件）。
2. 依赖只装一份，放在 ~/.digital-human/remotion-runtime，每个工程用目录联接指过去——不然每条片子都要再占约 700 MB。
   还没装就调 scripts/setup_env.py 自动装上（不用问用户），输出里的 installed 会列出这次新装了什么。
3. 调 remotion_prepare.py 放素材（人物视频、原声、逐词时间、字幕、音效、字幕字体）。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import _common  # noqa: F401  统一 stdout 编码

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent.parent / "assets" / "remotion-template"
ROOT = Path(os.environ.get("DH_WORKSPACE", "").strip() or Path.home() / ".digital-human")
RUNTIME = ROOT / "remotion-runtime"


def runtime_ready():
    want = json.loads((TEMPLATE / "package.json").read_text(encoding="utf-8"))
    have = RUNTIME / "package.json"
    if not (RUNTIME / "node_modules" / "remotion" / "package.json").is_file() or not have.is_file():
        return False
    return json.loads(have.read_text(encoding="utf-8")).get("dependencies") == want.get("dependencies")   # 模板升级了版本就重装


def install_runtime():
    """交给 setup_env.py：它会把 Node、渲染依赖、无头浏览器一起检查并装上"""
    r = subprocess.run([sys.executable, str(HERE.parent / "setup_env.py"), "--stage", "packaging"], capture_output=True, text=True, encoding="utf-8")
    info = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {"failed": [{"what": "环境检查", "detail": (r.stderr or r.stdout)[-300:]}]}
    if r.returncode or info.get("failed"):
        print(json.dumps({"failed": info.get("failed")}, ensure_ascii=False))
        sys.exit(7)
    return info.get("installed", [])


def link_modules(proj):
    nm = proj / "node_modules"
    if nm.exists():
        return
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(nm), str(RUNTIME / "node_modules")], check=True, capture_output=True)
    else:
        nm.symlink_to(RUNTIME / "node_modules", target_is_directory=True)


def copy_template(proj):
    made = []
    for src in TEMPLATE.rglob("*"):
        if src.is_dir():
            continue
        dst = proj / src.relative_to(TEMPLATE)
        if dst.exists():
            continue                                        # 不覆盖已经写过的编排和风格
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        made.append(str(src.relative_to(TEMPLATE)))
    return made


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", help="口播项目目录；工程建在它下面的 package/")
    ap.add_argument("--project", help="不用项目目录时，直接指定工程目录")
    ap.add_argument("--presenter")
    ap.add_argument("--voice")
    ap.add_argument("--words")
    ap.add_argument("--copy")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    ready = runtime_ready()
    if a.check:
        print(json.dumps({"runtime": str(RUNTIME), "ready": ready, "node": shutil.which("node"), "npm": shutil.which("npm")}, ensure_ascii=False))
        return
    installed = [] if ready else install_runtime()

    if a.workdir:
        wd = Path(a.workdir)
        proj = wd / "package"
        presenter, voice, words = wd / "avatar" / "final_avatar.mp4", wd / "out" / "merged.wav", wd / "out" / "words.json"
        copy = wd / "copy.txt"
    elif a.project:
        proj, copy = Path(a.project), Path(a.copy) if a.copy else None
        if not (a.presenter and a.voice and a.words):
            sys.exit("--project 需要同时给 --presenter、--voice、--words")
        presenter, voice, words = Path(a.presenter), Path(a.voice), Path(a.words)
    else:
        sys.exit("需要 --workdir 或 --project")
    missing = [str(p) for p in (presenter, voice, words) if not p.is_file()]
    if missing:
        sys.exit("缺少前面阶段的产物，先补上：" + "、".join(missing))
    if len(str(proj.resolve())) > 150:
        print("提醒：工程路径较长，Windows 上路径超过 260 个字符时渲染用的浏览器会启动失败", file=sys.stderr)

    proj.mkdir(parents=True, exist_ok=True)
    made = copy_template(proj)
    link_modules(proj)
    cmd = [sys.executable, str(HERE / "remotion_prepare.py"), str(proj), "--presenter", str(presenter), "--voice", str(voice), "--words", str(words)]
    if copy and copy.is_file():
        cmd += ["--copy", str(copy)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        sys.exit("放素材失败：" + (r.stderr or r.stdout)[-600:])
    print(json.dumps({"project": str(proj), "template_files_added": len(made), "runtime": str(RUNTIME), "installed": installed,
                      "prepare": json.loads(r.stdout.strip().splitlines()[-1])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
