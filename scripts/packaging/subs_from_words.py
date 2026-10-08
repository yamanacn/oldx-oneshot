"""把逐词时间变成字幕叠加项（可直接放进 pip_render 的 spec["overlays"]）。

用法：
  python subs_from_words.py words.json --out subs.json
  python subs_from_words.py words.json --copy copy.txt --out subs.json     # 用原文案纠正识别出的错别字
  python subs_from_words.py words.json --out subs.json --pip-safe          # 字幕左移收窄，避开右下角圆形小窗

输入 words.json：{"words": [{"text" 或 "word": "...", "start": 秒, "end": 秒}, ...]}，或直接是这个列表。
配音阶段产出的 out/words.json 就是这个格式；别的语音识别结果转成这个结构即可。
分行规则：在标点或 ≥0.35 秒的停顿处断行，每行不超过 --max-chars 个字；每行显示到下一行开始或本行说完后 0.3 秒。
识别文字会有同音错字：给了 --copy 就按原文案逐字对齐替换；没给就要人工校对 subs.json 再用。
"""
import argparse
import difflib
import json
import re
import sys
from pathlib import Path

import _common  # noqa: F401  统一 stdout 编码

PUNCT = "，。、！？!?；;：:,.…—"
STRIP = re.compile(r"[\s，。、！？!?；;：:“”\"'（）()\-—…,.]")


def load_words(path):
    d = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    ws = d["words"] if isinstance(d, dict) else d
    out = []
    for w in ws:
        raw = w.get("text") or w.get("word") or ""
        out.append({"raw": raw, "chars": STRIP.sub("", raw), "start": float(w["start"]), "end": float(w["end"]),
                    "brk": bool(raw.strip()) and raw.strip()[-1] in PUNCT})
    return [w for w in out if w["chars"]]


def correct(words, copy_text):
    """把识别出的字按原文案逐字对齐替换（只替换等长的差异，长度对不上的保持识别结果）。"""
    heard = "".join(w["chars"] for w in words)
    want = STRIP.sub("", copy_text)
    fixed = list(heard)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, heard, want, autojunk=False).get_opcodes():
        if tag == "replace" and i2 - i1 == j2 - j1:
            fixed[i1:i2] = want[j1:j2]
    k = 0
    for w in words:
        n = len(w["chars"])
        w["chars"] = "".join(fixed[k:k + n])
        k += n
    return sum(a != b for a, b in zip(heard, fixed))


def width(text):
    """显示宽度：英文字母和数字按半个字算。"""
    return sum(0.5 if ord(ch) < 128 else 1.0 for ch in text)


def group(words, max_chars, pause):
    lines, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        n = sum(width(x["chars"]) for x in cur)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = nxt["start"] - w["end"] if nxt else 9.9
        too_long = nxt is not None and n + width(nxt["chars"]) > max_chars
        if nxt is None or too_long or gap >= pause or (w["brk"] and n >= 4):
            lines.append(cur)
            cur = []
    return lines


def main():
    ap = argparse.ArgumentParser(description="逐词时间 -> 字幕叠加项")
    ap.add_argument("words")
    ap.add_argument("--out", required=True)
    ap.add_argument("--copy", help="原文案文本文件，用来纠正识别出的同音错字")
    ap.add_argument("--max-chars", type=int, default=16)
    ap.add_argument("--pause", type=float, default=0.35)
    ap.add_argument("--size", type=int, default=46)
    ap.add_argument("--y", type=float, default=0.9, help="字幕中心的纵向位置（占画面高度的比例）")
    ap.add_argument("--pip-safe", action="store_true", help="避开右下角圆形小窗")
    ap.add_argument("--offset", type=float, default=0.0, help="整体平移（秒），words 的零点与成片不同时用")
    a = ap.parse_args()

    words = load_words(a.words)
    if not words:
        sys.exit("words.json 里没有可用的词")
    changed = correct(words, Path(a.copy).read_text(encoding="utf-8-sig")) if a.copy else None
    lines = group(words, a.max_chars, a.pause)
    x, maxw = (0.4, 0.62) if a.pip_safe else (0.5, 0.8)
    items = []
    for i, ln in enumerate(lines):
        start = ln[0]["start"] + a.offset
        natural_end = ln[-1]["end"] + 0.3 + a.offset
        nxt = lines[i + 1][0]["start"] + a.offset if i + 1 < len(lines) else None
        end = min(natural_end, nxt) if nxt is not None else natural_end
        items.append({"type": "text", "text": "".join(w["chars"] for w in ln), "start": round(start, 3),
                      "end": round(max(end, start + 0.2), 3), "pos": [x, a.y], "size": a.size, "max_width": maxw,
                      "fade": 0.08, "bg": None, "stroke": 5})
    Path(a.out).write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": a.out, "lines": len(items), "corrected_chars": changed,
                      "note": None if a.copy else "没有给 --copy：字幕来自语音识别，可能有同音错字，使用前请校对"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
