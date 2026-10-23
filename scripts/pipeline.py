"""一条命令跑完一个环节：模型不用自己处理音频、也不用记先后顺序，照着结果里的 next 做就行。

  python scripts/pipeline.py voice    --workdir <项目目录> [--yes] [--user-split]
      拆段（没有 paragraphs.json 时自动拆）→ 拆段把关 → 合成（不加 --yes 只预览）→ 逐词对齐 → 核对
  python scripts/pipeline.py segments --workdir <项目目录> [--legacy]
      分段（停顿以配音为准、切点优先句末，并核对逐词时间和配音是同一份）
  python scripts/pipeline.py finish   --workdir <项目目录> [--legacy]
      拼接 → 质检（之后进入包装：packaging/remotion_new.py）

任何一步失败就停下：机读结果里 failed_step 是哪一步，error 是原因，next 是该运行的命令。
各步的输出原样打印出来。各步的脚本也可以单独运行，参数见 references/tools.md。
"""
import argparse
import json
import os
import subprocess
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _cli  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def step(name, script, args):
    """跑一步，原样打印输出；返回 (退出码, 这一步的机读结果)"""
    print("\n==== %s：%s %s" % (name, script, " ".join(args)), flush=True)
    r = subprocess.run([sys.executable, os.path.join(HERE, script), *args, "--json"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    lines = r.stdout.rstrip("\n").split("\n")
    res = {}
    if lines and lines[-1].startswith("{"):
        try:
            res = json.loads(lines[-1])
            lines = lines[:-1]
        except ValueError:
            pass
    if "\n".join(lines).strip():
        print("\n".join(lines), flush=True)
    if r.stderr.strip():
        print(r.stderr.rstrip(), file=sys.stderr, flush=True)
    return r.returncode, res


def run_steps(steps):
    done = []
    for name, script, args in steps:
        code, res = step(name, script, args)
        done.append({"step": name, "script": script, "exit": code, "ok": code == 0, "preview": bool(res.get("preview"))})
        if code != 0:
            _cli.report(steps=done, failed_step=name, next=res.get("next"))
            _cli.fail(code, "%s 没过：%s" % (name, res.get("error") or "看上面的输出"))
        if res.get("preview"):
            _cli.report(steps=done, preview=True, next=res.get("next"))
            print("\n[预览] 停在“%s”：确认后重新运行这条命令并加 --yes。" % name)
            return done, res
    return done, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["voice", "segments", "finish"])
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--user-split", action="store_true", help="voice：用户明确要求按他的分段配")
    ap.add_argument("--yes", action="store_true", help="voice：确认合成和识别（会产生少量费用）。不加就停在合成预览")
    ap.add_argument("--legacy", action="store_true", help="segments / finish：旧项目，结果文件里没有音频指纹也继续")
    a = ap.parse_args()
    wd = a.workdir
    lg = ["--legacy"] if a.legacy else []
    if a.stage == "voice":
        steps = []
        if not os.path.isfile(os.path.join(wd, "paragraphs.json")):
            steps.append(("拆段", "voice/split_text.py", ["--workdir", wd] + (["--user-split"] if a.user_split else [])))
        steps.append(("拆段把关", "voice/split_check.py", ["--copy", os.path.join(wd, "copy.txt"), "--paragraphs", os.path.join(wd, "paragraphs.json")]
                      + (["--user-split"] if a.user_split else [])))
        steps.append(("合成", "voice/synth.py", ["--workdir", wd] + (["--yes"] if a.yes else [])))
        steps.append(("逐词对齐", "voice/align_words.py", ["--workdir", wd, "--yes"]))
        steps.append(("核对", "voice/verify_audio.py", ["--workdir", wd]))
        done, last = run_steps(steps)
        if last.get("preview"):
            _cli.report(next="确认后：python scripts/pipeline.py voice --workdir %s --yes" % wd)
            return
        _cli.report(steps=done, issues=last.get("issues"), next="处理核对里的问题（issues）；没有问题就继续数字人的免费步骤：python scripts/avatar/check_setup.py。"
                         "用户开头说要先听配音的，现在就停下把 out/merged.wav 发给他试听；否则在提交生成前和费用一起请他试听确认")
    elif a.stage == "segments":
        done, last = run_steps([("分段", "avatar/plan_segments.py", ["--workdir", wd] + lg)])
        _cli.report(steps=done, segments=last.get("segments"), cut_punct=last.get("cut_punct"), next=last.get("next"))
    else:
        done, last = run_steps([("拼接", "avatar/finalize.py", ["--workdir", wd] + lg), ("质检", "avatar/qa_video.py", ["--workdir", wd])])
        _cli.report(steps=done, issues=last.get("issues"), must_view=last.get("must_view"),
                    next="用 Read 看 must_view 里的图；anomalies.json 里的时间段留给包装遮盖，然后进入包装：python scripts/packaging/remotion_new.py --workdir %s" % wd)


if __name__ == "__main__":
    _cli.run(main)
