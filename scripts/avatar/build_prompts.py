"""组装并校验每段的提示词。

  python build_prompts.py --workdir <项目目录>

输入：<workdir>/avatar/prompt_src.json（照 references/avatar/prompt_src.example.json 写，规则见 references/avatar/prompt-rules.md）：
  {"image_type": "图片类型 + 附加条件，例如 B+持物右+非中性（类型 A 近景 / B 半身无桌面 / C 半身有桌面 / D 站姿 / E 全身远景；
                  附加条件：持物左、持物右 = 画面哪一侧的手拿着东西，旁物 = 东西摆在旁边，非中性 = 图里嘴张开或手抬在半空）",
   "hands": "双手的原姿态", "posture": "身体的原姿态（可不写）",
   "lines": ["第1段的台词原文", ...],                 # 只用来核对分段，不放进提示词
   "motions": ["第1段的动作：一整段中文，从 <Picture 1> 的姿态写起，最后回到 <Picture 1> 的姿态", ...]}
同目录的 plan.json 提供每段时长和识别文字。
输出：<workdir>/avatar/prompts/segment_NN.txt = 第一行 <固定镜头，无运镜> + 双手（和身体）的原姿态 + 动作。

写法只有一种：中文连续叙述。以前的“节拍列表”和“英文连续叙述”两种写法已经删掉（2026-10-14）：节拍里的关键词和时间段
是烧字幕、镜头拉远的嫌疑，英文和中文效果一样。旧项目的 prompt_src.json 是这两种写法时会报错，按 prompt-rules.md 改写成中文叙述。

校验（不通过就退出码 1，并说明哪一段哪一条）：
  * 必须先填 image_type；hands 里要写两只手（或统一写“双手”；近景 A 不要求）；
  * 动作是一整段中文：不夹英文、不写时间点和秒数、不用引号、不写嘴和表情、不用比喻、不写镜头运动或前后方向的动作、
    不写具体手指数、不碰脸；开头和结尾各提一次 <Picture 1>；
  * 台词与本段识别文字相似度 ≥0.9（核对分段用）；每段不超过 2000 字。
  * 双手交叉、往中间靠拢、举过肩，叙述偏长偏短：只给提示。
"""
import argparse
import difflib
import json
import os
import re
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _cli  # noqa: E402

BEAT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[–—\-~～]\s*(\d+(?:\.\d+)?)\s*(?:「([^」]*)」)?\s*[：:]?\s*(.+?)\s*$")
FORBID = [
    # 只拦“比数字”的写法；“五指张开”“手指并拢”是在描述手形，不拦
    (r"[一二三四五六七八九十两\d]\s*根\s*(?:手)?指|(?:伸出|比出|竖起|举起|伸|比)\s*[一二三四五六七八九十两\d]\s*(?:个)?\s*(?:手)?指|比\s*(?:个|出)\s*[一二三四五六七八九十\d]",
     "具体手指数（数字手势多次出错，改用手掌或食指示意）"),
    (r"推近|拉远|变焦|摇镜|运镜|跟拍|转身|站起|起身|走动|凑近|靠近镜头|特写|放大画面|缩小画面", "镜头运动或大幅位移（推近拉远一律留给后期做）"),
    (r"前倾|后仰|后靠|向后.{0,4}靠|往后.{0,4}靠|探身|探出|身体向前|上身向前|伸向镜头|向镜头", "身体或手朝镜头前后移动：模型会把它做成推近拉远，画面和背景跟着出错；身体只写耸肩、侧倾、左右轻晃"),
    (r"摸|触碰|扶正|摘下|整理|挠|揉|托腮", "触碰脸部或佩戴物"),
    (r"像|仿佛|好似|如同|似的", "比喻（“像拿着话筒”“像捧起东西”）：模型会把比喻里的东西画出来，直接写手怎么动"),
    (r"看向手|低头看|看着手|目光.{0,4}(移开|离开|下移|转向)|视线.{0,4}(移开|离开|下移|转向)|看向别处|看向画面外", "视线离开镜头"),
]
# 与嘴部、表情有关的词一律不写（画面描述、双手描述、动作编排都检查；台词原文不检查）
MOUTH = re.compile(r"嘴|唇|口型|牙|笑|抿|咧|撇|开口|张口|闭合|换气|表情|神态|露出|亲切|惊喜|遗憾|无奈|皱眉|开心|高兴|愉快|满意|自信|明亮|热情")
LEFT, RIGHT = "画面左侧的手", "画面右侧的手"
MAX_CHARS = 2000
SHOTS = {"A": "近景", "B": "半身无桌面", "C": "半身有桌面", "D": "站姿", "E": "全身远景"}
FLAGS = {"持物左", "持物右", "旁物", "非中性"}


# 连续中文叙述（motion_style = narrative_zh）：动作是一整段中文，从首帧状态讲到回到首帧状态
ZH_HINT = [
    (r"交叉|叠在|叠放|重叠|十指相扣|合十|靠拢|向中间|往中间|收拢", "双手交叉、叠放或往中间靠拢：这类动作多次引出多手"),
    (r"举过头|高举|举高|抬高|过头顶|超过下巴", "手抬得过高：多次引出多手，写到肩膀高度为止"),
]


