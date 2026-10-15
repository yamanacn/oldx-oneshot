"""每段的历史版本（take）：重跑时旧片段不会被覆盖，而是收进 avatar/takes/；可以随时把某段换回以前的版本再拼接。

  python takes.py --workdir <项目目录>                 # 列出每段现在用的是哪个版本、还有哪些历史版本
  python takes.py --workdir <项目目录> --use 6=3        # 第 6 段换成历史版本 3（现在用的那个收进历史）
  python takes.py --workdir <项目目录> --use 6=3 9=1    # 一次换几段
  python takes.py --workdir <项目目录> --show 6=3       # 看历史版本 3 当时提交的提示词

换完之后重新拼接和质检：finalize.py → qa_video.py。

每个版本记着：种子、工作流版本、提示词（全文）、任务号、生成时间、提交前的备注。
run_avatar.py 在重跑某段之前会自动调用 archive()，所以不需要手工复制 clips 目录。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _cli  # noqa: E402


def _fail(code, msg):
    _cli.fail(code, msg)


def data(od, plan):
    """机读的版本列表：每段在用的和历史版本。"""
    rp = _result_path(od)
    res = json.load(open(rp, encoding="utf-8")).get("results", {}) if os.path.isfile(rp) else {}
    out = []
    for s in plan["segments"]:
        r = res.get(str(s["i"])) or {}
        out.append({"segment": s["i"], "current": {k: r.get(k) for k in ("seed", "workflow", "created", "from_take", "prompt_sha", "note")} if r else None,
                    "takes": [{k: m.get(k) for k in ("take", "seed", "workflow", "created", "archived_at", "prompt_sha", "note")} for m in _load(od, s["i"])]})
    return out


def prompt_sha(text):
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:10]


def _dir(od, i):
    return os.path.join(od, "takes", "seg_%02d" % int(i))


def _load(od, i):
    d = _dir(od, i)
    out = []
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".json"):
                m = json.load(open(os.path.join(d, f), encoding="utf-8"))
                m["_json"] = os.path.join(d, f)
                out.append(m)
    return sorted(out, key=lambda m: m["take"])


def archive(od, i, meta=None, note=""):
    """把第 i 段现在的片段收进历史，返回版本号；没有片段就返回 None。meta 是 result.json 里这一段的记录。"""
    clip = os.path.join(od, "clips", "seg_%02d.mp4" % int(i))
    if not os.path.isfile(clip):
        return None
    d = _dir(od, i)
    os.makedirs(d, exist_ok=True)
    n = max([m["take"] for m in _load(od, i)] + [0]) + 1
    dst = os.path.join(d, "take_%02d.mp4" % n)
    shutil.move(clip, dst)
    meta = dict(meta or {})
    rec = {"take": n, "segment": int(i), "file": dst, "archived_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "seed": meta.get("seed"), "workflow": meta.get("workflow"), "app_id": meta.get("app_id"),
           "prompt_sha": meta.get("prompt_sha"), "prompt": meta.get("prompt"), "task": meta.get("task"),
           "created": meta.get("created"), "note": note or meta.get("note", "")}
    json.dump(rec, open(os.path.join(d, "take_%02d.json" % n), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return n


def _result_path(od):
    return os.path.join(od, "result.json")


def use(od, i, n):
    """第 i 段换成历史版本 n：现在的片段收进历史，版本 n 放回 clips/，result.json 跟着改。"""
    i, n = int(i), int(n)
    takes = {m["take"]: m for m in _load(od, i)}
    if n not in takes:
        _fail(2, "段%d 没有历史版本 %d。现有：%s" % (i, n, "、".join(map(str, takes)) or "无"))
    rp = _result_path(od)
    res = json.load(open(rp, encoding="utf-8")) if os.path.isfile(rp) else {"results": {}, "errors": {}, "pending": {}, "seeds": {}}
    cur = res.get("results", {}).get(str(i))
    if res.get("pending", {}).get(str(i)):
        _fail(1, "段%d 还有云端任务没跑完，先等它结束再换。" % i)
    new_no = archive(od, i, cur, note="换成版本 %d 之前在用的" % n)
    t = takes[n]
    clip = os.path.join(od, "clips", "seg_%02d.mp4" % i)
    os.makedirs(os.path.dirname(clip), exist_ok=True)
    shutil.move(t["file"], clip)
    os.remove(t["_json"])
    entry = {k: t.get(k) for k in ("seed", "workflow", "app_id", "prompt_sha", "prompt", "task", "created")}
    entry.update({"i": i, "file": clip, "usage": None, "from_take": n})
    res.setdefault("results", {})[str(i)] = entry
    if t.get("seed") is not None:
        res.setdefault("seeds", {})[str(i)] = t["seed"]
    json.dump(res, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return new_no


def listing(od, plan):
    rp = _result_path(od)
    res = json.load(open(rp, encoding="utf-8")).get("results", {}) if os.path.isfile(rp) else {}
    cur_prompts = {}
    for s in plan["segments"]:
        p = os.path.join(od, "prompts", "segment_%02d.txt" % s["i"])
        cur_prompts[s["i"]] = prompt_sha(open(p, encoding="utf-8").read()) if os.path.isfile(p) else None
    lines = []
    workflows = set()
    for s in plan["segments"]:
        i = s["i"]
        r = res.get(str(i))
        if r:
            workflows.add(r.get("workflow") or "未记录")
            stale = r.get("prompt_sha") and cur_prompts[i] and r["prompt_sha"] != cur_prompts[i]
            lines.append("段%-2d 在用：种子 %s，工作流 %s，生成于 %s%s%s" % (
                i, r.get("seed"), r.get("workflow") or "未记录", r.get("created") or "?",
                "（来自历史版本 %s）" % r["from_take"] if r.get("from_take") else "",
                "  ⚠ 现在的提示词和生成它时不一样" if stale else ""))
        else:
            lines.append("段%-2d 在用：无" % i)
        for m in _load(od, i):
            lines.append("      版本 %-2d 种子 %s，工作流 %s，生成于 %s，收进历史于 %s%s" % (
                m["take"], m.get("seed"), m.get("workflow") or "未记录", m.get("created") or "?", m["archived_at"],
                "，%s" % m["note"] if m.get("note") else ""))
    if len(workflows) > 1:
        lines.append("注意：现在用的片段来自不同的工作流版本（%s），前后画面可能不一致。" % "、".join(sorted(workflows)))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--use", nargs="*", help="段号=版本号，可以写几个")
    ap.add_argument("--show", help="段号=版本号：打印那个版本当时提交的提示词")
    a = ap.parse_args()
    od = os.path.join(a.workdir, "avatar")
    plan = json.load(open(os.path.join(od, "plan.json"), encoding="utf-8"))
    if a.show:
        i, n = a.show.split("=")
        m = {x["take"]: x for x in _load(od, i)}.get(int(n))
        if not m:
            _fail(2, "没有这个版本")
        print(m.get("prompt") or "（这个版本没有记下提示词）")
        _cli.report(segment=int(i), take=int(n), prompt=m.get("prompt"), seed=m.get("seed"), workflow=m.get("workflow"))
        return
    if a.use:
        swapped = []
        for pair in a.use:
            i, n = pair.split("=")
            kept = use(od, i, n)
            swapped.append({"segment": int(i), "now_using_take": int(n), "previous_kept_as_take": kept})
            print("段%s 换成了版本 %s%s" % (i, n, "；原来在用的收进历史，版本号 %d" % kept if kept else ""))
        print("接着重新拼接和质检：finalize.py → qa_video.py")
        _cli.report(swapped=swapped, next="finalize.py → qa_video.py")
        return
    print(listing(od, plan))
    _cli.report(segments=data(od, plan))


if __name__ == "__main__":
    _cli.run(main)
