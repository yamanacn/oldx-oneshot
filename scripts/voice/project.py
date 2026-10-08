"""口播文案的本地缓存（项目目录）：每份文案一个目录，后续环节（配音、数字人……）共用。

  python project.py init --copy <文案文件>   # 建立或命中缓存，打印项目目录和已有进度（JSON）
  python project.py show [--dir <项目目录>]  # 查看某个项目的进度，默认最近一个
  python project.py latest                   # 打印最近一个项目目录

目录：<根>/projects/<日期-哈希前8位>/
  copy.txt    用户文案原文（一个字不改）
  meta.json   哈希、字数、创建时间、以及后续各环节写回的进度（音色、配音、逐词时间……）
  voice.json  paragraphs.json  cache/  out/   由配音技能的其他脚本生成
根目录默认 ~/.digital-human，可用环境变量 DH_WORKSPACE 改。
命中规则：文案去掉空白后的 sha256 相同即视为同一份文案，直接复用已有目录和音频。
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

PROJECTS = os.path.join(C.WORKSPACE_ROOT, "projects")
LATEST = os.path.join(C.WORKSPACE_ROOT, "latest.json")


def sha_of(text):
    return hashlib.sha256(re.sub(r"\s+", "", text).encode("utf-8")).hexdigest()


def state(d):
    o = os.path.join(d, "out")
    return {"voice": os.path.isfile(os.path.join(d, "voice.json")),
            "paragraphs": os.path.isfile(os.path.join(d, "paragraphs.json")),
            "audio": os.path.isfile(os.path.join(o, "merged.wav")),
            "words": os.path.isfile(os.path.join(o, "words.json"))}


def set_latest(d, pid):
    os.makedirs(C.WORKSPACE_ROOT, exist_ok=True)
    json.dump({"id": pid, "dir": d, "updated": time.strftime("%Y-%m-%d %H:%M:%S")},
              open(LATEST, "w", encoding="utf-8"), ensure_ascii=False)


def find_by_sha(sha):
    if not os.path.isdir(PROJECTS):
        return None
    for name in sorted(os.listdir(PROJECTS)):
        mp = os.path.join(PROJECTS, name, "meta.json")
        if os.path.isfile(mp) and json.load(open(mp, encoding="utf-8")).get("sha256") == sha:
            return os.path.join(PROJECTS, name)
    return None


def cmd_init(a):
    text = open(a.copy, encoding="utf-8-sig").read()
    if not text.strip():
        sys.exit("文案是空的")
    sha = sha_of(text)
    hit = find_by_sha(sha)
    if hit:
        pid = os.path.basename(hit)
        set_latest(hit, pid)
        print(json.dumps({"project_dir": hit, "cache_hit": True, "state": state(hit)}, ensure_ascii=False))
        return
    pid = "%s-%s" % (time.strftime("%Y%m%d"), sha[:8])
    d = os.path.join(PROJECTS, pid)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "copy.txt"), "w", encoding="utf-8", newline="").write(text)
    json.dump({"id": pid, "sha256": sha, "chars": len(re.sub(r"\s+", "", text)),
               "created_at": time.strftime("%Y-%m-%d %H:%M:%S")},
              open(os.path.join(d, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    set_latest(d, pid)
    print(json.dumps({"project_dir": d, "cache_hit": False, "state": state(d)}, ensure_ascii=False))


def cmd_show(a):
    d = a.dir
    if not d:
        if not os.path.isfile(LATEST):
            sys.exit("还没有任何项目")
        d = json.load(open(LATEST, encoding="utf-8"))["dir"]
    m = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
    print(json.dumps({"project_dir": d, "meta": m, "state": state(d)}, ensure_ascii=False, indent=1))


def cmd_latest(a):
    if not os.path.isfile(LATEST):
        sys.exit("还没有任何项目")
    print(json.load(open(LATEST, encoding="utf-8"))["dir"])


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    i = sp.add_parser("init")
    i.add_argument("--copy", required=True)
    s = sp.add_parser("show")
    s.add_argument("--dir")
    sp.add_parser("latest")
    a = ap.parse_args()
    {"init": cmd_init, "show": cmd_show, "latest": cmd_latest}[a.cmd](a)


if __name__ == "__main__":
    main()
