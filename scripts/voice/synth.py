"""按段并发合成配音，并拼成一条：<workdir>/out/merged.wav + 每段 seg_NN.wav + timeline.json。

  python synth.py --workdir 配音
  python synth.py --workdir 配音 --paragraphs 配音/paragraphs.json --workers 6 --gap 0.3

要点（都来自实测）：
  * 最多 6 路并发；相邻提交至少间隔 0.5 秒。瞬间同时提交 6 路只会放行约 3 路，其余 429；错开就全过。
  * 429、连接错误、5xx 自动退避重试，最多 6 次；只重试失败的那一段。
  * 每段结果按 文字+音色+指令 缓存在 cache/，改一段只重跑那一段。
  * 某段被接口以"过长"拒绝时，只在该段内部按句号对半拆再合成，并在结果里说明。
  * 固定参数在 common.py：模型、指令（语速较快、不要播音腔）、48k wav。
  * 情绪标签只用一个：以问号结尾的句子前加 QUESTION_TAG（让问句带疑问语气），只加在发给接口的文字里。
  * 标签有时会引出文案里没有的语气词（嘿、哼、哎……）：合并之前对每段识别一次，把多出来的这一声剪掉（fillers.py）；--keep-fillers 不剪。
"""
import argparse
import hashlib
import json
import os
import random
import re
import shutil
import sys
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

_lock = threading.Lock()
_next_submit = [0.0]
FATAL = {401, 403}


def pace():
    """全局节流：相邻两次提交至少间隔 SUBMIT_INTERVAL 秒（含重试）。"""
    with _lock:
        now = time.time()
        t = max(now, _next_submit[0])
        _next_submit[0] = t + C.SUBMIT_INTERVAL
    if t > now:
        time.sleep(t - now)


def cache_key(text, voice):
    h = hashlib.sha256("|".join([C.MODEL, voice, C.INSTRUCTION, str(C.SAMPLE_RATE), text]).encode("utf-8"))
    return h.hexdigest()[:16]


def split_half(text):
    """在句末标点处对半拆（用于被接口拒绝过长的段）；找不到句末标点返回 None。"""
    marks = [m.end() for m in re.finditer(r"[。！？!?；;\n]", text)]
    marks = [m for m in marks if 20 < m < len(text) - 20]
    if not marks:
        return None
    mid = min(marks, key=lambda m: abs(m - len(text) / 2))
    return text[:mid], text[mid:]


EMOTIONS = []      # 这个项目的情绪标签规则，main() 里从 <workdir>/emotions.json 读入
TAGGED = {}        # 段落原文 -> 加好所有标签的文字（见 common.apply_carry）


def build_text(raw):
    """发给合成接口的文字：段首沿用的标签 + 问句标签 + 情绪标签。不改文案本身。"""
    return (TAGGED.get(raw) or C.tag_emotions(C.tag_questions(raw), EMOTIONS)).replace("\n", "").strip()


class Locked(Exception):
    pass


def safe_write_wav(path, x, sr):
    """先写临时文件再替换。目标被别的程序占用（多半是播放器）时重试几次；还不行就另存为 *_新.wav 并报 Locked。"""
    tmp = path + ".tmp"
    write_wav(tmp, x, sr)
    for _ in range(6):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(1.5)
    alt = path[:-4] + "_新.wav"
    os.replace(tmp, alt)
    raise Locked("%s 被其他程序占用（多半是播放器），新的已另存为 %s。关掉播放器后重跑同一条命令即可（都已缓存，几秒钟）" % (path, alt))