def check_narrative_zh(tag, dur, text):
    """返回 (problems, hints)。固定镜头那一行由 prompt_prefix 加，叙述里不写。"""
    probs, hints = [], []
    body = text.replace("<Picture 1>", "")
    if re.search(r"[A-Za-z]", body):
        probs.append("%s：叙述整段用中文写，出现了英文字母（%s）" % (tag, re.search(r"[A-Za-z]+", body).group()))
    if re.search(r"[\"“”「」『』]", text):
        probs.append("%s：叙述里不用引号：引号里的字可能被当成画面上的文字画出来" % tag)
    m = re.search(r"\d+(\.\d+)?\s*(秒|s\b)|\d+\.\d+\s*[–—\-~]|\b\d{1,2}:\d{2}\b", text)
    if m:
        probs.append("%s：叙述里不写时间点和秒数，只按先后顺序写（%s）" % (tag, m.group()))
    m = MOUTH.search(text)
    if m:
        probs.append("%s：叙述里出现与嘴部或表情有关的词「%s」，一律不写" % (tag, m.group()))
    for pat, why in FORBID:
        mm = re.search(pat, text)
        if mm:
            probs.append("%s：叙述里出现%s（%s）" % (tag, why, mm.group()))
    if text.count("<Picture 1>") < 2 or "<Picture 1>" not in text[:40] or "<Picture 1>" not in text[-30:]:
        probs.append("%s：叙述要从 <Picture 1> 的姿态写起，最后一句写明回到 <Picture 1> 的姿态（开头和结尾各提一次）" % tag)
    for pat, why in ZH_HINT:
        mm = re.search(pat, text)
        if mm:
            hints.append("%s：%s（%s）" % (tag, why, mm.group()))
    n = len(re.sub(r"[\s，。；：、！？<>]|<Picture 1>", "", text))
    if n / max(dur, 0.1) > 16:
        hints.append("%s：叙述偏长（%d 字，%.1f 秒），动作可能挤在一起" % (tag, n, dur))
    if n / max(dur, 0.1) < 6:
        hints.append("%s：叙述偏短（%d 字，%.1f 秒），后半段可能没有动作" % (tag, n, dur))
    return probs, hints


def parse_image_type(v):
    """'B+持物右+非中性' -> ('B', {'持物右', '非中性'})；写错了返回 (None, 说明)"""
    parts = [x.strip() for x in re.split(r"[+＋,，、\s]+", str(v or "")) if x.strip()]
    if not parts or parts[0].upper() not in SHOTS:
        return None, "image_type：先判断图片类型再写动作。写成「类型+附加条件」，例如 B+持物右+非中性。类型：%s；附加条件：%s" % (
            "、".join("%s %s" % kv for kv in SHOTS.items()), "、".join(sorted(FLAGS)))
    bad = [x for x in parts[1:] if x not in FLAGS]
    if bad:
        return None, "image_type：不认识的附加条件 %s，只能用 %s" % ("、".join(bad), "、".join(sorted(FLAGS)))
    return parts[0].upper(), set(parts[1:])


_CN = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNIT = {"十": 10, "百": 100, "千": 1000}


def _cn_to_int(s):
    if not any(ch in _UNIT for ch in s):          # 没有十百千：逐位读（年份"一九五六"→1956）
        return int("".join(str(_CN[ch]) for ch in s))
    total, cur = 0, 0
    for ch in s:
        if ch in _CN:
            cur = _CN[ch]
        elif ch in _UNIT:
            total += (cur or 1) * _UNIT[ch]
            cur = 0
    return total + cur


def norm(s):
    """去标点空白，并把中文数字统一成阿拉伯数字（识别结果常把"三十"写成"30"）。"""
    s = re.sub(r"[零〇一二两三四五六七八九十百千]+", lambda m: str(_cn_to_int(m.group())), s)
    return re.sub(r"[\s，。、！？!?；;：:“”\"'（）()\-—…,.]", "", s).lower()


DEFAULT_PREFIX = "<固定镜头，无运镜>"


