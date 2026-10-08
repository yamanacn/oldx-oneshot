"""配音阶段的离线测试（不联网、不花钱）：情绪标签、沿用、核对里查标签被念出来、写文件被占用、旧版本留档、音色历史、--dry-run。
  python tests/voice/test_voice_stage.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "..", "scripts", "voice")
TMP = tempfile.mkdtemp(prefix="voice_test_")
os.environ["DH_WORKSPACE"] = TMP                       # 音色历史、默认音色都写到临时目录
sys.path.insert(0, SCRIPTS)
import common as C          # noqa: E402
import synth as S           # noqa: E402
import verify_audio as V    # noqa: E402
import clone_voice as CV    # noqa: E402

ok = bad = 0


def check(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1
        print("✓", name)
    else:
        bad += 1
        print("✗", name, extra)


E, H, SAD = "[excited]", "[happy]", "[sad]"

# ---------------------------------------------------------------- tag_emotions
t = C.tag_emotions("你好呀。真能看吗？看看我。", [{"at": "你好呀", "tag": E}, {"at": "看看我", "tag": H}, {"at": "不存在的话", "tag": SAD}])
check("tag_emotions：标签加在指定句子的开头，找不到的规则跳过", t == "[excited]你好呀。真能看吗？[happy]看看我。", t)
t = C.tag_emotions(C.tag_questions("真能看吗？好。"), [{"at": "真能看吗", "tag": E}])
check("tag_emotions：那个位置已经有问句标签就不再加", t == "[curious]真能看吗？好。", t)
t = C.tag_emotions("第一句。第二句。第一句。", [{"at": "第一句", "tag": E, "n": 2}])
check("tag_emotions：n 指定第几次出现", t == "第一句。第二句。[excited]第一句。", t)

# ---------------------------------------------------------------- apply_carry
paras = ["开场！我是小义。今天分享这个东西，很厉害。这里是一段信息密集的话，" + "字" * 40 + "。接着又是一大段信息，" + "字" * 40 + "。",
         "这是什么意思？下一句要接回原来的情绪。再来一句。",
         "没有任何规则命中的一段。"]
rules = [{"at": "开场", "tag": E}, {"at": "这是什么意思", "tag": SAD}]
out = C.apply_carry(paras, rules, "auto", E, 80)
check("apply_carry：第一段第一句有标签，段内长段没有标签的地方隔 80 字补沿用的标签",
      out[0].startswith("[excited]开场") and out[0].count("[excited]") >= 2 and out[0].count("[excited]") <= 3, out[0][:80])
check("apply_carry：问句标签后的下一句马上接回原来的情绪（沿用的是上一个情绪标签，不是问句标签）",
      C.apply_carry(["这是什么意思？下一句要接回原来的情绪。"], [{"at": "开场", "tag": E}], "auto", E, 80)[0] == "[curious]这是什么意思？[excited]下一句要接回原来的情绪。", out[1])
check("apply_carry：每段开头必须有标签，没有自己标签的段首用上一段结尾的情绪（这里上一段结尾是 excited）", out[2].startswith("[excited]没有任何规则命中"), out[2])
check("apply_carry：carry=off 不补", C.apply_carry(["普通。"], rules, "off", E)[0] == "普通。")
check("apply_carry：没有规则时整段原样返回（不是所有段都加 excited）", C.apply_carry(["普通。很多句。"], [], "auto", E)[0] == "普通。很多句。")
sen = C.apply_carry(["甲。乙。丙。"], [{"at": "甲", "tag": H}], "sentence", E)[0]
check("apply_carry：sentence 模式每句都补", sen == "[happy]甲。[happy]乙。[happy]丙。", sen)
til = C.apply_carry(["第一句~" + "字" * 90 + "~第三句。"], [{"at": "第一句", "tag": E}], "auto", E, 80)[0]
check("波浪号算句末：补标签会落在波浪号后面的整句开头", "~[excited]" in til, til)
check("tags_not_at_sentence_start：句中的标签被抓出来，句首的不抓",
      len(C.tags_not_at_sentence_start("好。[happy]对。我觉得[sad]不行")) == 1 and C.tags_not_at_sentence_start("[excited]好。[happy]对！") == [])

# ---------------------------------------------------------------- 核对：标签被念出来
check("verify：识别文字里多出一个 Happy 就报出来", V.spoken_tags("以后都能直接用，Happy，老板再也不用", "以后都能直接用，老板再也不用") == [("happy", 1, 0)])
check("verify：原文本来就有的英文词不误报", V.spoken_tags("用 happy 的心情", "用 happy 的心情") == [])
check("verify：emotions.json 里用到的自定义标签名也查", V.spoken_tags("说 gentle 地", "说地") == [("gentle", 1, 0)] and V.spoken_tags("说 whisper 地", "说地", ["whisper"]) == [("whisper", 1, 0)])

# ---------------------------------------------------------------- 写 wav：被占用时不崩
x = (np.sin(np.linspace(0, 200, 4800)) * 8000).astype(np.int16)
dst = os.path.join(TMP, "a.wav")
S.safe_write_wav(dst, x, 24000)
check("safe_write_wav：正常写入", os.path.isfile(dst) and not os.path.isfile(dst + ".tmp"))
real_replace, real_sleep = os.replace, S.time.sleep
calls = []


def locked_replace(a, b):
    calls.append(b)
    if b == dst:
        raise PermissionError(13, "locked")
    return real_replace(a, b)


os.replace, S.time.sleep = locked_replace, lambda s: None
try:
    S.safe_write_wav(dst, x, 24000)
    got = None
except S.Locked as e:
    got = str(e)
finally:
    os.replace, S.time.sleep = real_replace, real_sleep
check("safe_write_wav：目标被占用时重试后另存为 *_新.wav 并抛出 Locked（不是崩溃）", got is not None and os.path.isfile(os.path.join(TMP, "a_新.wav")) and len(calls) >= 6, got)

# ---------------------------------------------------------------- 旧版本留档
out = os.path.join(TMP, "proj", "out")
os.makedirs(out)
for n in ("merged.wav", "words.json", "verify.json"):
    open(os.path.join(out, n), "w").write(n)
json.dump({"fingerprint": "AAA"}, open(os.path.join(out, "timeline.json"), "w"))
check("archive_previous：指纹相同（结果没变）不留档", S.archive_previous(out, "AAA") is None and os.path.isfile(os.path.join(out, "words.json")))
dest = S.archive_previous(out, "BBB")
check("archive_previous：结果变了就存到 out_prev/<时间>/，旧的 words.json 和 verify.json 移走（属于旧音频）",
      dest and os.path.isfile(os.path.join(dest, "merged.wav")) and os.path.isfile(os.path.join(dest, "words.json"))
      and not os.path.isfile(os.path.join(out, "words.json")) and os.path.basename(os.path.dirname(dest)) == "out_prev", dest)

# ---------------------------------------------------------------- 音色历史
os.makedirs(os.path.join(TMP, "projects", "p1"))
os.makedirs(os.path.join(TMP, "projects", "p2"))
v1 = {"voice_id": "voice-aaaa1111", "sample": "C:/x/10月3日.mp3", "sample_seconds": 35.5, "created_at": "2026-10-03 22:50:03"}
v2 = {"voice_id": "voice-bbbb2222", "sample": "C:/x/10月5日.mp3", "sample_seconds": 10.4, "created_at": "2026-10-05 11:15:32"}
json.dump(v1, open(os.path.join(TMP, "projects", "p1", "voice.json"), "w"))
json.dump(v2, open(os.path.join(TMP, "voice.json"), "w"))                          # 当前默认
h = CV.history()
check("音色历史：从默认音色和各项目的 voice.json 里找回所有复刻过的，按时间排序、去重", [r["voice_id"] for r in h] == ["voice-aaaa1111", "voice-bbbb2222"], h)
wd = os.path.join(TMP, "projects", "p2")
r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "clone_voice.py"), "--workdir", wd, "--use", "10月3日"], capture_output=True, text=True, encoding="utf-8", env=dict(os.environ))
check("clone_voice --use：按样本文件名里的字换回旧音色，项目和默认都改过去",
      r.returncode == 0 and json.load(open(os.path.join(wd, "voice.json"), encoding="utf-8"))["voice_id"] == "voice-aaaa1111"
      and json.load(open(os.path.join(TMP, "voice.json"), encoding="utf-8"))["voice_id"] == "voice-aaaa1111", r.stdout + r.stderr)
r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "clone_voice.py"), "--workdir", wd, "--use", "不存在"], capture_output=True, text=True, encoding="utf-8", env=dict(os.environ))
check("clone_voice --use：没有唯一匹配时报错而不是乱选", r.returncode != 0)

# ---------------------------------------------------------------- 整体：--dry-run 不联网、不要密钥
pd = os.path.join(TMP, "proj2")
os.makedirs(pd)
json.dump({"voice_id": "voice-x"}, open(os.path.join(pd, "voice.json"), "w"))
json.dump({"paragraphs": ["开场！很好。你觉得呢？不错。", "第二段有一些内容。"]}, open(os.path.join(pd, "paragraphs.json"), "w", encoding="utf-8"), ensure_ascii=False)
json.dump({"instruction": "测试指令", "tags": [{"at": "开场", "tag": E}, {"at": "不存在", "tag": H}]}, open(os.path.join(pd, "emotions.json"), "w", encoding="utf-8"), ensure_ascii=False)
env = dict(os.environ, HOME=TMP, USERPROFILE=TMP)
r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "synth.py"), "--workdir", pd, "--dry-run"], capture_output=True, text=True, encoding="utf-8", env=env)
check("synth --dry-run：打印带标签的文字、提示没匹配上的规则、不需要密钥、不产生结果文件",
      r.returncode == 0 and "[excited]开场" in r.stdout and "没匹配上" in r.stdout and "[curious]你觉得呢" in r.stdout and "[excited]第二段" in r.stdout
      and not os.path.exists(os.path.join(pd, "out", "merged.wav")), r.stdout[-300:] + r.stderr[-300:])

# ---------------------------------------------------------------- 整体：用假的合成结果跑一遍 main（拼接、留档、变体、被占用）
wd = os.path.join(TMP, "proj3")
os.makedirs(wd)
json.dump({"voice_id": "voice-x"}, open(os.path.join(wd, "voice.json"), "w"))
json.dump({"paragraphs": ["第一段。", "第二段。"]}, open(os.path.join(wd, "paragraphs.json"), "w", encoding="utf-8"), ensure_ascii=False)


def fake_wav(name, freq):
    path = os.path.join(TMP, name)
    S.write_wav(path, (np.sin(np.arange(48000) * freq) * 9000).astype(np.int16), C.SAMPLE_RATE)
    return path


state = {"a": fake_wav("fa.wav", 0.05), "b": fake_wav("fb.wav", 0.07)}
S.synth_text = lambda text, voice, key, cache, label, depth=0: (state["a"] if "第一" in text else state["b"], [], True)
C.require_key = lambda: "k"
C.update_meta = lambda *a, **k: None


def run_main(*extra):
    sys.argv = ["synth.py", "--workdir", wd, "--keep-fillers", *extra]      # 剪语气词要联网识别，这里离线，单独测
    try:
        S.main()
        return 0
    except SystemExit as e:
        return e.code or 0


check("main：用缓存的段拼出 merged.wav 和 timeline.json（带指纹）", run_main() == 0 and os.path.isfile(os.path.join(wd, "out", "merged.wav"))
      and json.load(open(os.path.join(wd, "out", "timeline.json"), encoding="utf-8")).get("fingerprint"))
check("main：结果没变时重跑不留档", not os.path.isdir(os.path.join(wd, "out_prev")) and run_main() == 0 and not os.path.isdir(os.path.join(wd, "out_prev")))
state["a"] = fake_wav("fa2.wav", 0.09)
check("main：结果变了就把上一版存到 out_prev", run_main() == 0 and len(os.listdir(os.path.join(wd, "out_prev"))) == 1)
state["a"] = fake_wav("fa3.wav", 0.11)
check("main：--variant 写到 out_<名字>，不动 out/", run_main("--variant", "试听") == 0 and os.path.isfile(os.path.join(wd, "out_试听", "merged.wav"))
      and json.load(open(os.path.join(wd, "out", "timeline.json"), encoding="utf-8")).get("fingerprint") != json.load(open(os.path.join(wd, "out_试听", "timeline.json"), encoding="utf-8")).get("fingerprint"))
real_replace, real_sleep = os.replace, S.time.sleep
target = os.path.join(wd, "out", "merged.wav")


def locked_replace(a, b):
    if b == target:
        raise PermissionError(13, "locked")
    return real_replace(a, b)


os.replace, S.time.sleep = locked_replace, lambda s: None
code = run_main()
os.replace, S.time.sleep = real_replace, real_sleep
check("main：merged.wav 被占用时退出码 5，新的另存为 merged_新.wav，不是崩溃", code == 5 and os.path.isfile(os.path.join(wd, "out", "merged_新.wav")), code)


# ---- 标签引出来的多余语气词：找出来、剪掉、验证
import fillers as F   # noqa: E402
import asr as ASR     # noqa: E402


def W(word, b, e):
    return {"text": word, "word": word, "begin": b, "end": e}


found, lost = F.find_inserted([W("你好", 0, .3), W("哼", .7, .9), W("再见", 1.0, 1.3)], "你好，再见")
check("语气词：原文没有的“哼”被找出来，原文的字没少", [f["text"] for f in found] == ["哼"] and lost == 0, (found, lost))
found, lost = F.find_inserted([W("哼", 0, .2), W("你好", .3, .6)], "哼！你好")
check("语气词：原文本来就有的“哼”不动", found == [] and lost == 0, found)
found, lost = F.find_inserted([W("你好", 0, .3), W("嘿嘿", .5, 1.0), W("再见", 1.0, 1.3)], "你好再见")
check("语气词：一次多出两个字（嘿嘿）也找得到", [f["text"] for f in found] == ["嘿嘿"], found)
found, lost = F.find_inserted([W("你", 0, .3), W("再见", 1.0, 1.3)], "你好再见")
check("语气词：原文的字被吞掉了会记到 lost（只数整个听不到的字）", found == [] and lost == 1, (found, lost))
found, lost = F.find_inserted([W("你好", 0, .3), W("在见", 1.0, 1.3)], "你好再见")
check("语气词：同音字写错只是识别误差，不算丢字", lost == 0, lost)

sr_ = C.SAMPLE_RATE


def burst(sec, amp=8000, f=220):
    t = np.arange(int(sec * sr_)) / sr_
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.int16)


def quiet(sec):
    return np.zeros(int(sec * sr_), np.int16)


sig = np.concatenate([burst(.3), quiet(.4), burst(.2, 9000, 330), quiet(.12), burst(.3)])   # 你好 / 静音 / 哼 / 静音 / 再见
cd = os.path.join(TMP, "fcache"); os.makedirs(cd)
raw_wav = os.path.join(cd, "raw1.wav"); S.write_wav(raw_wav, sig, sr_)
calls = []


def _tone(x):
    spec = np.abs(np.fft.rfft(x.astype(np.float32)))
    return float(spec[int(330 * len(x) / sr_) - 3:int(330 * len(x) / sr_) + 4].max())


def fake_asr(path, key, cache_dir=None):
    x, _ = S.read_wav(path); calls.append(len(x))
    w = [W("你好", 0, .3), W("再见", len(x) / sr_ - .3, len(x) / sr_)]
    if _tone(x) > .3 * _tone(sig):               # 330Hz 的那一声还在：识别会听到“哼”
        w.insert(1, W("哼", .7, .9))
    return {"text": "".join(a["word"] for a in w), "words": w}


real_tr, ASR.transcribe = ASR.transcribe, fake_asr
try:
    newp, notes = F.clean(raw_wav, "你好，再见", "k", cd, "段1", S.write_wav, S.read_wav)
    y, _ = S.read_wav(newp)
    check("语气词：clean 剪掉多余的一声，换成新文件", newp != raw_wav and notes and "已剪掉" in notes[0], (newp, notes))
    check("语气词：剪完变短，两边的字都还在", len(y) < len(sig) - int(.15 * sr_) and np.abs(y[:int(.25 * sr_)]).max() > 5000 and np.abs(y[-int(.25 * sr_):]).max() > 5000, len(y))
    check("语气词：前后停顿合计不超过 0.5 秒左右", len(y) / sr_ < .3 + F.GAP_CAP + .3 + .05, len(y) / sr_)
    n0 = len(calls)
    again = F.clean(raw_wav, "你好，再见", "k", cd, "段1", S.write_wav, S.read_wav)
    check("语气词：重跑直接用记录，不再联网识别", len(calls) == n0 and again[0] == newp)
    clean_sig = np.concatenate([burst(.3), quiet(.4), burst(.3)])
    p2 = os.path.join(cd, "raw2.wav"); S.write_wav(p2, clean_sig, sr_)
    r2 = F.clean(p2, "你好，再见", "k", cd, "段2", S.write_wav, S.read_wav)
    check("语气词：没有多余语气词的段原样返回", r2 == (p2, []), r2)

    def stubborn(path, key, cache_dir=None):          # 怎么剪都还听到“哼”：不能乱剪，要保留原样并报告
        return {"text": "你好哼再见", "words": [W("你好", 0, .3), W("哼", .7, .9), W("再见", 1.0, 1.3)]}

    ASR.transcribe = stubborn
    p3 = os.path.join(cd, "raw3.wav"); S.write_wav(p3, sig, sr_)
    r3 = F.clean(p3, "你好，再见", "k", cd, "段3", S.write_wav, S.read_wav)
    check("语气词：剪不干净就保留原样并说明", r3[0] == p3 and r3[1] and "没剪干净" in r3[1][0], r3)
finally:
    ASR.transcribe = real_tr

shutil.rmtree(TMP, ignore_errors=True)
print("\n通过 %d/%d" % (ok, ok + bad))
sys.exit(1 if bad else 0)