def archive_previous(out, fp):
    """重新合成出了不同的结果时，把上一版存到 <out>_prev/<时间>/，免得覆盖掉用户还想对比的版本。"""
    old = os.path.join(out, "merged.wav")
    if not os.path.isfile(old):
        return None
    try:
        prev = json.load(open(os.path.join(out, "timeline.json"), encoding="utf-8")).get("fingerprint")
    except (OSError, ValueError):
        prev = None
    if prev == fp:
        return None
    dest = os.path.join(os.path.dirname(out), os.path.basename(out) + "_prev", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(dest, exist_ok=True)
    for name in ("merged.wav", "timeline.json"):
        if os.path.isfile(os.path.join(out, name)):
            shutil.copy2(os.path.join(out, name), os.path.join(dest, name))
    for name in ("words.json", "verify.json"):                     # 这两份属于旧音频，留在原处会误导后面的步骤
        if os.path.isfile(os.path.join(out, name)):
            shutil.move(os.path.join(out, name), os.path.join(dest, name))
    return dest


class Fatal(Exception):
    pass


def synth_text(text, voice, key, cache_dir, label, depth=0):
    """合成一段文字，返回规范化后的 wav 路径。可能返回拼接过的拆半结果。"""
    n_chars = len(text.replace("\n", "").strip())
    text = build_text(text)
    ck = cache_key(text, voice)
    out = os.path.join(cache_dir, ck + ".wav")
    if os.path.isfile(out):
        print("[%s] 命中缓存" % label, flush=True)
        return out, [], True
    body = {"model": C.MODEL, "input": {"text": text, "voice": voice, "format": "wav",
                                        "sample_rate": C.SAMPLE_RATE, "instruction": C.INSTRUCTION}}
    for attempt in range(1, C.MAX_TRIES + 1):
        pace()
        t0 = time.time()
        status, data = C.post_json(C.TTS_URL, body, key)
        url = ((data.get("output") or {}).get("audio") or {}).get("url") if status == 200 else None
        if url:
            raw = os.path.join(cache_dir, ck + ".raw.wav")
            C.download(url, raw)
            C.to_pcm_wav(raw, out)
            os.remove(raw)
            print("[%s] 完成（%d 字，%.1fs，第 %d 次尝试）" % (label, n_chars, time.time() - t0, attempt), flush=True)
            return out, [], False
        msg = (data.get("message") or "")[:160]
        if status in FATAL:
            raise Fatal("密钥被拒绝（HTTP %s）。请重新获取 API Key：%s" % (status, C.KEY_PAGE))
        too_long = status == 400 and re.search(r"length|long|exceed|too|超|长度|限制", msg, re.I)
        if too_long and depth < 3:
            halves = split_half(text)
            if halves:
                print("[%s] 接口以过长拒绝，改为按句对半拆分" % label, flush=True)
                parts, notes = [], ["%s 过长被拆成两半" % label]
                for j, h in enumerate(halves):
                    p, n, _ = synth_text(h, voice, key, cache_dir, "%s.%d" % (label, j + 1), depth + 1)
                    parts.append(p)
                    notes += n
                joined = os.path.join(cache_dir, ck + ".wav")
                concat_wavs(parts, joined, gap=0.0)
                return joined, notes, False
        if status in (429, 0) or status >= 500:
            wait = min(2.0 * attempt, 15.0) + random.random()
            print("[%s] HTTP %s %s，%.1fs 后重试（%d/%d）" % (label, status, msg[:40], wait, attempt, C.MAX_TRIES), flush=True)
            time.sleep(wait)
            continue
        raise RuntimeError("%s 失败：HTTP %s %s" % (label, status, msg))
    raise RuntimeError("%s 重试 %d 次仍失败" % (label, C.MAX_TRIES))


def read_wav(path):
    w = wave.open(path)
    try:
        assert w.getsampwidth() == 2 and w.getnchannels() == 1, "只支持 16bit 单声道"
        return np.frombuffer(w.readframes(w.getnframes()), np.int16), w.getframerate()
    finally:
        w.close()


def write_wav(path, x, sr):
    w = wave.open(path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes(np.asarray(x, np.int16).tobytes())
    w.close()


def trim(x, sr):
    """去掉首尾多余静音：保留开头 60ms、结尾 120ms 的自然余量。"""
    a = np.abs(x.astype(np.float32))
    if a.max() <= 0:
        return x, 0
    idx = np.where(a > 0.01 * a.max())[0]
    s = max(0, idx[0] - int(0.06 * sr))
    e = min(len(x), idx[-1] + int(0.12 * sr))
    return x[s:e], s


def concat_wavs(paths, dst, gap=0.0):
    parts, sr = [], C.SAMPLE_RATE
    for i, p in enumerate(paths):
        x, sr = read_wav(p)
        if i and gap > 0:
            parts.append(np.zeros(int(gap * sr), np.int16))
        parts.append(x)
    write_wav(dst, np.concatenate(parts), sr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--paragraphs")
    ap.add_argument("--workers", type=int, default=C.WORKERS)
    ap.add_argument("--gap", type=float, default=C.GAP_SECONDS)
    ap.add_argument("--dry-run", action="store_true", help="只打印每段实际发给接口的文字（带标签），不联网、不合成")
    ap.add_argument("--keep-fillers", action="store_true", help="不剪标签引出来的多余语气词（默认会剪，见 fillers.py）")
    ap.add_argument("--variant", help="另出一版：结果写到 out_<名字>/，不覆盖 out/；后面的对齐和核对加 --out out_<名字>")
    a = ap.parse_args()
    wd = a.workdir
    pj = a.paragraphs or os.path.join(wd, "paragraphs.json")
    vj = os.path.join(wd, "voice.json")
    if not os.path.isfile(vj):
        sys.exit("没有 voice.json：请先运行 clone_voice.py")
    if not os.path.isfile(pj):
        sys.exit("没有 paragraphs.json：请先完成拆段并通过 split_check.py")
    voice = json.load(open(vj, encoding="utf-8"))["voice_id"]
    paras = json.load(open(pj, encoding="utf-8"))["paragraphs"]
    ej = os.path.join(wd, "emotions.json")
    if os.path.isfile(ej):                                  # 这条片子要更有情绪时才有这个文件
        emo = json.load(open(ej, encoding="utf-8"))
        EMOTIONS[:] = emo.get("tags", [])
        if emo.get("instruction"):
            C.INSTRUCTION = emo["instruction"]
        if "question_tag" in emo:                           # 写成 "" 就连问句的标签也不加
            C.QUESTION_TAG = emo["question_tag"]
        used = sum(C.tag_emotions(p, EMOTIONS).count("[") - p.count("[") for p in paras)
        carry = emo.get("carry", "auto")                    # auto 段首必补、段内隔 carry_gap 字补一个（默认）/ sentence 逐句 / paragraph 只段首 / off 不沿用
        for p, t in zip(paras, C.apply_carry(paras, EMOTIONS, carry, emo.get("start_tag", "[excited]"), int(emo.get("carry_gap", 80)))):
            TAGGED[p] = t
        total = sum(len(re.findall(r"\[[a-z_]+\]", t)) for t in TAGGED.values())
        miss = [r["at"] for r in EMOTIONS if not any(r["at"] in p for p in paras)]
        print("情绪标签 %d 条规则，用上 %d 条；沿用方式 %s，发给接口的标签共 %d 个%s" % (len(EMOTIONS), used, carry, total, "；没匹配上：%s" % miss if miss else ""), flush=True)
        for p in paras:
            for frag in C.tags_not_at_sentence_start(C.tag_emotions(p, EMOTIONS)):
                print("注意：这处标签不在句首，容易被念出来：…%s…" % frag, flush=True)
    if a.dry_run:
        for i, p in enumerate(paras, 1):
            print("\n--- 段%d（%d 字）---\n%s" % (i, len(p), build_text(p)))
        print("\n[dry-run] 没有联网，没有合成。指令：%s" % C.INSTRUCTION)
        return
    key = C.require_key()
    cache = os.path.join(wd, "cache")
    out = os.path.join(wd, "out" + ("_" + a.variant if a.variant else ""))
    os.makedirs(cache, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    workers = max(1, min(a.workers, C.WORKERS, len(paras)))
    print("共 %d 段，并发 %d，提交间隔 %.1fs，指令：%s" % (len(paras), workers, C.SUBMIT_INTERVAL, C.INSTRUCTION), flush=True)
    nq = sum(C.tag_questions(p).count(C.QUESTION_TAG) for p in paras) if C.QUESTION_TAG else 0
    print("问句 %d 个%s" % (nq, "，句前加标签 %s" % C.QUESTION_TAG if nq else ""), flush=True)

    t0 = time.time()
    notes, cached = [], 0
    results = [None] * len(paras)
    errors = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(synth_text, p, voice, key, cache, "段%d" % (i + 1)): i for i, p in enumerate(paras)}
        for f, i in futs.items():
            try:
                path, n, hit = f.result()
                results[i] = path
                notes += n
                cached += hit
            except Fatal as e:
                print(str(e))
                sys.exit(4)
            except Exception as e:
                errors.append("段%d：%s" % (i + 1, e))
    if errors:
        print("以下段落失败，未拼接（其余段已缓存，重跑只会补失败的）：")
        for e in errors:
            print("  ", e)
        sys.exit(1)

    if not a.keep_fillers:
        import fillers
        def _clean(i):
            return fillers.clean(results[i], paras[i], key, cache, "段%d" % (i + 1), write_wav, read_wav)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            cleaned = list(ex.map(_clean, range(len(paras))))
        results = [c[0] for c in cleaned]
        for c in cleaned:
            notes += ["剪掉多余语气词 " + n for n in c[1]]

    fp = hashlib.sha256("|".join(os.path.basename(p) for p in results).encode()).hexdigest()[:16] + "|%.2f" % a.gap
    try:
        arch = archive_previous(out, fp)
        if arch:
            print("上一版已存到：", arch, flush=True)
    except OSError as e:
        print("注意：旧版本没能存档（%s），继续" % e, flush=True)
    segs, merged, t = [], [], 0.0
    sr = C.SAMPLE_RATE
    for i, (p, txt) in enumerate(zip(results, paras), 1):
        x, sr = read_wav(p)
        safe_write_wav(os.path.join(out, "seg_%02d.wav" % i), x, sr)
        x, cut = trim(x, sr)
        if merged:
            merged.append(np.zeros(int(a.gap * sr), np.int16))
            t += a.gap
        start = t
        merged.append(x)
        t += len(x) / sr
        segs.append({"i": i, "chars": len(re.sub(r"\s+", "", txt)), "start": round(start, 3), "end": round(t, 3),
                     "trim_start": round(cut / sr, 3), "text": txt})
    try:
        safe_write_wav(os.path.join(out, "merged.wav"), np.concatenate(merged), sr)
    except Locked as e:
        print("\n" + str(e))
        sys.exit(5)
    json.dump({"voice_id": voice, "model": C.MODEL, "instruction": C.INSTRUCTION, "question_tag": C.QUESTION_TAG, "questions": nq, "gap": a.gap, "fingerprint": fp,
               "total_seconds": round(t, 3), "segments": segs}, open(os.path.join(out, "timeline.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    if not a.variant:
        C.update_meta(wd, voice_id=voice, audio=os.path.join(out, "merged.wav"), total_seconds=round(t, 3),
                      segments=len(paras))
    print("\n完成：%d 段，合计 %.1f 秒，耗时 %.1fs（命中缓存 %d 段）" % (len(paras), t, time.time() - t0, cached))
    print("合并文件：", os.path.join(out, "merged.wav"))
    for n in notes:
        print("注意：", n)


if __name__ == "__main__":
    main()
