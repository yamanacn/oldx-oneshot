"""按原文案做字幕：一句一条、保留句中的标点，时间从逐词时间对齐得到。

用法：
  python captions_from_copy.py words.json --copy copy.txt --out subs.json
  python captions_from_copy.py words.json --copy copy.txt --out subs.json --max-chars 14      # 竖屏一行放得下的字少

为什么不直接用识别出来的字分行（subs_from_words.py 的做法）：那样得到的是没有标点的碎短语，
一条只停半秒，小数点和英文之间的空格也会丢（“Opus 5.5”变成“Opus55”）。字幕应该是观众读得顺的原文。

规则：
  * 文字用原文案，一个字不改；句中的逗号、顿号、问号、感叹号保留，句末的句号和波浪号去掉。
  * 一句一条；超过 --max-chars 的句子在逗号、冒号、分号处分开，不在词中间断。
  * 很短的一条（停留不到 --min-sec 秒）并到相邻的一条里，但不把两句完整的话硬拼成一条。
  * 中文和英文、数字之间留一个空格。
  * 每条显示到下一条开始；后面停顿超过 0.9 秒时，说完 0.5 秒就收掉。
输出：[{"text", "start", "end"}]（秒），和 subs_from_words.py 的输出通用。
"""
import argparse
import difflib
import json
import re
import sys
from pathlib import Path

import _common  # noqa: F401  统一 stdout 编码

STRIP = r"[\s，。、！？!?；;：:“”\"'（）()\-—…,.~～]"
CJK = "[一-鿿]"


def vis(s):
    return len(re.sub(r"\s", "", s))


def tidy(s):
    s = re.sub(r"[。~～；;]+$", "", s.strip())
    return re.sub(r"[，、：:；;]+$", "", s)


def split_copy(copy, max_chars):
    """原文 → 一条一条的字幕文字"""
    copy = re.sub(r"！+", "！", copy)
    sents = [s.strip() for s in re.split(r"(?<=[。！？!?~～；;])(?![”\"])|(?<=[。！？!?~～][”\"])|\n", copy) if s and s.strip()]
    chunks = []
    for s in sents:
        s = tidy(s)
        if vis(s) <= max_chars:
            chunks.append(s)
            continue
        cur = ""
        for part in (p for p in re.split(r"(?<=[，,：:；;])", s) if p):
            if cur and vis(cur + part) > max_chars:
                chunks.append(tidy(cur))
                cur = part
            else:
                cur += part
        if cur:
            chunks.append(tidy(cur))
    return [c for c in chunks if c]


def align(chunks, words):
    """每条字幕的起止时间：把原文逐字对到识别结果上（识别有同音错字，所以对齐而不是查找）"""
    a_chars, a_time = [], []
    for w in words:
        t = re.sub(STRIP, "", w.get("text") or w.get("word") or "")
        for k, ch in enumerate(t):
            a_chars.append(ch.lower())
            a_time.append((w["start"] + (w["end"] - w["start"]) * k / max(1, len(t)), w["end"]))
    c_chars, owner = [], []
    for i, c in enumerate(chunks):
        for ch in re.sub(STRIP, "", c):
            c_chars.append(ch.lower())
            owner.append(i)
    start, end = [None] * len(chunks), [None] * len(chunks)
    sm = difflib.SequenceMatcher(None, "".join(c_chars), "".join(a_chars), autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("delete", "insert"):
            continue
        for k in range(i1, i2):
            j = j1 + (k - i1) * max(1, j2 - j1) // max(1, i2 - i1) if tag == "replace" else j1 + (k - i1)
            if j >= len(a_time):
                continue
            o = owner[k]
            if start[o] is None:
                start[o] = a_time[j][0]
            end[o] = a_time[j][1]
    for i in range(len(chunks)):               # 整条都没对上的，夹在前一条后面
        if start[i] is None:
            start[i] = end[i - 1] if i and end[i - 1] is not None else 0.0
            end[i] = start[i] + 0.8
    return start, end


def build(words, copy, max_chars=22, min_sec=1.0):
    chunks = split_copy(copy, max_chars)
    start, end = align(chunks, words)
    merged = []
    short_cap = max(8, int(max_chars * 0.65))       # 并出来的一条不超过这么长，免得把两句完整的话拼在一起
    for txt, s, e in zip(chunks, start, end):
        if merged and (merged[-1][2] - merged[-1][1] < min_sec or e - s < min_sec) and vis(merged[-1][0] + txt) <= short_cap and s - merged[-1][2] < 0.6:
            sep = "" if re.search(r"[，、：:！？!?]$", merged[-1][0]) else "，"
            merged[-1] = [merged[-1][0] + sep + txt, merged[-1][1], e]
        else:
            merged.append([txt, s, e])
    out = []
    for i, (txt, s, e) in enumerate(merged):
        nxt = merged[i + 1][1] if i + 1 < len(merged) else e + 0.6
        stop = min(nxt, e + 0.5) if nxt - e > 0.9 else nxt
        txt = re.sub("(" + CJK + ")(?=[A-Za-z0-9])", r"\1 ", txt)
        txt = re.sub("([A-Za-z0-9.]+)(?=" + CJK + ")", r"\1 ", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        out.append({"text": txt, "start": round(max(0.0, s - 0.06), 3), "end": round(max(stop, s + 0.6), 3)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("words")
    ap.add_argument("--copy", required=True, help="原文案文本文件")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-chars", type=int, default=22, help="一条最多多少个字（横屏 22 左右，竖屏 14 左右）")
    ap.add_argument("--min-sec", type=float, default=1.0, help="一条至少停留多久")
    a = ap.parse_args()
    d = json.loads(Path(a.words).read_text(encoding="utf-8-sig"))
    words = d["words"] if isinstance(d, dict) else d
    caps = build(words, Path(a.copy).read_text(encoding="utf-8-sig"), a.max_chars, a.min_sec)
    if not caps:
        sys.exit("没有生成任何字幕：检查文案和逐词时间")
    Path(a.out).write_text(json.dumps(caps, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"captions": len(caps), "longest_chars": max(vis(c["text"]) for c in caps),
                      "shortest_seconds": round(min(c["end"] - c["start"] for c in caps), 2)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
