"""检查这个技能要用到的东西，缺什么就自动下载安装什么，不用问用户。

  python scripts/setup_env.py                    # 三个阶段要用的全部
  python scripts/setup_env.py --stage voice      # 只管某一阶段：voice / avatar / packaging
  python scripts/setup_env.py --check            # 只看缺什么，不安装

会自动装的：
- Python 包（numpy、opencv-python、Pillow、imageio-ffmpeg、av、librosa）——用当前这个 Python 的 pip；
- ffmpeg——不单独装，用 imageio-ffmpeg 自带的那份；
- Node.js（只有包装阶段要）——Windows 上用 winget 装长期支持版；
- 包装用的渲染依赖——装在 ~/.digital-human/remotion-runtime，所有片子共用一份（约 700 MB）；
- 渲染用的无头浏览器——由渲染工具自己下载。

不会自动处理的：两个密钥（必须由用户提供）。

输出 JSON：ok 是不是都齐了；installed 这次新装了什么（要告诉用户）；failed 没装上的和原因（要告诉用户怎么办）。
退出码：0 都齐了；7 有东西没装上。
"""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "assets" / "remotion-template"
ROOT = Path(os.environ.get("DH_WORKSPACE", "").strip() or Path.home() / ".digital-human")
RUNTIME = ROOT / "remotion-runtime"

# 模块名 -> pip 包名，按阶段
PY = {
    "voice": {"numpy": "numpy", "imageio_ffmpeg": "imageio-ffmpeg", "librosa": "librosa"},
    "avatar": {"numpy": "numpy", "cv2": "opencv-python", "PIL": "Pillow", "imageio_ffmpeg": "imageio-ffmpeg"},
    "packaging": {"numpy": "numpy", "cv2": "opencv-python", "PIL": "Pillow", "imageio_ffmpeg": "imageio-ffmpeg", "av": "av"},
}


def run(cmd, cwd=None, timeout=1800):
    r = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)[-500:]


def find_node():
    """PATH 里找不到时，再看一眼默认安装位置（刚装完的 Node 还没进当前进程的 PATH）"""
    if shutil.which("npm"):
        return True
    d = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "nodejs"
    if (d / "npm.cmd").is_file():
        os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
        return True
    return False


def runtime_ready():
    have = RUNTIME / "package.json"
    if not have.is_file() or not (RUNTIME / "node_modules" / "remotion" / "package.json").is_file():
        return False
    want = json.loads((TEMPLATE / "package.json").read_text(encoding="utf-8")).get("dependencies")
    return json.loads(have.read_text(encoding="utf-8")).get("dependencies") == want       # 模板升级了版本就重装


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["voice", "avatar", "packaging", "all"], default="all")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    stages = ["voice", "avatar", "packaging"] if a.stage == "all" else [a.stage]
    present, installed, failed, missing = [], [], [], []

    def need(name, ok, install, how_if_failed):
        """ok 为真就算有；否则（不是 --check 时）调 install() 去装，装完再判断一次"""
        if ok():
            present.append(name)
            return True
        if a.check:
            missing.append(name)
            return False
        code, tail = install()
        if code == 0 and ok():
            installed.append(name)
            return True
        failed.append({"what": name, "tell_user": how_if_failed, "detail": tail.strip()[-300:]})
        return False

    # ---- Python 包
    pkgs = {}
    for s in stages:
        pkgs.update(PY[s])
    for mod, pip_name in pkgs.items():
        need(f"Python 包 {pip_name}", lambda m=mod: importlib.util.find_spec(m) is not None,
             lambda p=pip_name: (importlib.invalidate_caches(), run([sys.executable, "-m", "pip", "install", "--quiet", p]))[1],
             f"请在命令行运行：{Path(sys.executable).name} -m pip install {pip_name}")

    # ---- ffmpeg：PATH 里没有就用 imageio-ffmpeg 自带的，脚本本来就会这样找
    def ffmpeg_ok():
        if shutil.which("ffmpeg"):
            return True
        try:
            import imageio_ffmpeg
            return bool(imageio_ffmpeg.get_ffmpeg_exe())
        except Exception:
            return False
    need("ffmpeg", ffmpeg_ok, lambda: run([sys.executable, "-m", "pip", "install", "--quiet", "--force-reinstall", "imageio-ffmpeg"]),
         "请安装 ffmpeg 并确保命令行里能运行 ffmpeg")

    # ---- 包装阶段：Node、渲染依赖、无头浏览器
    if "packaging" in stages:
        def install_node():
            if os.name == "nt" and shutil.which("winget"):
                return run("winget install --id OpenJS.NodeJS.LTS -e --silent --accept-source-agreements --accept-package-agreements")
            return 1, "这台机器上没有可用的自动安装方式"
        node = need("Node.js", find_node, install_node, "请到 https://nodejs.org 下载安装长期支持版（LTS），装完告诉我")

        def install_runtime():
            RUNTIME.mkdir(parents=True, exist_ok=True)
            shutil.copy2(TEMPLATE / "package.json", RUNTIME / "package.json")
            return run("npm i --no-audit --no-fund", cwd=RUNTIME)
        if node:
            rt = need("渲染依赖（约 700 MB，所有片子共用）", runtime_ready, install_runtime, "网络问题导致下载失败的话，稍后让我重试一次")
            if rt:
                shell = RUNTIME / "node_modules" / ".remotion" / "chrome-headless-shell"
                need("渲染用的无头浏览器", lambda: any(shell.rglob("chrome-headless-shell*")) if shell.is_dir() else False,
                     lambda: run("npx remotion browser ensure", cwd=RUNTIME), "网络问题导致下载失败的话，稍后让我重试一次")
        elif not a.check:
            failed.append({"what": "渲染依赖", "tell_user": "要先有 Node.js", "detail": ""})

    out = {"ok": not failed and not missing, "stage": a.stage, "present": present, "installed": installed, "failed": failed}
    if a.check:
        out["missing"] = missing
    print(json.dumps(out, ensure_ascii=False, indent=1))
    sys.exit(7 if failed else 0)


if __name__ == "__main__":
    main()
