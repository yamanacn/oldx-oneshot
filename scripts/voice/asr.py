"""百炼语音识别 qwen-audio-3.1-asr-flash：返回文字 + 逐词起止时间（秒）。

被 align_words.py 和 verify_audio.py 共用。音频先转成 16k 单声道 wav，以 data URI 内联上传（≤10MB）。
识别结果按"音频内容哈希"缓存在 <cache_dir>/asr_<哈希>.json，同一段音频不会重复计费。
"""
import base64
import hashlib
import json
import os
import random
import re
import tempfile
import threading
import time

import common as C

_lock = threading.Lock()
_next = [0.0]
INTERVAL = 0.5


def _pace():
    with _lock:
        now = time.time()
        t = max(now, _next[0])
        _next[0] = t + INTERVAL
    if t > now:
        time.sleep(t - now)


def _collect_sentences(obj, acc):
    """响应里 sentence/sentences 的嵌套层数不固定，递归找出所有带 words 的字典。"""
    if isinstance(obj, dict):
        if isinstance(obj.get("words"), list):
            acc.append(obj)
        for v in obj.values():
            _collect_sentences(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _collect_sentences(v, acc)


def parse(resp):
    sents = []
    _collect_sentences(resp, sents)
    seen, out_words, texts = set(), [], []
    for s in sorted(sents, key=lambda s: s.get("begin_time", 0)):
        sid = (s.get("sentence_id"), s.get("begin_time"))
        if sid in seen:
            continue
        seen.add(sid)
        texts.append(s.get("text", ""))
        for w in s["words"]:
            out_words.append({"text": w["text"] + (w.get("punctuation") or ""),
                              "word": w["text"],
                              "begin": w["begin_time"] / 1000.0, "end": w["end_time"] / 1000.0})
    return {"text": "".join(texts), "words": out_words}


def transcribe(wav_path, key, cache_dir=None):
    """返回 {"text","words":[{text,word,begin,end}]}；失败抛 RuntimeError，密钥被拒抛 PermissionError。"""
    h = hashlib.sha256(open(wav_path, "rb").read()).hexdigest()[:16]
    cp = os.path.join(cache_dir, "asr_%s.json" % h) if cache_dir else None
    if cp and os.path.isfile(cp):
        return json.load(open(cp, encoding="utf-8"))
    tmp = os.path.join(tempfile.gettempdir(), "asr16k_%s.wav" % h)
    C.to_pcm_wav(wav_path, tmp, sr=16000)
    size = os.path.getsize(tmp)
    if size > 9.5 * 1024 * 1024:
        raise RuntimeError("音频转 16k 后 %.1fMB，超过 10MB 上限，请按段识别" % (size / 1048576))
    b64 = base64.b64encode(open(tmp, "rb").read()).decode()
    os.remove(tmp)
    body = {"model": C.ASR_MODEL,
            "input": {"messages": [{"role": "user", "content": [
                {"type": "input_audio", "input_audio": {"data": "data:audio/wav;base64," + b64}}]}]},
            "parameters": {"format": "wav", "sample_rate": "16000"}}
    last = ""
    for attempt in range(1, C.MAX_TRIES + 1):
        _pace()
        status, data = C.post_json(C.ASR_URL, body, key, timeout=240)
        if status == 200:
            res = parse(data)
            if not res["words"]:
                raise RuntimeError("识别返回里没有逐词时间戳：" + json.dumps(data, ensure_ascii=False)[:200])
            if cp:
                json.dump(res, open(cp, "w", encoding="utf-8"), ensure_ascii=False)
            return res
        last = "HTTP %s %s" % (status, (data.get("message") or "")[:100])
        if status in (401, 403):
            raise PermissionError("密钥被拒绝（HTTP %s）。请重新获取 API Key：%s" % (status, C.KEY_PAGE))
        if status in (429, 0) or status >= 500:
            time.sleep(min(2.0 * attempt, 15.0) + random.random())
            continue
        break
    raise RuntimeError("识别失败：" + last)


# ---------------------------------------------------------------- 文字比较用：统一数字写法
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


def norm_text(s):
    """去标点和空白，并把中文数字统一成阿拉伯数字（识别结果常把"三十"写成"30"）。"""
    s = re.sub(r"[一-鿿]*", lambda m: re.sub(r"[零〇一二两三四五六七八九十百千]+",
                                                     lambda n: str(_cn_to_int(n.group())), m.group()), s)
    return re.sub(r"[\s，。、！？!?；;：:“”\"'（）()\-—…,.]", "", s).lower()
