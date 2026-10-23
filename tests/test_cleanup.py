"""cleanup.py 的离线测试：在临时目录里造一个项目，看预览不删、--yes 只删第 1 类、参数才删第 2/3 类、链接只拆不进。

  python tests/test_cleanup.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "cleanup.py")
TMP = tempfile.mkdtemp(prefix="cleanup_test_")
ok_n, total = 0, 0


def check(name, cond, info=""):
    global ok_n, total
    total += 1
    ok_n += bool(cond)
    print(("✓ " if cond else "✗ ") + name + ("" if cond else "  →  %s" % str(info)[:400]))


def touch(path, n=1000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(b"x" * n)


def run(*args):
    env = dict(os.environ, DH_WORKSPACE=os.path.join(TMP, "ws"))
    r = subprocess.run([sys.executable, SCRIPT, *args, "--json"], capture_output=True, text=True, encoding="utf-8", env=env)
    last = r.stdout.strip().split("\n")[-1] if r.stdout.strip() else "{}"
    try:
        res = json.loads(last)
    except ValueError:
        res = {}
    return r, res


def link_dir(target, link):
    """建目录链接：Windows 先试目录联接（不需要管理员），别的系统用符号链接"""
    try:
        if os.name == "nt":
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "New-Item -ItemType Junction -Path '%s' -Target '%s' | Out-Null" % (link, target)], check=True, capture_output=True)
            if not os.path.lexists(link):
                return False
        else:
            os.symlink(target, link)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


wd = os.path.join(TMP, "ws", "projects", "20261015-test")
keep = ["copy.txt", "meta.json", "voice.json", "out/merged.wav", "out/words.json", "avatar/plan.json", "avatar/seg_01.wav",
        "avatar/prompts/segment_01.txt", "avatar/clips/seg_01.mp4", "avatar/result.json", "avatar/final_avatar.mp4",
        "avatar/final_jumpcut.mp4", "avatar/final_subtitled.mp4", "avatar/subtitles.srt", "avatar/qa.json", "assets/presenter.webp",
        "package/out/main.mp4", "package/src/Film.tsx", "final/成片.mp4"]
regen = ["avatar/sheets/overview_01.jpg", "avatar/seams.png", "avatar/mouth_strip.png", "avatar/zoom.jpg", "avatar/image_crop_check.jpg",
         "avatar/subtitles_check.jpg", "out_prev/20261015-1/merged.wav", "package/out/still_01.png"]
cache = ["cache/abc.wav"]
takes = ["avatar/takes/seg_01/1/clip.mp4"]
arch = ["avatar_旧版/final_avatar.mp4", "avatar/clips_v1/seg_01.mp4", "avatar/final_avatar_远景.mp4"]
for f in keep + regen + cache + takes + arch:
    touch(os.path.join(wd, f))
runtime = os.path.join(TMP, "runtime_node_modules")
touch(os.path.join(runtime, "remotion", "package.json"))
os.makedirs(os.path.join(wd, "package_v1"), exist_ok=True)
linked = link_dir(runtime, os.path.join(wd, "package_v1", "node_modules"))
touch(os.path.join(wd, "package_v1", "out", "main.mp4"))
exists = lambda f: os.path.exists(os.path.join(wd, f))

# 1. 预览不删
r, res = run("--workdir", wd)
check("预览：退出码 0，什么都不删", r.returncode == 0 and all(exists(f) for f in keep + regen + cache + takes + arch), r.stdout + r.stderr)
p = (res.get("projects") or [{}])[0]
check("预览：列出实验目录（avatar_旧版、clips_v1、final_avatar_远景.mp4、package_v1）",
      {x["path"] for x in p.get("archive_items", [])} >= {"avatar_旧版", "avatar/clips_v1", "avatar/final_avatar_远景.mp4", "package_v1"}, p)

# 2. --yes 只删第 1 类
r, res = run("--workdir", wd, "--yes")
check("--yes：第 1 类全删", r.returncode == 0 and not any(exists(f) for f in regen), [f for f in regen if exists(f)])
check("--yes：要保留的一个不少", all(exists(f) for f in keep), [f for f in keep if not exists(f)])
check("--yes：缓存、历史版本、实验目录不动", all(exists(f) for f in cache + takes + arch))

# 3. 第 2 类要参数
r, res = run("--workdir", wd, "--cache", "--takes", "--yes")
check("--cache --takes：删掉缓存和历史版本", not exists("cache") and not exists("avatar/takes"))
check("--cache --takes：成片和当前片段还在", all(exists(f) for f in keep))

# 4. 实验目录要点名，名字不对就拒绝
r, res = run("--workdir", wd, "--archive", "不存在的目录", "--yes")
check("--archive 不在列表里：拒绝（退出码 2），不删别的", r.returncode == 2 and all(exists(f) for f in arch), r.stdout + r.stderr)
r, res = run("--workdir", wd, "--archive", "avatar_旧版", "--archive", "avatar/final_avatar_远景.mp4", "--yes")
check("--archive：只删点名的两项", not exists("avatar_旧版") and not exists("avatar/final_avatar_远景.mp4") and exists("avatar/clips_v1/seg_01.mp4"))

# 5. 链接只拆不进
if linked:
    r, res = run("--workdir", wd, "--archive", "package_v1", "--yes")
    check("删带 node_modules 链接的目录：目录没了", r.returncode == 0 and not exists("package_v1"), r.stdout + r.stderr)
    check("删带 node_modules 链接的目录：共用依赖完好", os.path.isfile(os.path.join(runtime, "remotion", "package.json")))
else:
    print("（跳过链接测试：这台机器建不了目录链接）")

# 6. --all
r, res = run("--all")
check("--all 预览：能找到这个项目", r.returncode == 0 and any(x["project"].endswith("20261015-test") for x in res.get("projects", [])), r.stdout)
r, res = run("--all", "--takes")
check("--all 不接受第 2/3 类参数（退出码 2）", r.returncode == 2)

shutil.rmtree(TMP, ignore_errors=True)
print("\n通过 %d/%d" % (ok_n, total))
sys.exit(0 if ok_n == total else 1)
