"""历史版本与工作流版本的离线测试（不联网、不花钱）。  python tests/avatar/test_takes.py"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "scripts", "avatar"))
import common as C
import run_avatar as R
import takes as TK

cases = []


def check(name, ok, extra=""):
    print("✓" if ok else "✗", name, extra)
    cases.append(bool(ok))


def make_project():
    wd = tempfile.mkdtemp()
    od = os.path.join(wd, "avatar")
    os.makedirs(os.path.join(od, "clips"))
    os.makedirs(os.path.join(od, "prompts"))
    segs = []
    for i in (1, 2):
        a = os.path.join(od, "seg_%02d.wav" % i)
        open(a, "wb").write(b"wav")
        segs.append({"i": i, "start": (i - 1) * 5.0, "end": i * 5.0, "duration": 5.0, "audio": a})
        open(os.path.join(od, "prompts", "segment_%02d.txt" % i), "w", encoding="utf-8").write("提示词 %d 第一版" % i)
        open(os.path.join(od, "clips", "seg_%02d.mp4" % i), "wb").write(b"OLD-%d" % i)
    json.dump({"segments": segs}, open(os.path.join(od, "plan.json"), "w", encoding="utf-8"))
    img = os.path.join(od, "image.png")
    open(img, "wb").write(b"png")
    json.dump({"width": 768, "height": 1376, "uploaded_image": img}, open(os.path.join(od, "image_info.json"), "w", encoding="utf-8"))
    res = {"results": {str(i): {"i": i, "file": os.path.join(od, "clips", "seg_%02d.mp4" % i), "seed": 42, "workflow": "旧版",
                                "prompt": "提示词 %d 第一版" % i, "prompt_sha": TK.prompt_sha("提示词 %d 第一版" % i), "created": "2026-10-01 10:00:00"}
                       for i in (1, 2)}, "errors": {}, "pending": {}, "seeds": {"1": 42, "2": 42}}
    json.dump(res, open(os.path.join(od, "result.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return wd, od


def fake_run_one(seg, prompt, img_val, w, h, key, instance, out_dir, timeout_min, tries, state, interval=10, backoff=15.0, seed=None, meta=None, **kw):
    i = seg["i"]
    dst = os.path.join(out_dir, "seg_%02d.mp4" % i)
    assert not os.path.exists(dst), "旧片段应该已经被收走"
    open(dst, "wb").write(b"NEW-%d" % i)
    res = {"i": i, "file": dst, "seed": seed, "workflow": meta.get("workflow"), "prompt": meta.get("prompt"),
           "prompt_sha": meta.get("prompt_sha"), "note": meta.get("note"), "created": "2026-10-14 12:00:00", "usage": None}
    state.set("results", i, res)
    return res


def run_main(args):
    old_argv = sys.argv
    sys.argv = ["run_avatar.py"] + args + ["--legacy"]          # 手搓的分段计划没有音频指纹
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            R.main()
    finally:
        sys.argv = old_argv
    return buf.getvalue()


# 隔离：不碰真实配置、不联网
C.workflow = lambda: ("新版", "2026-10-14 11:00:00")
C.require_key = lambda: "k"
C.upload = lambda path, key: "uploaded"
C.load_config = lambda: {"plan": "测试", "concurrency": 2}
R.run_one = fake_run_one

wd, od = make_project()
out = run_main(["--workdir", wd, "--only", "1"])
check("预览：不收历史、不改文件", open(os.path.join(od, "clips", "seg_01.mp4"), "rb").read() == b"OLD-1" and not os.path.isdir(os.path.join(od, "takes"))
      and "收进历史版本" in out and "工作流版本：新版" in out)

out = run_main(["--workdir", wd, "--only", "1", "--voice-approved", "测试：配音可以", "--yes", "--note", "第二版"])
t1 = os.path.join(od, "takes", "seg_01", "take_01.mp4")
meta1 = json.load(open(os.path.join(od, "takes", "seg_01", "take_01.json"), encoding="utf-8")) if os.path.isfile(t1) else {}
check("重跑第 1 段：旧片段收进历史版本 1，带着当时的种子、工作流、提示词",
      os.path.isfile(t1) and open(t1, "rb").read() == b"OLD-1" and meta1.get("seed") == 42 and meta1.get("workflow") == "旧版"
      and meta1.get("prompt") == "提示词 1 第一版")
res = json.load(open(os.path.join(od, "result.json"), encoding="utf-8"))
check("新片段记下了这次的工作流版本和备注", open(os.path.join(od, "clips", "seg_01.mp4"), "rb").read() == b"NEW-1"
      and res["results"]["1"]["workflow"] == "新版" and res["results"]["1"]["note"] == "第二版")
check("没重跑的第 2 段不动", open(os.path.join(od, "clips", "seg_02.mp4"), "rb").read() == b"OLD-2" and not os.path.isdir(os.path.join(od, "takes", "seg_02")))
check("预览提示：沿用的段是别的工作流版本生成的", "段2 是在别的工作流版本下生成的" in run_main(["--workdir", wd, "--only", "1"]))

listing = TK.listing(od, json.load(open(os.path.join(od, "plan.json"), encoding="utf-8")))
check("列表：显示在用的和历史版本，并提示混用了工作流版本", "版本 1" in listing and "来自不同的工作流版本" in listing)

kept = TK.use(od, 1, 1)
res = json.load(open(os.path.join(od, "result.json"), encoding="utf-8"))
check("换回版本 1：旧片段回到 clips，刚才在用的收进历史版本 2",
      open(os.path.join(od, "clips", "seg_01.mp4"), "rb").read() == b"OLD-1" and kept == 2
      and open(os.path.join(od, "takes", "seg_01", "take_02.mp4"), "rb").read() == b"NEW-1"
      and res["results"]["1"]["from_take"] == 1 and res["results"]["1"]["seed"] == 42 and res["seeds"]["1"] == 42)
check("换过的版本从历史里拿掉，不重复", not os.path.isfile(t1))

open(os.path.join(od, "prompts", "segment_02.txt"), "w", encoding="utf-8").write("提示词 2 第二版")
check("提示词改过但片段没重跑 → 列表里标出来", "现在的提示词和生成它时不一样" in TK.listing(od, json.load(open(os.path.join(od, "plan.json"), encoding="utf-8"))))

wd2, od2 = make_project()
run_main(["--workdir", wd2, "--all", "--voice-approved", "测试：配音可以", "--yes"])
check("--all：两段都重跑，旧片段都收进历史", all(os.path.isfile(os.path.join(od2, "takes", "seg_%02d" % i, "take_01.mp4")) for i in (1, 2))
      and all(open(os.path.join(od2, "clips", "seg_%02d.mp4" % i), "rb").read() == b"NEW-%d" % i for i in (1, 2)))

# ---------------------------------------------------------------- 配音试听确认（写在代码里的门槛）
def run_code(args):
    try:
        run_main(args)
        return 0
    except SystemExit as e:
        return e.code


wd3, od3 = make_project()
os.makedirs(os.path.join(wd3, "out"))
open(os.path.join(wd3, "out", "merged.wav"), "wb").write(b"VOICE-A")
before = open(os.path.join(od3, "result.json"), encoding="utf-8").read()
code = run_code(["--workdir", wd3, "--only", "1", "--yes"])
check("没有配音确认：--yes 退出码 5，不提交，result.json 和片段都不动",
      code == 5 and open(os.path.join(od3, "result.json"), encoding="utf-8").read() == before
      and open(os.path.join(od3, "clips", "seg_01.mp4"), "rb").read() == b"OLD-1" and not os.path.isdir(os.path.join(od3, "takes")), code)
out = run_main(["--workdir", wd3, "--only", "1"])
check("预览里写出配音还没确认，提示和费用一起问", "还没有用户确认这一版配音" in out)
code = run_code(["--workdir", wd3, "--only", "1", "--voice-approved", "配音可以，开始吧", "--yes"])
vr = json.load(open(os.path.join(wd3, "voice_review.json"), encoding="utf-8"))
check("带上用户原话：提交成功，原话和配音指纹记进 voice_review.json",
      code == 0 and vr.get("approved") == "配音可以，开始吧" and vr.get("audio_sha")
      and open(os.path.join(od3, "clips", "seg_01.mp4"), "rb").read() == b"NEW-1", (code, vr))
check("确认过之后预览显示用户的原话", "配音可以，开始吧" in run_main(["--workdir", wd3, "--only", "2"]))
code = run_code(["--workdir", wd3, "--only", "2", "--yes"])
check("配音没变：重跑别的段不用再给原话", code == 0 and open(os.path.join(od3, "clips", "seg_02.mp4"), "rb").read() == b"NEW-2", code)
open(os.path.join(wd3, "out", "merged.wav"), "wb").write(b"VOICE-B")
code = run_code(["--workdir", wd3, "--only", "1", "--yes"])
check("配音重新合成过：旧的确认作废，退出码 5", code == 5, code)

print("\n通过 %d/%d" % (sum(cases), len(cases)))
sys.exit(0 if all(cases) else 1)
