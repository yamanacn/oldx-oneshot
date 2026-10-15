"""配音前的拆段，按固定规则自动做，不靠模型判断：写 <workdir>/paragraphs.json。

  python split_text.py --workdir <项目目录>                # 读 copy.txt
  python split_text.py --workdir <项目目录> --user-split   # 用户明确说“按我的分段配”时才用：用户的每个自然段一段

规则（2026-10-14 用户定）：
  * 全文不超过 1000 字：整篇一段，不拆。分段越少，各段语气越一致；每段是分开合成、再用固定间隔拼起来的，
    拆得碎了，段与段之间的停顿又长又机械（实测：拆成 16 段时段间停顿平均 0.49 秒，句子之间只有 0.33 秒）。
  * 超过 1000 字：拆成尽量少、长度尽量均匀的几段，每段不超过 1000 字。只在整句结束的地方断（。！？），
    优先断在用户换行的地方；永远不断在句子中间。
  * 用户的换行只是“可以断开的位置”，不是“每行一段”。只有 --user-split 才一个自然段一段（超长的自然段在句末再拆）。
  * 不改字：各段拼起来等于原文（空白除外）。
"""
import argparse
import json
import math
import os
import re
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _cli  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

SENT_END = r"[。！？!?…]+[”」』）)\"']*"
LINE_BONUS = 1.0     # 断在用户换行处比断在行内句末好多少


def vis(s):
    return len(re.sub(r"\s+", "", s))


def sentences(text):
    """→ [(句子原文, 后面是不是用户的换行)]。句子保留原文里的标点和空白。"""
    out = []
    lines = text.split("\n")
    for li, line in enumerate(lines):
        if not line.strip():
            continue
        for m in re.finditer(r".+?(?:%s|$)" % SENT_END, line):
            if m.group():
                out.append([m.group(), False])
        if out:
            out[-1][1] = True
    return out


def ends_sentence(s):
    return re.search(SENT_END + r"\s*$", s) is not None


def split_auto(text, max_chars):
    sents = sentences(text)
    total = sum(vis(s) for s, _ in sents)
    if total <= max_chars:
        return [text.strip()]
    lens = [vis(s) for s, _ in sents]
    # 能断的位置：句末之后（行尾的句末更好）
    cut_cost = {}
    for i, (s, nl) in enumerate(sents[:-1]):
        if ends_sentence(s):
            cut_cost[i + 1] = 0.0 if nl else LINE_BONUS
    pre = [0]
    for n in lens:
        pre.append(pre[-1] + n)
    for k in range(math.ceil(total / max_chars), len(sents) + 1):
        unit = total / k
        best = {0: (0.0, [])}
        for step in range(k):
            nxt = {}
            for i, (c, path) in best.items():
                for j in [x for x in cut_cost if x > i] + ([len(sents)] if step == k - 1 else []):
                    size = pre[j] - pre[i]
                    if size > max_chars:
                        break
                    cc = c + ((size - unit) / unit) ** 2 + (cut_cost.get(j, 0.0) if j < len(sents) else 0.0)
                    if j not in nxt or cc < nxt[j][0]:
                        nxt[j] = (cc, path + [j])
            best = nxt
        if len(sents) in best:
            bounds = [0] + best[len(sents)][1]
            return ["".join(s for s, _ in sents[a:b]).strip() for a, b in zip(bounds, bounds[1:])]
    _cli.fail(_cli.ERROR, "有一句话超过 %d 字，没法只在句末断开：在那句话里加一个句号或问号再试" % max_chars)


def split_user(text, max_chars):
    paras = []
    for block in [b for b in text.split("\n") if b.strip()]:
        paras += split_auto(block, max_chars) if vis(block) > max_chars else [block.strip()]
    return paras


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--copy", help="文案文件（默认 <workdir>/copy.txt）")
    ap.add_argument("--user-split", action="store_true", help="用户明确要求按他的分段配：一个自然段一段")
    ap.add_argument("--max-chars", type=int, default=C.MAX_CHARS)
    ap.add_argument("--force", action="store_true", help="已有 paragraphs.json 时覆盖")
    a = ap.parse_args()
    cp = a.copy or os.path.join(a.workdir, "copy.txt")
    if not os.path.isfile(cp):
        _cli.fail(_cli.USAGE, "文案不存在：%s（先运行 project.py init --copy <文案文件>）" % cp)
    pj = os.path.join(a.workdir, "paragraphs.json")
    if os.path.isfile(pj) and not a.force:
        old = json.load(open(pj, encoding="utf-8"))
        _cli.report(paragraphs=pj, segments=[vis(p) for p in old.get("paragraphs", [])], kept=True)
        print("已有 %s（%d 段），没有覆盖；要按规则重新拆就加 --force。" % (pj, len(old.get("paragraphs", []))))
        return
    text = open(cp, encoding="utf-8-sig").read().replace("\r\n", "\n")
    paras = split_user(text, a.max_chars) if a.user_split else split_auto(text, a.max_chars)
    if re.sub(r"\s+", "", "".join(paras)) != re.sub(r"\s+", "", text):
        _cli.fail(_cli.ERROR, "拆段后的文字和原文对不上（脚本出错），请把文案发给维护者")
    json.dump({"paragraphs": paras, "user_split": bool(a.user_split)}, open(pj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("拆成 %d 段%s：%s" % (len(paras), "（按用户的分段）" if a.user_split else "", "、".join("%d 字" % vis(p) for p in paras)))
    for i, p in enumerate(paras, 1):
        q = re.sub(r"\s+", "", p)
        print("  段%d：%s……%s" % (i, q[:12], q[-8:]))
    _cli.report(paragraphs=pj, segments=[vis(p) for p in paras], user_split=bool(a.user_split))


if __name__ == "__main__":
    _cli.run(main)
