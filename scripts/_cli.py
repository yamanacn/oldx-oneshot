"""配音、数字人两个阶段可单独运行的脚本共用的命令行约定（包装阶段的脚本在 scripts/packaging/，不走这一套）：退出码、--json 机读结果、UTF-8 输出。

用法（脚本里）：
    import _cli
    ...
    _cli.report(output="...", segments=12)      # 记下要放进机读结果的内容，可以调多次（同名键后写的覆盖先写的）
    _cli.fail(_cli.NEED_USER, "需要用户提供一段人声样本")   # 按类别退出，消息打到 stderr
    ...
    if __name__ == "__main__":
        _cli.run(main)

调用方加 --json：脚本照常打印给人看的中文，**最后一行**额外输出一行 JSON：
    {"script": "synth.py", "ok": true, "exit": 0, "exit_name": "ok", "error": null, ...report 里的内容}
其他 agent 只读最后一行就能拿到结果。--json 由 run() 从命令行里拿掉，各脚本的参数解析不用管它。

退出码（全技能统一）：
    0 ok              成功
    1 error           一般错误、校验没通过、缺前一步的产物（看 error 里的说明）
    2 usage           参数写错、给的文件不存在
    3 no_key          本机没有密钥
    4 key_rejected    密钥被拒绝
    5 need_user       需要用户先提供东西或做决定（会员档位、人声样本……）
    6 file_locked     输出文件被别的程序占用
    7 deps            依赖没装上
    8 remote_failed   云端任务或接口调用失败

机读结果里总有一个 next：下一步要运行的命令（或要做的事），照着做就行，不用自己判断。脚本自己写了 next 的以脚本为准，
没写的用下面 NEXT 表里的默认值；出错时 next 一般为空，看 error 里的说明。
"""
import json
import os
import sys
import traceback

OK, ERROR, USAGE, NO_KEY, KEY_REJECTED, NEED_USER, FILE_LOCKED, DEPS, REMOTE_FAILED = range(9)
NAMES = {OK: "ok", ERROR: "error", USAGE: "usage", NO_KEY: "no_key", KEY_REJECTED: "key_rejected",
         NEED_USER: "need_user", FILE_LOCKED: "file_locked", DEPS: "deps", REMOTE_FAILED: "remote_failed"}

_state = {"json": False, "report": {}, "error": None}

W = "{workdir}"
# 成功之后的下一步：(正常完成, 只是预览)。{workdir} 换成这次的项目目录
NEXT = {
    "setup_env.py": ("python scripts/voice/check_key.py；要做人物视频再运行 python scripts/avatar/check_setup.py", None),
    "check_key.py": ("python scripts/voice/project.py init --copy <文案文件>", None),
    "project.py": ("python scripts/voice/clone_voice.py --workdir %s" % W, None),
    "clone_voice.py": ("python scripts/pipeline.py voice --workdir %s（先预览）" % W,
                       "确认复刻（会产生费用）：python scripts/voice/clone_voice.py --workdir %s --sample <样本> --yes" % W),
    "split_text.py": ("python scripts/pipeline.py voice --workdir %s（先预览）" % W, None),
    "split_check.py": ("python scripts/voice/synth.py --workdir %s（先预览）" % W, None),
    "synth.py": ("python scripts/voice/align_words.py --workdir %s --yes" % W, "确认后：python scripts/voice/synth.py --workdir %s --yes" % W),
    "align_words.py": ("python scripts/voice/verify_audio.py --workdir %s" % W, "确认后：python scripts/voice/align_words.py --workdir %s --yes" % W),
    "verify_audio.py": ("处理 issues 里的问题；没有问题就做数字人：python scripts/avatar/check_setup.py", None),
    "check_setup.py": ("python scripts/avatar/prepare_image.py --image <图片> --workdir %s --mouth-box x0,y0,x1,y1" % W, None),
    "prepare_image.py": ("打开 crop_check 看裁剪；没问题就运行 python scripts/pipeline.py segments --workdir %s" % W, None),
    "build_prompts.py": ("python scripts/avatar/run_avatar.py --workdir %s（预览，报费用）" % W, None),
    "run_avatar.py": ("python scripts/pipeline.py finish --workdir %s" % W, "把预览里的段数和预计费用告诉用户，用户确认后：python scripts/avatar/run_avatar.py --workdir %s --yes" % W),
    "finalize.py": ("python scripts/avatar/qa_video.py --workdir %s" % W, None),
    "qa_video.py": ("用 Read 看 must_view 里的图；anomalies.json 里的时间段留给包装遮盖，然后进入包装：python scripts/packaging/remotion_new.py --workdir %s" % W, None),
    "takes.py": ("换了版本的话：python scripts/pipeline.py finish --workdir %s" % W, None),
}


def _default_next(code):
    if code != OK or "next" in _state["report"]:
        return _state["report"].get("next")
    ok, preview = NEXT.get(os.path.basename(sys.argv[0]), (None, None))
    nxt = preview if _state["report"].get("preview") and preview else ok
    if nxt and W in nxt:
        args = sys.argv[1:]
        wd = args[args.index("--workdir") + 1] if "--workdir" in args[:-1] else None
        nxt = nxt.replace(W, wd) if wd else nxt.replace(W, "<项目目录>")
    return nxt


def setup_utf8():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def report(**kw):
    """记下机读结果里的字段。"""
    _state["report"].update(kw)


def fail(code, msg):
    """打印消息到 stderr 并按类别退出。"""
    _state["error"] = msg
    print(msg, file=sys.stderr, flush=True)
    raise SystemExit(code)


def run(main):
    """脚本入口：统一 UTF-8 输出、处理 --json、把各种退出方式归到统一的退出码。"""
    setup_utf8()
    if "--json" in sys.argv[1:]:
        sys.argv = [sys.argv[0]] + [x for x in sys.argv[1:] if x != "--json"]
        _state["json"] = True
    code = OK
    try:
        main()
    except SystemExit as e:
        if isinstance(e.code, str):                     # sys.exit("一句话")：当作一般错误
            _state["error"] = _state["error"] or e.code
            print(e.code, file=sys.stderr, flush=True)
            code = ERROR
        else:
            code = int(e.code or 0)
            if code == USAGE and not _state["error"]:
                _state["error"] = "参数写错了，用法见上面的说明（或加 -h 查看）"
    except KeyboardInterrupt:
        _state["error"] = "被中断"
        code = ERROR
    except Exception as e:                               # 未预料的异常：照常打出堆栈，机读结果里给一句
        traceback.print_exc()
        _state["error"] = "%s: %s" % (type(e).__name__, e)
        code = ERROR
    if _state["json"]:
        payload = {"script": os.path.basename(sys.argv[0]), "ok": code == OK, "exit": code,
                   "exit_name": NAMES.get(code, str(code)), "error": _state["error"]}
        payload.update(_state["report"])
        payload["next"] = _default_next(code)
        sys.stdout.flush()
        print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)
    sys.exit(code)
