"""build_prompts.py 的校验规则测试：好的输入通过，坏的输入被对应规则拦下（不联网）。  python tests/avatar/test_build_prompts.py
写法只有中文连续叙述一种；旧的节拍列表、英文叙述会被拦下并说明怎么改。"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "..", "scripts", "avatar", "build_prompts.py")
L, R = "画面左侧的手", "画面右侧的手"
SCENE = "齐肩黑色短发，黑色圆领T恤；女子居中，中近景；背景是紫红色的虚化空间。"
HANDS = "%s抬在胸前左侧，掌心向前；%s抬在胸前右侧，掌心向前。" % (L, R)
LINE = "大家好，今天聊聊人工智能的发展史。"
ZH = ("从<Picture 1>的姿态开始，双手抬在胸前两侧；她一只手用力向下一压，头跟着一点；接着双手放低到胸口，上身明显向画面左侧侧倾，再慢慢回正；"
      "然后一只手轻点两下，头和上身回到端正；最后双手回到胸前两侧，回到<Picture 1>的姿态。")
cases = []


def run(src):
    d = tempfile.mkdtemp()
    av = os.path.join(d, "avatar")
    os.makedirs(av)
    json.dump({"segments": [{"i": 1, "duration": 6.0, "text_asr": "大家好今天聊聊人工智能的发展史"}]}, open(av + "/plan.json", "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(src, open(av + "/prompt_src.json", "w", encoding="utf-8"), ensure_ascii=False)
    r = subprocess.run([sys.executable, SCRIPT, "--workdir", d], capture_output=True, text=True, encoding="utf-8")
    p = os.path.join(av, "prompts", "segment_01.txt")
    return r.returncode, r.stdout + r.stderr, (open(p, encoding="utf-8").read() if os.path.isfile(p) else "")


def src_of(text=ZH, **kw):
    s = {"image_type": "C", "hands": HANDS, "lines": [LINE], "motions": [text]}
    s.update(kw)
    return {k: v for k, v in s.items() if v is not None}


def check(name, rc_expected, needle, src, look=None):
    rc, out, prompt = run(src)
    ok = rc == rc_expected and (needle is None or needle in out) and (look is None or look(prompt))
    print("✓" if ok else "✗", name)
    cases.append(ok)
    if not ok:
        print(out[-800:])


std = lambda t: t.startswith("<固定镜头，无运镜>\n\n双手的原姿态：") and "画面描述" not in t and "\n\n动作：从<Picture 1>的姿态开始" in t and "台词" not in t
# ---------------------------------------------------------------- 正常写法和默认值
check("中文连续叙述 → 通过；第一行固定镜头标签，不放画面描述和台词", 0, "中文连续叙述", src_of(), std)
check("两只手统一写「双手……」→ 通过", 0, None, src_of(hands="双手抬在胸前两侧，掌心向前。"), std)
check("写了 posture → 提示词里带上身体的原姿态", 0, None, src_of(posture="上身端正，肩膀放松。"), lambda t: "身体的原姿态：上身端正" in t)
check("include_scene=true → 提示词里放画面描述", 0, None, src_of(scene=SCENE, include_scene=True), lambda t: "画面描述：齐肩黑色短发" in t)
check("include_line=true → 提示词里放台词", 0, None, src_of(include_line=True), lambda t: "本段台词（时长约6.0秒）：大家好" in t)
check("prompt_prefix 写成空 → 不加第一行", 0, None, src_of(prompt_prefix=""), lambda t: t.startswith("双手的原姿态"))
check("近景（A）不写手 → 通过", 0, None, src_of(image_type="A", hands="双手不在画面里。"))
check("“五指张开”是手形不是数字手势 → 不拦", 0, None, src_of(ZH.replace("一只手轻点两下", "一只手五指张开轻点两下")))
# ---------------------------------------------------------------- 图片类型、双手、嘴部
check("没填 image_type → 拦下", 1, "先判断图片类型", src_of(image_type=None))
check("image_type 写了不认识的附加条件 → 拦下", 1, "不认识的附加条件", src_of(image_type="C+拿话筒"))
check("image_type 带附加条件 → 通过", 0, "附加条件：持物右", src_of(image_type="B+持物右"))
check("hands 既没分左右也没写双手 → 拦下", 1, "要分别描述", src_of(hands="手抬在胸前。"))
check("hands 里写了表情词 → 拦下", 1, "与嘴部或表情有关的词", src_of(hands=HANDS + "面带笑意。"))
check("posture 里写了神态 → 拦下", 1, "与嘴部或表情有关的词", src_of(posture="神态亲切。"))
check("叙述里写了微笑 → 拦下", 1, "嘴部或表情", src_of(ZH.replace("然后", "然后微笑着")))
check("叙述里写了嘴唇 → 拦下", 1, "嘴部或表情", src_of(ZH.replace("头跟着一点", "头跟着一点，嘴唇闭合")))
# ---------------------------------------------------------------- 叙述的禁忌
check("叙述里夹英文 → 拦下", 1, "出现了英文字母", src_of(ZH.replace("用力", "firmly")))
check("叙述里写了秒数 → 拦下", 1, "不写时间点", src_of(ZH.replace("然后", "在第2秒")))
check("叙述里写了时间段 → 拦下", 1, "不写时间点", src_of(ZH.replace("然后", "2.5–3.0")))
check("叙述里用引号 → 拦下", 1, "不用引号", src_of(ZH.replace("然后", "说到“开源”时")))
check("叙述里写了前倾 → 拦下", 1, "前后移动", src_of(ZH.replace("上身明显向画面左侧侧倾", "上身前倾")))
check("叙述里写了向后一靠 → 拦下", 1, "前后移动", src_of(ZH.replace("上身明显向画面左侧侧倾", "上身向后轻轻一靠")))
check("叙述里写了推近 → 拦下", 1, "镜头运动", src_of(ZH.replace("然后", "镜头推近，然后")))
check("叙述里写了几根手指 → 拦下", 1, "具体手指数", src_of(ZH.replace("一只手轻点两下", "一只手伸出两根手指")))
check("叙述里写了“比个三” → 拦下", 1, "具体手指数", src_of(ZH.replace("一只手轻点两下", "一只手比个三")))
check("叙述里写了比喻 → 拦下", 1, "比喻", src_of(ZH.replace("一只手轻点两下", "一只手像拿着话筒")))
check("叙述里碰脸 → 拦下", 1, "触碰", src_of(ZH.replace("一只手轻点两下", "一只手摸了摸头发")))
check("叙述里视线离开镜头 → 拦下", 1, "视线离开镜头", src_of(ZH.replace("然后", "然后低头看手，")))
check("开头没从 <Picture 1> 写起 → 拦下", 1, "<Picture 1>", src_of(ZH.replace("从<Picture 1>的姿态开始，", "")))
check("结尾没回到 <Picture 1> → 拦下", 1, "回到 <Picture 1>", src_of(ZH.replace("，回到<Picture 1>的姿态", "")))
# ---------------------------------------------------------------- 只提示、不拦
check("双手交叉 → 只提示，不拦", 0, "多次引出多手", src_of(ZH.replace("一只手用力向下一压", "双手交叉")))
check("手举过头 → 只提示，不拦", 0, "抬得过高", src_of(ZH.replace("一只手轻点两下", "一只手举过头")))
check("叙述太短 → 只提示，不拦", 0, "叙述偏短", src_of("从<Picture 1>的姿态开始，双手一压，回到<Picture 1>的姿态。"))
check("叙述太长 → 只提示，不拦", 0, "叙述偏长", src_of(ZH.replace("然后", "然后" + "双手轻轻地向两侧展开再慢慢地收回到胸前" * 4)))
# ---------------------------------------------------------------- 核对分段和格式
check("台词和这一段的识别文字对不上 → 拦下", 1, "相似度", src_of(lines=["完全不相干的另一句话。"]))
check("lines / motions 数量和分段数不一致 → 拦下", 1, "必须和分段数", src_of(lines=[LINE, LINE]))
check("提示词超过 2000 字 → 拦下", 1, "超过 2000 字", src_of(ZH.replace("然后", "然后" + "双手轻轻地向两侧展开" * 220)))
check("旧格式（head）→ 提示改用新格式", 1, "旧格式（head）", {"image_type": "C", "head": "x", "hands": HANDS, "lines": [LINE], "motions": [ZH], "include_scene": True})
# ---------------------------------------------------------------- 旧写法已删除：说明怎么改
BEATS = "\n".join(["0.00–0.40 保持<Picture 1>的原姿态：双手保持轻微的自然晃动。", "0.40–6.00「今天聊聊」：双手缓缓前推，回到原姿态。"])
check("旧的节拍列表写法 → 拦下，说明改写成中文叙述", 1, "改写成一整段中文叙述", src_of(BEATS))
check("motion_style=narrative（英文叙述）→ 拦下", 1, "英文叙述", src_of("From <Picture 1> she raises one hand and returns to the pose of <Picture 1>.", motion_style="narrative"))
check("motion_style=beats → 拦下", 1, "节拍列表", src_of(motion_style="beats"))

print("\n通过 %d/%d" % (sum(cases), len(cases)))
sys.exit(0 if all(cases) else 1)
