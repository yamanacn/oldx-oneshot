"""检查拆段是否合格：不增不减原文、每段不超过上限，并提示拆得太碎或没必要拆。

  python split_check.py --copy 配音/copy.txt --paragraphs 配音/paragraphs.json

paragraphs.json 格式：{"paragraphs": ["第一大段……", "第二大段……"]}
拆段本身由 Claude 通读全文后按话题转折来做；本脚本只负责把关，不自动切。
退出码：0 合格；1 不合格（文字对不上或超长）。
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C


def squash(s):
    return re.sub(r"\s+", "", s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--copy", required=True)
    ap.add_argument("--paragraphs", required=True)
    a = ap.parse_args()
    orig = squash(open(a.copy, encoding="utf-8").read())
    paras = json.load(open(a.paragraphs, encoding="utf-8"))["paragraphs"]
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
    total = len(joined)
    if len(paras) > 1 and total <= C.MAX_CHARS:
        print("提示：全文只有 %d 字，不超过 %d 字，可以整段一次合成，不必拆。" % (total, C.MAX_CHARS))
    small = [i for i, p in enumerate(paras, 1) if len(squash(p)) < 60 and len(paras) > 1]
    if small:
        print("提示：段%s 很短（<60 字），拆得可能偏碎，考虑并入相邻段。" % "、".join(map(str, small)))
    print("合格" if ok else "不合格")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
