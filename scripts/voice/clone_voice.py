"""用用户给的一段干净人声复刻音色（百炼 voice-enrollment），结果存到 <workdir>/voice.json，
并记为用户默认音色（<根>/voice.json），之后的新项目不给样本就自动复用。

  python clone_voice.py --sample 我的声音.wav --workdir <项目目录>
  python clone_voice.py --workdir <项目目录>                                # 复用默认音色
  python clone_voice.py --sample 新声音.wav --workdir <项目目录> --force      # 换声音
  python clone_voice.py --sample 我的声音.wav --workdir <项目目录> --dry-run  # 只做本地检查，不上传
  python clone_voice.py --list                                              # 列出复刻过的所有音色
  python clone_voice.py --workdir <项目目录> --use 2                         # 换回某个以前的音色（序号、样本文件名里的字、音色号都行），不用重新上传

项目目录里已有 voice.json 时直接复用，除非加 --force。
参考音频：请用户给 10–20 秒干净的正常口播；接口要求至少 5 秒、不超过 60 秒、≤10MB，格式 wav/mp3/m4a。
本脚本只做接口会拒绝的技术检查（时长、大小、格式），不检查噪声、不询问授权。
"""
import argparse
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

HIST = os.path.join(C.WORKSPACE_ROOT, "voice_history.json")


def _read(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def history():
    """复刻过的所有音色：历史记录文件，加上默认音色和各项目里的 voice.json（以前没有历史文件时靠它们找回来）。按创建时间排序、去重。"""
    cands = []
    h = _read(HIST)
    cands += h if isinstance(h, list) else []
    cands.append(_read(os.path.join(C.WORKSPACE_ROOT, "voice.json")))
    proj = os.path.join(C.WORKSPACE_ROOT, "projects")
    if os.path.isdir(proj):
        cands += [_read(os.path.join(proj, d, "voice.json")) for d in sorted(os.listdir(proj))]
    seen, rows = set(), []
    for c in cands:
        if isinstance(c, dict) and c.get("voice_id") and c["voice_id"] not in seen:
            seen.add(c["voice_id"])
            rows.append(c)
    return sorted(rows, key=lambda r: r.get("created_at", ""))


MIME = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample")
    ap.add_argument("--workdir")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--use", help="换回以前复刻过的音色：序号、样本文件名里的字或音色号")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    default_vj = os.path.join(C.WORKSPACE_ROOT, "voice.json")
    if a.list or a.use:
        rows = history()
        if a.list or not rows:
            for i, r in enumerate(rows, 1):
                print("%d  %s  %s  %s秒  …%s%s" % (i, r.get("created_at", ""), os.path.basename(r.get("sample", "")), r.get("sample_seconds", "?"),
                                                  r["voice_id"][-8:], "  ← 当前默认" if r["voice_id"] == (_read(default_vj) or {}).get("voice_id") else ""))
            if not rows:
                print("还没有复刻过音色")
            return
        if not a.workdir:
            sys.exit("--use 需要同时给 --workdir")
        hit = [r for i, r in enumerate(rows, 1) if a.use == str(i) or a.use in os.path.basename(r.get("sample", "")) or a.use in r["voice_id"]]
        if len(hit) != 1:
            sys.exit("没有唯一匹配的音色（匹配到 %d 个），先用 --list 看序号" % len(hit))
        os.makedirs(a.workdir, exist_ok=True)
        for dst in (os.path.join(a.workdir, "voice.json"), default_vj):
            json.dump(hit[0], open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        C.update_meta(a.workdir, voice_id=hit[0]["voice_id"])
        print("已换成：%s（样本 %s），并记为默认音色" % (hit[0]["voice_id"], os.path.basename(hit[0].get("sample", ""))))
        return
    if not a.workdir:
        sys.exit("需要 --workdir")
    os.makedirs(a.workdir, exist_ok=True)
    vj = os.path.join(a.workdir, "voice.json")
    if os.path.isfile(vj) and not a.force and not a.dry_run:
        info = json.load(open(vj, encoding="utf-8"))
        print("复用已有音色：", info["voice_id"])
        return
    if not a.sample:
        if os.path.isfile(default_vj) and not a.dry_run:
            info = json.load(open(default_vj, encoding="utf-8"))
            json.dump(info, open(vj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            C.update_meta(a.workdir, voice_id=info["voice_id"])
            print("复用用户默认音色：", info["voice_id"])
            return
        sys.exit("没有可复用的音色，需要提供一段人声样本（--sample）")

    if not os.path.isfile(a.sample):
        sys.exit("找不到音频文件：" + a.sample)
    ext = os.path.splitext(a.sample)[1].lower()
    if ext not in MIME:
        sys.exit("格式不支持：%s（支持 wav / mp3 / m4a）" % ext)
    size = os.path.getsize(a.sample)
    if size > 10 * 1024 * 1024:
        sys.exit("音频超过 10MB，请换一段更短的")
    secs = C.media_seconds(a.sample)
    if secs is None:
        sys.exit("读不到音频时长，文件可能已损坏")
    if secs < 5:
        sys.exit("音频只有 %.1f 秒，接口要求至少 5 秒的连续朗读，请重新提供" % secs)
    if secs > 60:
        sys.exit("音频 %.1f 秒，超过接口上限 60 秒，请截一段 10–20 秒的" % secs)
    print("参考音频：%.1f 秒，%.0f KB，%s" % (secs, size / 1024, ext))
    if a.dry_run:
        print("[dry-run] 本地检查通过，未上传。")
        return

    key = C.require_key()
    b64 = base64.b64encode(open(a.sample, "rb").read()).decode()
    body = {"model": "voice-enrollment",
            "input": {"action": "create_voice", "target_model": C.MODEL, "prefix": "myvoice",
                      "url": "data:%s;base64,%s" % (MIME[ext], b64)}}
    status, data = C.post_json(C.CLONE_URL, body, key)
    out = data.get("output") or {}
    vid = out.get("voice_id") or out.get("voice")
    if not vid:
        if status in (401, 403):
            print("密钥被拒绝（%s）。请重新获取 API Key：%s" % (status, C.KEY_PAGE))
            sys.exit(4)
        sys.exit("创建音色失败：HTTP %s %s" % (status, json.dumps(data, ensure_ascii=False)[:400]))
    json.dump({"voice_id": vid, "sample": os.path.abspath(a.sample), "sample_seconds": round(secs, 1),
               "model": C.MODEL, "created_at": time.strftime("%Y-%m-%d %H:%M:%S")},
              open(vj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.makedirs(C.WORKSPACE_ROOT, exist_ok=True)
    json.dump(json.load(open(vj, encoding="utf-8")), open(default_vj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(history(), open(HIST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    C.update_meta(a.workdir, voice_id=vid)
    print("音色已创建：", vid, "\n已保存：", vj, "（同时记为用户默认音色）")


if __name__ == "__main__":
    main()
