"""build_prompts.py 的校验规则测试：好的输入通过，坏的输入被对应规则拦下（不联网）。  python tests/test_build_prompts.py"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "..", "scripts", "avatar", "build_prompts.py")
L, R = "画面左侧的手", "画面右侧的手"
SCENE = "齐肩黑色短发，黑色圆领T恤；女子居中，中近景；背景是紫红色的虚化空间。"
HANDS = "%s抬在胸前左侧，掌心向前；%s抬在胸前右侧，掌心向前。" % (L, R)
LINE = "大家好，今天聊聊人工智能的发展史。"
GOOD = "\n".join([
    "0.00–0.40 保持<Picture 1>的原姿态：%s和%s抬在胸前两侧，保持轻微的自然晃动。" % (L, R),
    "0.40–2.50「今天聊聊」：%s掌心向外缓缓前推，%s同时轻轻上下摆动，点头一次。" % (L, R),
    "2.50–3.20 双手继续轻轻晃动，手指放松。",
    "3.20–5.00「发展史」：%s轻轻下压，%s掌心向上缓缓托起。" % (L, R),
    "5.00–6.00 回到原姿态附近：%s和%s回到抬起的位置，仍保持轻微的自然晃动。" % (L, R)])


def run(scene=SCENE, hands=HANDS, motion=GOOD, src=None):
    d = tempfile.mkdtemp(); av = os.path.join(d, "avatar"); os.makedirs(av)
    json.dump({"segments": [{"i": 1, "duration": 6.0, "text_asr": "大家好今天聊聊人工智能的发展史"}]}, open(av + "/plan.json", "w", encoding="utf-8"), ensure_ascii=False)
    src = src or {"scene": scene, "hands": hands, "lines": [LINE], "motions": [motion]}
    json.dump(src, open(av + "/prompt_src.json", "w", encoding="utf-8"), ensure_ascii=False)
    r = subprocess.run([sys.executable, SCRIPT, "--workdir", d], capture_output=True, text=True, encoding="utf-8")
    return r.returncode, r.stdout + r.stderr, d


cases = []
def check(name, rc_expected, needle, **kw):
    rc, out, d = run(**kw)
    ok = (rc == rc_expected) and (needle is None or needle in out)
    extra = ""
    if rc_expected == 0 and ok:
        t = open(os.path.join(d, "avatar", "prompts", "segment_01.txt"), encoding="utf-8").read()
        parts_ok = all(k in t for k in ("画面描述：", "双手的原姿态：", "本段台词", "动作编排")) and t.rstrip().endswith("仍保持轻微的自然晃动。")
        ok = ok and parts_ok
        extra = "（成品提示词 %d 字，只含四项内容=%s）" % (len(t), parts_ok)
    print("✓" if ok else "✗", name, extra)
    cases.append(ok)
    if not ok: print(out)

check("正确的输入通过，输出只有画面描述、双手、台词、动作编排", 0, None)
check("scene 里写了微笑 → 拦下", 1, "与嘴部或表情有关的词", scene=SCENE + "神态亲切，带着笑意。")
check("hands 里写了表情词 → 拦下", 1, "与嘴部或表情有关的词", hands=HANDS + "面带笑意。")
check("节拍里写了嘴唇闭合 → 拦下", 1, "与嘴部或表情有关的词「嘴」", motion=GOOD.replace("点头一次", "点头一次，嘴唇自然闭合"))
check("节拍里写了换气 → 拦下", 1, "「换气」", motion=GOOD.replace("手指放松。", "手指放松，换气。"))
# 两只手的写法只给提示、不拦截：云端模型不会逐字照做，没必要卡死
check("只写一只手 → 只提示，不拦", 0, "没有写两只手", motion=GOOD.replace("2.50–3.20 双手继续轻轻晃动，手指放松。", "2.50–3.20 一只手轻轻晃动。"))
check("写了“另一只手停在原位” → 只提示，不拦", 0, "停在半空", motion=GOOD.replace("%s同时轻轻上下摆动，点头一次" % R, "%s停在原位，点头一次" % R))
check("左右分开写的节拍不足一半 → 只提示，不拦", 0, "分别写了左右两只手", motion="\n".join([
    "0.00–0.40 保持<Picture 1>的原姿态：双手保持轻微的自然晃动。",
    "0.40–2.50「今天聊聊」：双手缓缓前推，点头一次。",
    "2.50–3.20 双手继续轻轻晃动，手指放松。",
    "3.20–5.00「发展史」：双手缓缓托起。",
    "5.00–6.00 回到原姿态附近：双手回到抬起的位置，仍保持轻微的自然晃动。"]))
check("首尾节拍没写晃动 → 只提示，不拦", 0, "仍在轻微晃动", motion=GOOD.replace("，保持轻微的自然晃动。", "。", 1))
check("hands 没分别描述左右手 → 拦下", 1, "要分别描述", hands="双手抬在胸前。")
check("没有写 scene → 拦下", 1, "缺少画面描述", scene="")
check("写了几根手指 → 拦下", 1, "具体手指数", motion=GOOD.replace("点头一次", "伸出三根手指，点头一次"))
check("写了“像拿着话筒”这类比喻 → 拦下", 1, "比喻", motion=GOOD.replace("点头一次", "握拳像拿着话筒，点头一次"))
check("写了“上身微微前倾” → 拦下（模型会做成推近）", 1, "前后移动", motion=GOOD.replace("点头一次", "上身微微前倾"))
check("写了“向后轻轻一靠” → 拦下", 1, "前后移动", motion=GOOD.replace("点头一次", "上身向后轻轻一靠"))
check("写了“画面推近” → 拦下", 1, "镜头运动", motion=GOOD.replace("点头一次", "画面缓缓推近"))
check("旧格式（head）→ 提示改用新格式", 1, "旧格式", src={"head": "旧的开头", "lines": [LINE], "motions": [GOOD]})
# 手的动作够不够丰富：副手有自己的动作、两只手轮流当主角时不该有提示；副手只有弱动作时提示
RICH = "\n".join([
    "0.00–0.40 保持<Picture 1>的原姿态：%s和%s抬在胸前两侧，保持轻微的自然晃动。" % (L, R),
    "0.40–1.60「大家好」：%s向外挥动，%s落到桌面上轻点一下。" % (L, R),
    "1.60–3.20「今天聊聊」：%s掌心向上向前托出，%s收到身前握拳。" % (R, L),
    "3.20–5.00「发展史」：%s在桌面上轻拍一下再抬起，%s向外侧展开。" % (R, L),
    "5.00–6.00 回到原姿态附近：%s和%s回到抬起的位置，仍保持轻微的自然晃动。" % (L, R)])
WEAK = "\n".join([
    "0.00–0.40 保持<Picture 1>的原姿态：%s和%s抬在胸前两侧，保持轻微的自然晃动。" % (L, R),
    "0.40–1.60「大家好」：%s向外挥动，%s同时轻轻摆动。" % (L, R),
    "1.60–3.20「今天聊聊」：%s掌心向上向前托出，%s同时手指放松地一张一合。" % (L, R),
    "3.20–5.00「发展史」：%s轻轻下压，%s同时轻轻晃动。" % (L, R),
    "5.00–6.00 回到原姿态附近：%s和%s回到抬起的位置，仍保持轻微的自然晃动。" % (L, R)])
rc, out, _ = run(motion=RICH)
ok = rc == 0 and "弱动作" not in out and "主角" not in out
print("✓" if ok else "✗", "两只手都有自己的动作 → 通过，且没有丰富度提示")
cases.append(ok)
if not ok: print(out)
check("副手只有弱动作 → 提示给它一件自己的事，不拦", 0, "弱动作", motion=WEAK)
check("整段只写了手 → 提示加身体动作，不拦", 0, "没写身体", motion=RICH)
rc, out, d = run(motion=RICH.replace("收到身前握拳。", "收到身前握拳，耸一下肩。"), src=None)
ok = rc == 0 and "没写身体" not in out
print("✓" if ok else "✗", "写了耸肩 → 没有身体提示")
cases.append(ok)
rc, out, d = run(src={"scene": SCENE, "hands": HANDS, "posture": "上身正对镜头坐直，双肩水平。", "lines": [LINE], "motions": [GOOD]})
ok = rc == 0 and "身体的原姿态：上身正对镜头" in open(os.path.join(d, "avatar", "prompts", "segment_01.txt"), encoding="utf-8").read()
print("✓" if ok else "✗", "写了 posture → 提示词里带上身体的原姿态")
cases.append(ok)
print("\n通过 %d/%d" % (sum(cases), len(cases)))
sys.exit(0 if all(cases) else 1)