def apply_defaults(src):
    """补上没写的开关：第一行固定镜头标签、不放画面描述、不放台词。旧写法（节拍列表、英文叙述）直接报错，说明怎么改。"""
    style = src.get("motion_style") or "narrative_zh"
    mot = [str(m).strip() for m in src.get("motions", []) if str(m).strip()]
    if style != "narrative_zh" or (mot and all(BEAT.match(m.splitlines()[0]) for m in mot)):
        _cli.report(next="按 references/avatar/prompt-rules.md 第四节，把 motions 改写成中文连续叙述，再运行 build_prompts.py")
        _cli.fail(_cli.ERROR, "prompt_src.json 用的是旧写法（%s），已经不支持。把每段的 motions 改写成一整段中文叙述："
                  "从 <Picture 1> 的姿态写起，按先后顺序写手、头、身体怎么动，最后回到 <Picture 1> 的姿态；不写时间、不写关键词。"
                  % ("英文叙述" if style == "narrative" else "节拍列表"))
    src["motion_style"] = "narrative_zh"
    src.setdefault("prompt_prefix", DEFAULT_PREFIX)
    src.setdefault("include_scene", False)
    src.setdefault("include_line", False)
    return src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    a = ap.parse_args()
    od = os.path.join(a.workdir, "avatar")
    plan = json.load(open(os.path.join(od, "plan.json"), encoding="utf-8"))
    src = json.load(open(os.path.join(od, "prompt_src.json"), encoding="utf-8"))
    apply_defaults(src)
    segs = plan["segments"]
    problems, hints = [], []
    scene, hands = src.get("scene", "").strip(), src.get("hands", "").strip()
    posture = src.get("posture", "").strip()
    shot, flags = parse_image_type(src.get("image_type"))
    if shot is None:
        if "head" not in src:                                 # 旧格式另有专门的报错
            problems.append(flags)
        shot, flags = "C", set()
    if "head" in src and not scene:
        sys.exit("prompt_src.json 是旧格式（head）。现在改为 scene（画面描述）和 hands（双手的原姿态）两项，见 references/avatar/prompt-rules.md")
    if not scene and src.get("include_scene", True):
        problems.append("scene：缺少画面描述（人物的发型、衣着、佩戴物，位置和姿态，背景左右两侧）")
    if shot != "A" and not ((LEFT in hands and RIGHT in hands) or "双手" in hands):
        problems.append("hands：要分别描述「%s」和「%s」在图中的位置和姿态（两只手姿态相同时可以统一写成「双手……」）" % (LEFT, RIGHT))
    for name, txt in (("scene", scene), ("hands", hands), ("posture", posture)):
        m_ = MOUTH.search(txt)
        if m_:
            problems.append("%s：出现与嘴部或表情有关的词「%s」，一律不写" % (name, m_.group()))
    if not (len(src.get("lines", [])) == len(src.get("motions", [])) == len(segs)):
        sys.exit("lines / motions 的数量（%d / %d）必须和分段数 %d 一致" % (len(src.get("lines", [])), len(src.get("motions", [])), len(segs)))
    outs = []
    for s, line, motion in zip(segs, src["lines"], src["motions"]):
        tag = "段%d" % s["i"]
        dur = s["duration"]
        sim = difflib.SequenceMatcher(None, norm(line), norm(s["text_asr"])).ratio()
        if sim < 0.9:
            problems.append("%s：台词与本段识别文字相似度 %.2f（<0.9），请核对台词是不是这一段的内容" % (tag, sim))
        prefix = src.get("prompt_prefix", "").strip()           # 例如「<固定镜头，无运镜>」：放在每段提示词的第一行，由脚本加，不参与动作叙述的检查
        text = " ".join(motion.split())                         # 中文连续叙述：不分节拍、不写时间
        pr, hi = check_narrative_zh(tag, dur, text)
        problems.extend(pr)
        hints.extend(hi)
        head = (["画面描述：%s" % scene] if src.get("include_scene", True) else []) + ["双手的原姿态：%s" % hands] + (["身体的原姿态：%s" % posture] if posture else [])
        parts = ([prefix] if prefix else []) + ["\n".join(head)]
        if src.get("include_line", True):
            parts.append("本段台词（时长约%.1f秒）：%s" % (dur, line))
        parts.append("动作：" + text)
        full = "\n\n".join(parts)
        if len(full) > MAX_CHARS:
            problems.append("%s：提示词 %d 字，超过 %d 字" % (tag, len(full), MAX_CHARS))
        outs.append((s["i"], full))
    print("图片类型：%s %s%s" % (shot, SHOTS[shot], "，附加条件：" + "、".join(sorted(flags)) if flags else ""))
    print("写法：中文连续叙述；第一行：%s；画面描述：%s；台词：%s" % (src.get("prompt_prefix") or "无", "放" if src.get("include_scene", True) else "不放", "放" if src.get("include_line", True) else "不放"))
    for h in hints:
        print("提示：" + h)
    if problems:
        print("校验未通过：")
        for p in problems:
            print(" ✗", p)
        _cli.report(passed=False, problems=problems, hints=hints, style=src["motion_style"])
        sys.exit(_cli.ERROR)
    pd = os.path.join(od, "prompts")
    os.makedirs(pd, exist_ok=True)
    for i, full in outs:
        open(os.path.join(pd, "segment_%02d.txt" % i), "w", encoding="utf-8").write(full)
        print("segment_%02d.txt  %d 字" % (i, len(full)))
    print("校验通过，提示词已写入", pd)
    _cli.report(passed=True, prompts_dir=pd, segments=len(outs), hints=hints, style=src["motion_style"],
                image_type=shot + ("+" + "+".join(sorted(flags)) if flags else ""),
                prompt_prefix=src.get("prompt_prefix") or "", include_scene=src.get("include_scene", True), include_line=src.get("include_line", True))


if __name__ == "__main__":
    _cli.run(main)
