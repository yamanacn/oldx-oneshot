"""配音阶段的公共部分：固定参数、密钥查找、HTTP、ffmpeg。

密钥只在内存里使用：任何地方都不打印、不写日志，只报告它的"来源"。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------- 固定参数（用户已定，不再询问）
MODEL = "qwen-audio-3.1-tts-flash"
INSTRUCTION = "像朋友聊天一样自然随意的口语，不要播音腔，语速较快，句与句之间保留自然的停顿，语气轻松带笑意"
SAMPLE_RATE = 48000
MAX_CHARS = 1000         # 每段文字上限（实测：整篇 1551 字一次合成成功 3 次，488、501 字的段也稳；再长没测过）
WORKERS = 6              # 最高并发
SUBMIT_INTERVAL = 0.5    # 相邻两次提交的最小间隔（秒）。瞬间同时提交会被 429 限流，错开即可
GAP_SECONDS = 0.3        # 段与段之间的停顿
QUESTION_TAG = "[curious]"   # 问句（以问号结尾的句子）前加的情绪标签，只影响这一句；设为 "" 则不加。其他情绪标签一律不用
MAX_TRIES = 6

ASR_MODEL = "qwen-audio-3.1-asr-flash"       # 逐词时间戳 + 文字核对，都用它（不依赖本地识别工具）
ASR_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

# 项目缓存根目录：每份口播文案一个目录，配音、逐词时间、数字人等后续环节共用
WORKSPACE_ROOT = os.environ.get("DH_WORKSPACE", "").strip() or os.path.join(os.path.expanduser("~"), ".digital-human")

TTS_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
CLONE_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
KEY_PAGE = "https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key"
DEFAULT_KEY_FILE = os.path.join(os.path.expanduser("~"), ".bailian", "api_key")

# 没有密钥时对用户说的话（原样输出）
GUIDE_TEXT = "本机没有注册百炼的APIkey。请获取apikey后，直接粘贴到对话输入框发送给我，其余的我来处理。\n获取地址：" + KEY_PAGE


# ---------------------------------------------------------------- 问句标签
_SENT = re.compile(r"[^。！？!?；;…~～\n]+[。！？!?；;…~～]*")


def tag_questions(text):
    """在每个以问号结尾的句子前加 QUESTION_TAG。只改发给合成接口的文字，不改文案和缓存里的原文。"""
    if not QUESTION_TAG:
        return text

    def f(m):
        s = m.group(0)
        if not re.search(r"[？?]", s[-3:]):
            return s
        lead = len(s) - len(s.lstrip())
        return s[:lead] + QUESTION_TAG + s[lead:]
    return _SENT.sub(f, text)


def tag_emotions(text, rules):
    """按项目里 emotions.json 的规则，在指定的句子或分句前加情绪标签（如 [excited]）。
    rules: [{"at": "这句话开头的几个字", "tag": "[excited]", "n": 第几次出现（默认 1）}]。
    只改发给合成接口的文字；找不到 at 的规则跳过（这一段里没有这句话）。那个位置已经有问句标签时不再加。"""
    spots = []
    for r in rules or []:
        at, tag, n = r.get("at", ""), r.get("tag", ""), int(r.get("n", 1))
        if not at or not tag:
            continue
        i = -1
        for _ in range(n):
            i = text.find(at, i + 1)
            if i < 0:
                break
        if i < 0 or (QUESTION_TAG and text[max(0, i - len(QUESTION_TAG)):i] == QUESTION_TAG):
            continue
        spots.append((i, tag))
    for i, tag in sorted(spots, reverse=True):
        text = text[:i] + tag + text[i:]
    return text


def apply_carry(paras, rules, carry="auto", start_tag="[excited]", gap=80):
    """给每一段加好所有标签，返回和 paras 等长的列表。
    先按 rules 在指定的句子前加情绪标签、给问句加问句标签；然后“沿用”：没有自己标签的句子，沿用前面最近一个情绪标签，
    这样信息密集的地方（报档位、念清单）和段与段的交界都不会出现情绪突变。第一句之前没有标签时用 start_tag。
    carry: "auto"（默认）段首必补，段内某个句子没有标签、而且离上一个标签已经超过 gap 个字时才补，既不留长长一截没有标签的话，也不让标签太碎；
           "sentence" 每个没标签的句子都补（标签最密）；"paragraph" 只在段首补；"off" 不补。问句标签不算情绪，也不会被沿用；问句后面的那一句总是补沿用的情绪标签。
    注意每一段是单独一次合成请求，标签不会跨段生效，所以段首的沿用是必须的。"""
    out, cur = [], start_tag
    for p in paras:
        tagged = tag_emotions(tag_questions(p), rules)
        parts = _SENT.findall(tagged)
        if not rules or carry == "off" or "".join(parts) != tagged:
            out.append(tagged)
            continue
        res, since, after_q = [], 0, False
        for k, sent in enumerate(parts):
            body = sent.lstrip()
            m = re.match(r"\[[a-z_]+\]", body)
            if m:
                after_q = m.group(0) == QUESTION_TAG
                if not after_q:
                    cur = m.group(0)
                res.append(sent)
                since = len(body)
            elif k == 0 or after_q or carry == "sentence" or (carry == "auto" and since >= gap):
                res.append(sent[:len(sent) - len(body)] + cur + body)     # 问句标签只管问句那一句，后面马上接回原来的情绪
                since, after_q = len(body), False
            else:
                res.append(sent)
                since += len(body)
        out.append("".join(res))
    return out


def tags_not_at_sentence_start(tagged):
    """标签不在句首的位置（句中标签容易被念出来，也会让一句话内部变调）。"""
    bad = []
    for m in re.finditer(r"\[[a-z_]+\]", tagged):
        i = m.start()
        before = tagged[:i].rstrip()
        if before and before[-1] not in "。！？!?；;…]":
            bad.append(tagged[max(0, i - 8):i + 12])
    return bad


# ---------------------------------------------------------------- 密钥
def find_key():
    """返回 (key, 来源描述)；找不到返回 (None, None)。来源描述不含密钥。"""
    k = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if k:
        return k, "环境变量 DASHSCOPE_API_KEY"
    f = os.environ.get("BL_KEY_FILE", "").strip()
    if f and os.path.isfile(f):
        k = open(f, encoding="utf-8").read().strip()
        if k:
            return k, "BL_KEY_FILE 指向的文件"
    if os.path.isfile(DEFAULT_KEY_FILE):
        k = open(DEFAULT_KEY_FILE, encoding="utf-8").read().strip()
        if k:
            return k, "默认密钥文件 " + DEFAULT_KEY_FILE
    return None, None


def require_key():
    key, src = find_key()
    if not key:
        print(GUIDE_TEXT)
        sys.exit(3)
    return key


# ---------------------------------------------------------------- HTTP
def post_json(url, body, key, timeout=180):
    """返回 (status, data)。status=0 表示连接层错误（可重试）。"""
    req = urllib.request.Request(
        url, json.dumps(body, ensure_ascii=False).encode("utf-8"),
        {"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"message": raw[:300]}
    except Exception as e:                      # 断连、超时等
        return 0, {"message": type(e).__name__}


def download(url, dst, tries=3):
    last = None
    for _ in range(tries):
        try:
            urllib.request.urlretrieve(url, dst)
            return
        except Exception as e:
            last = e
    raise RuntimeError("下载音频失败：" + type(last).__name__)


# ---------------------------------------------------------------- 项目缓存
def update_meta(workdir, **kw):
    """如果 workdir 是项目目录（有 meta.json），把进度写回去；否则什么也不做。"""
    p = os.path.join(workdir, "meta.json")
    if not os.path.isfile(p):
        return
    m = json.load(open(p, encoding="utf-8"))
    m.update(kw)
    json.dump(m, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- ffmpeg
def ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        sys.exit("找不到 ffmpeg：请安装 ffmpeg 并加入 PATH，或 pip install imageio-ffmpeg")


def to_pcm_wav(src, dst, sr=SAMPLE_RATE):
    """统一转成 16bit 单声道 wav，顺便修正接口返回的 wav 头里不准的时长字段。"""
    subprocess.run([ffmpeg(), "-y", "-v", "error", "-i", src, "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", dst],
                   check=True)


def media_seconds(path):
    """用 ffmpeg 读时长（秒）。"""
    r = subprocess.run([ffmpeg(), "-hide_banner", "-i", path], capture_output=True)
    for line in r.stderr.decode("utf-8", "replace").splitlines():
        if "Duration:" in line:
            t = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = t.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return None
