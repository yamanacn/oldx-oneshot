"""种子策略（run_avatar.py 预览，不联网）：第一次用 42；重跑时提示词、工作流、种子都没变就自动换随机种子；改了提示词就沿用种子。
  python tests/avatar/test_seed.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "..", "scripts")
RA = os.path.join(SCRIPTS, "avatar", "run_avatar.py")
sys.path.insert(0, os.path.join(SCRIPTS, "avatar"))
import takes as TK  # noqa: E402

TMP = tempfile.mkdtemp(prefix="seed_test_")
cases = []


def check(name, ok, extra=""):
    print("✓" if ok else "✗", name, "" if ok else extra)
    cases.append(bool(ok))


wd = os.path.join(TMP, "proj")
od = os.path.join(wd, "avatar")
os.makedirs(os.path.join(od, "prompts"))
os.makedirs(os.path.join(od, "clips"))
json.dump({"total_seconds": 9.0, "segments": [{"i": 1, "start": 0, "end": 9.0, "duration": 9.0, "audio": "seg_01.wav"}]},
          open(os.path.join(od, "plan.json"), "w", encoding="utf-8"))
json.dump({"width": 768, "height": 1376}, open(os.path.join(od, "image_info.json"), "w"))
PROMPT = "<固定镜头，无运镜>\n\n双手的原姿态：双手抬在胸前。\n\n动作：从<Picture 1>的姿态开始……回到<Picture 1>的姿态。"
open(os.path.join(od, "prompts", "segment_01.txt"), "w", encoding="utf-8").write(PROMPT)
env = dict(os.environ, USERPROFILE=TMP, HOME=TMP, DH_WORKSPACE=TMP)


def preview(*extra):
    r = subprocess.run([sys.executable, RA, "--workdir", wd, "--legacy", *extra, "--json"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    try:
        return r, json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return r, {}


r, j = preview()
check("第一次生成：用默认种子 42，不自动换", r.returncode == 0 and j.get("todo", [{}])[0].get("seed") == 42 and j.get("auto_seed") == [], r.stderr[-300:])
clip = os.path.join(od, "clips", "seg_01.mp4")
open(clip, "wb").write(b"x")
json.dump({"results": {"1": {"i": 1, "file": clip, "seed": 42, "prompt_sha": TK.prompt_sha(PROMPT), "workflow": None}},
           "seeds": {"1": 42}, "pending": {}, "errors": {}}, open(os.path.join(od, "result.json"), "w", encoding="utf-8"))
r, j = preview("--only", "1")
seed = j.get("todo", [{}])[0].get("seed")
check("重跑、提示词和工作流都没改：自动换成随机种子，并说明原因", j.get("auto_seed") == [1] and seed not in (None, 42) and "一模一样" in r.stdout, (j.get("auto_seed"), seed))
r, j = preview("--only", "1", "--seed", "42")
check("用户指定了 --seed 42：照用，不自动换", j.get("auto_seed") == [] and j.get("todo", [{}])[0].get("seed") == 42)
open(os.path.join(od, "prompts", "segment_01.txt"), "w", encoding="utf-8").write(PROMPT.replace("双手抬在胸前", "双手放在桌面"))
r, j = preview("--only", "1")
check("改了提示词：沿用上次的种子（看得清是提示词起的作用）", j.get("auto_seed") == [] and j.get("todo", [{}])[0].get("seed") == 42)
check("预览里有预计费用（RH 币）", (j.get("estimate_coins") or {}).get("mid", 0) > 0 and "预计费用" in r.stdout, j.get("estimate_coins"))

print("\n通过 %d/%d" % (sum(cases), len(cases)))
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if all(cases) else 1)
