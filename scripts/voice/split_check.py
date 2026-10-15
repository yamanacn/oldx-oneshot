"""检查拆段是否合格：不增不减原文、每段不超过上限、只断在句末、没有拆得过碎。

  python split_check.py --copy 配音/copy.txt --paragraphs 配音/paragraphs.json

paragraphs.json 格式：{"paragraphs": ["第一大段……", "第二大段……"]}
拆段用 split_text.py 按规则自动做；本脚本把关（手工拆的也照样查）。
不合格（退出码 1）的情况：文字对不上、某段超长、某段不是在整句结束处断开、全文不超过上限却拆成了多段、有不到 60 字的短段。
后两条在用户明确要求按他的分段配时（paragraphs.json 里 user_split 为 true，或加 --user-split）放行。
"""
import argparse
import json
import os
import re
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _cli  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C


def squash(s):
    return re.sub(r"\s+", "", s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--copy", required=True)
    ap.add_argument("--paragraphs", required=True)
    ap.add_argument("--user-split", action="store_true", help="用户明确要求按他的分段配")
    a = ap.parse_args()
    orig = squash(open(a.copy, encoding="utf-8").read())
    pj = json.load(open(a.paragraphs, encoding="utf-8"))
    paras = pj["paragraphs"]
    user_split = a.user_split or bool(pj.get("user_split"))
    joined = squash("".join(paras))
    ok = True
    if joined != orig:
        ok = False
        n = next((i for i, (x, y) in enumerate(zip(joined, orig)) if x != y), min(len(joined), len(orig)))
        print("✗ 拆段后的文字和原文不一致：原文 %d 字，拆段后 %d 字，第 %d 个字起不同" % (len(orig), len(joined), n + 1))
        print("  原文附近：", orig[max(0, n - 8):n + 12])
        print("  拆段附近：", joined[max(0, n - 8):n + 12])
    for i, p in enumerate(paras, 1):
        n = len(squash(p))
        flag = ""
        if n > C.MAX_CHARS:
            ok = False
            flag = "  ✗ 超过 %d 字上限" % C.MAX_CHARS
        print("段%d：%d 字%s" % (i, n, flag))
    mid = [i for i, p in enumerate(paras[:-1], 1) if not re.search(r"[。！？!?…][”」』）)\"']*\s*$", p)]
    if mid:
        ok = False
        print("✗ 段%s 不是在整句结束处断开的（结尾不是。！？）：不要在句子中间断开" % "、".join(map(str, mid)))
    total = len(joined)
    small = [i for i, p in enumerate(paras, 1) if len(squash(p)) < 60 and len(paras) > 1]
    if not user_split:
        if len(paras) > 1 and total <= C.MAX_CHARS:
            ok = False
            print("✗ 全文只有 %d 字，不超过 %d 字，应该整篇一段合成（拆开后段与段之间的停顿又长又机械）" % (total, C.MAX_CHARS))
        elif small:
            ok = False
            print("✗ 段%s 很短（<60 字），拆得过碎" % "、".join(map(str, small)))
    print("合格" if ok else "不合格")
    wd = os.path.dirname(os.path.abspath(a.paragraphs))
    _cli.report(passed=ok, segments=[len(squash(p)) for p in paras], max_chars=C.MAX_CHARS, user_split=user_split)
    if not ok:
        _cli.report(next="python scripts/voice/split_text.py --workdir %s --force（按规则重新拆，不要手工拆）" % wd)
    sys.exit(_cli.OK if ok else _cli.ERROR)


if __name__ == "__main__":
    _cli.run(main)
