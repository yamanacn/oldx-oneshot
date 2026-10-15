"""一键流程（pipeline.py）、自动拆段（split_text.py / split_check.py）、机读结果里的 next（不联网、不需要密钥）。
  python tests/test_pipeline.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
TMP = tempfile.mkdtemp(prefix="pipeline_test_")
ENV = dict(os.environ, DH_WORKSPACE=TMP, DASHSCOPE_API_KEY="", USERPROFILE=TMP, HOME=TMP)
cases = []


def check(name, ok, extra=""):
    print("✓" if ok else "✗", name, "" if ok else extra)
    cases.append(bool(ok))


def run(script, *args):
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args, "--json"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    try:
        j = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
    except ValueError:
        j = {}
    return r, j


def project(name, copy):
    wd = os.path.join(TMP, name)
    os.makedirs(wd)
    open(os.path.join(wd, "copy.txt"), "w", encoding="utf-8").write(copy)
    json.dump({"voice_id": "voice-x"}, open(os.path.join(wd, "voice.json"), "w"))
    return wd


SENT = "这是一句完整的话，用来凑够字数，看看拆段会不会在句子中间断开。"          # 31 字
N = len(SENT)
SHORT = "\n\n".join([SENT] * 16)                                              # 496 字、16 个自然段
LONG = "\n\n".join(["".join([SENT] * 6)] * 12)                                # 2232 字、12 个自然段

# ---------------------------------------------------------------- split_text
wd = project("short", SHORT)
r, j = run("voice/split_text.py", "--workdir", wd)
check("不超过 1000 字：整篇一段（不按 16 个自然段拆）", r.returncode == 0 and j.get("segments") == [N * 16], j.get("segments"))
r, j = run("voice/split_text.py", "--workdir", wd)
check("已有 paragraphs.json：不覆盖（要重拆加 --force）", r.returncode == 0 and j.get("kept") is True)
r, j = run("voice/split_text.py", "--workdir", wd, "--user-split", "--force")
check("--user-split：一个自然段一段，并在文件里记下 user_split", j.get("segments") == [N] * 16
      and json.load(open(os.path.join(wd, "paragraphs.json"), encoding="utf-8")).get("user_split") is True)
r, j = run("voice/split_check.py", "--copy", os.path.join(wd, "copy.txt"), "--paragraphs", os.path.join(wd, "paragraphs.json"))
check("split_check：用户要按他的分段 → 放行", r.returncode == 0 and j.get("passed") is True)

wd2 = project("long", LONG)
r, j = run("voice/split_text.py", "--workdir", wd2)
paras = json.load(open(os.path.join(wd2, "paragraphs.json"), encoding="utf-8"))["paragraphs"]
check("超过 1000 字：拆成尽量少、尽量均匀的几段，每段 ≤1000 字", j.get("segments") and len(j["segments"]) == 3 and max(j["segments"]) <= 1000
      and max(j["segments"]) - min(j["segments"]) <= 200, j.get("segments"))
check("只断在句末", all(p.rstrip().endswith("。") for p in paras))

# ---------------------------------------------------------------- split_check：拦下碎拆和断在句中
cp, pj = os.path.join(wd, "copy.txt"), os.path.join(TMP, "p.json")
json.dump({"paragraphs": [SENT] * 16}, open(pj, "w", encoding="utf-8"), ensure_ascii=False)
r, j = run("voice/split_check.py", "--copy", cp, "--paragraphs", pj)
check("全文不到 1000 字却拆成 16 段：不合格，next 是重新拆段", r.returncode == 1 and "split_text.py" in (j.get("next") or ""), r.stdout[-200:])
half = SHORT.replace("\n", "")
json.dump({"paragraphs": [half[:100], half[100:]]}, open(pj, "w", encoding="utf-8"), ensure_ascii=False)
r, j = run("voice/split_check.py", "--copy", cp, "--paragraphs", pj, "--user-split")
check("断在句子中间：不合格（用户分段也不放行这一条）", r.returncode == 1 and "整句结束" in r.stdout)

# ---------------------------------------------------------------- pipeline voice：预览
wd3 = project("pipe", SHORT)
r, j = run("pipeline.py", "voice", "--workdir", wd3)
steps = [s["step"] for s in j.get("steps", [])]
check("pipeline voice 不加 --yes：自动拆段 → 把关 → 停在合成预览，不联网、不要密钥", r.returncode == 0 and j.get("preview") is True
      and steps == ["拆段", "拆段把关", "合成"] and not os.path.isdir(os.path.join(wd3, "out")), (steps, r.stderr[-200:]))
check("pipeline voice 预览：next 是加 --yes 的同一条命令", "pipeline.py voice" in (j.get("next") or "") and "--yes" in j["next"])
r, j = run("pipeline.py", "voice", "--workdir", wd3, "--yes")
check("pipeline voice --yes 没有密钥：停在“合成”，退出码 3，说明是哪一步", r.returncode == 3 and j.get("failed_step") == "合成", (r.returncode, j.get("failed_step")))
r, j = run("pipeline.py", "segments", "--workdir", wd3)
check("pipeline segments 还没对齐：停在“分段”，next 是先对齐", r.returncode == 1 and j.get("failed_step") == "分段" and "align_words" in (j.get("next") or ""),
      (j.get("failed_step"), j.get("next")))

# ---------------------------------------------------------------- next 字段
r, j = run("voice/split_check.py", "--copy", os.path.join(wd2, "copy.txt"), "--paragraphs", os.path.join(wd2, "paragraphs.json"))
check("机读结果总带 next：拆段合格后下一步是合成预览", j.get("passed") is True and "synth.py" in (j.get("next") or ""), j.get("next"))
r, j = run("voice/synth.py", "--workdir", wd2)
check("合成预览的 next：确认后加 --yes", j.get("preview") is True and "--yes" in (j.get("next") or "") and wd2 in j["next"], j.get("next"))

print("\n通过 %d/%d" % (sum(cases), len(cases)))
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if all(cases) else 1)
