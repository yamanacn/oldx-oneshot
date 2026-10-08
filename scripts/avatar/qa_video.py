"""成片自动质检：嘴部是否随说话动、两只手是否有一只僵着、各段色彩是否一致。

  python qa_video.py --workdir <项目目录>                    # 默认检查 avatar/final_avatar.mp4
  python qa_video.py --workdir <项目目录> --video 其他.mp4

检查内容（都是帧间差分得到的粗略指标，只用来**发现该看的地方**，不证明口型对不对）：
  1. 嘴部：用 OpenCV 自带的人脸检测定位脸，取下半脸；按 1 秒窗口，音频说话占比 ≥50% 但嘴部动作量
     低于全片说话时嘴部动作量中位数的 45%，连续 ≥3 个窗口 → 标出"说话时嘴几乎不动"（第一版第 4 段 40–49 秒就是这种）。
  2. 两只手：以脸为中心取画面左右两侧、胸前到画面底部的区域，统计每段里动作量 <0.8 的帧占比；
     任一只手 >60% 标出。
  3. 色彩：取画面左右边缘的背景条带，按段算平均 Lab 色，与各段中位色的距离 >25 标出。
  4. 逐秒色彩：同一条带每秒的平均色与输入图片比，色相偏转 >20° 或彩度 <55% 的连续区间记为异常时间段。
输出 avatar/anomalies.json（嘴不动和逐秒色彩的异常时间段），供包装环节遮盖用。
手和色彩的阈值按用户看片后的意见放宽过（2026-10-03）：单段手静止 56%、段间色差约 19 的成片用户认为可以接受；
整段明显变成另一种色调（实测色差 54–72）仍会被标出。嘴的阈值没有放宽。阈值都是经验值，只在这类构图（人物居中、双手在身体两侧）上验证过；换构图后先人工看一遍再信。
"""
import argparse
import json
import os
import sys
import wave

import cv2
import numpy as np

STATIC_THR = 0.8
MOUTH_RATIO = 0.45
HAND_STATIC_MAX = 0.60     # 一段里某只手近似静止的帧占比超过它才标出
COLOR_DE_MAX = 25.0        # 某段背景色与各段中位色的 Lab 距离超过它才标出
HUE_SHIFT_MAX = 20.0       # 逐秒：背景色相比图片偏转超过多少度算变色（实测正常 ≤12°，变青/变黄绿 ≥26°）
CHROMA_MIN = 0.55          # 逐秒：背景彩度降到图片的多少倍以下算褪色（实测正常 0.9–1.7，褪成灰白 ≤0.4）



def _imwrite(path, img):
    """cv2.imwrite 在带中文的路径上会悄悄失败（不报错、也不写文件），改成先编码再用 numpy 写出"""
    ok, buf = cv2.imencode(os.path.splitext(str(path))[1] or ".png", img)
    if not ok:
        raise RuntimeError("图片编码失败：%s" % path)
    buf.tofile(str(path))
    return True

def color_anomalies(colors, ref_ab, fps):
    """逐秒比较背景色与输入图片：色相偏转或褪色的连续区间，返回 [(起, 止, 说明)]。
    只看色相和彩度，不看亮度和"更鲜艳"——同一色调下变亮、变浓是用户认可的正常波动。"""
    r = ref_ab - 128.0
    rn = float(np.linalg.norm(r))
    bad = []
    for sec in range(int(len(colors) / fps)):
        v = colors[int(sec * fps):int((sec + 1) * fps), 1:].mean(0) - 128.0
        vn = float(np.linalg.norm(v))
        if rn < 6:                                      # 图片背景接近无彩色：没有色相可比，看有没有染上颜色
            if float(np.linalg.norm(v - r)) > 12:
                bad.append((sec, "背景染上了图片里没有的颜色"))
            continue
        hue = float(np.degrees(np.arccos(np.clip(np.dot(v, r) / (vn * rn + 1e-6), -1, 1))))
        if vn / rn < CHROMA_MIN:
            bad.append((sec, "背景褪色（彩度只有图片的 %.0f%%）" % (vn / rn * 100)))
        elif hue > HUE_SHIFT_MAX:
            bad.append((sec, "背景变成另一种色调（色相偏转 %.0f°）" % hue))
    runs = []
    for sec, why in bad:
        if runs and sec <= runs[-1][1] + 1:             # 相邻或只隔 1 秒的并成一个区间
            runs[-1][1] = sec + 1
        else:
            runs.append([sec, sec + 1, why])
    return [(a, b, why) for a, b, why in runs]


def face_box(video):
    if not hasattr(cv2, "CascadeClassifier") or not hasattr(cv2, "data"):
        return None            # 本机的 OpenCV 没带人脸检测（如 5.x 精简版），改用人物居中的默认位置
    cap = cv2.VideoCapture(video)
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    boxes = []
    for t in (0.5, 2.0, 4.0, 6.0):
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, f = cap.read()
        if not ok:
            continue
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        r = det.detectMultiScale(g, 1.1, 5, minSize=(f.shape[1] // 10, f.shape[1] // 10))
        if len(r):
            boxes.append(max(r, key=lambda b: b[2] * b[3]))
    cap.release()
    if not boxes:
        return None
    return tuple(int(np.median([b[i] for b in boxes])) for i in range(4))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--video")
    a = ap.parse_args()
    od = os.path.join(a.workdir, "avatar")
    video = a.video or os.path.join(od, "final_avatar.mp4")
    plan = json.load(open(os.path.join(od, "plan.json"), encoding="utf-8"))
    segs = plan["segments"]
    cap = cv2.VideoCapture(video)
    fps, W, H = cap.get(cv2.CAP_PROP_FPS), int(cap.get(3)), int(cap.get(4))
    info_p = os.path.join(od, "image_info.json")
    info = json.load(open(info_p, encoding="utf-8")) if os.path.isfile(info_p) else {}
    mb = info.get("mouth_box")          # 由看图的人写入：嘴部在画面中的位置（占画面宽高的比例）[x0,y0,x1,y1]
    fb = face_box(video)
    if mb:
        mx0, my0, mx1, my1 = int(mb[0] * W), int(mb[1] * H), int(mb[2] * W), int(mb[3] * H)
        cx = (mx0 + mx1) // 2
        fy, fh = int(my0 - 0.25 * H), int(0.3 * H)
        print("嘴部区域：使用图片信息里给出的位置 %s" % str(mb))
    elif fb is not None:
        fx, fy, fw, fh = fb
        cx = fx + fw // 2
        mx0, mx1, my0, my1 = fx + int(0.25 * fw), fx + int(0.75 * fw), fy + int(0.68 * fh), fy + int(0.98 * fh)
        print("嘴部区域：用人脸检测定位")
    else:
        mx0, mx1, my0, my1 = int(0.42 * W), int(0.58 * W), int(0.46 * H), int(0.58 * H)
        cx, fy, fh = W // 2, int(0.12 * H), int(0.3 * H)
        print("提示：既没有嘴部位置也没有人脸检测，嘴部区域按人物居中估算，嘴部结果可能不准。"
              "让看图的人用 prepare_image.py --mouth-box 写入嘴的位置。")
    reg = {"嘴": (mx0, mx1, my0, my1),
           "左手": (max(0, cx - int(0.42 * W)), max(0, cx - int(0.20 * W)), min(H - 2, fy + int(1.2 * fh)), H),
           "右手": (min(W - 2, cx + int(0.26 * W)), min(W, cx + int(0.45 * W)), min(H - 2, fy + int(1.2 * fh)), H)}
    mot = {k: [] for k in reg}
    edge_cols = list(range(0, int(W * 0.06))) + list(range(int(W * 0.94), W))
    colors = []
    prev = None
    while True:
        ok, f = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None:
            for k, (x0, x1, y0, y1) in reg.items():
                mot[k].append(float(np.abs(g[y0:y1, x0:x1] - prev[y0:y1, x0:x1]).mean()))
        prev = g
        lab = cv2.cvtColor(f[: int(H * 0.5)][:, edge_cols], cv2.COLOR_BGR2LAB).reshape(-1, 3).mean(0)
        colors.append(lab)
    cap.release()
    mot = {k: np.array(v) for k, v in mot.items()}
    colors = np.array(colors)
    n = len(mot["嘴"])
    w = wave.open(os.path.join(a.workdir, "out", "merged.wav"))
    sr = w.getframerate()
    audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    w.close()
    act = np.array([np.sqrt(np.mean(audio[int(i / fps * sr):int((i + 1) / fps * sr)] ** 2)) if int((i + 1) / fps * sr) > int(i / fps * sr) else 0 for i in range(n)])
    speak = act > 0.05 * np.percentile(act, 95)
    med_mouth = float(np.median(mot["嘴"][speak]))
    flags = []
    # ---- 嘴
    low_windows = []
    for sec in range(int(n / fps)):
        sl = slice(int(sec * fps), int((sec + 1) * fps))
        if speak[sl].mean() >= 0.5 and mot["嘴"][sl].mean() < MOUTH_RATIO * med_mouth:
            low_windows.append(sec)
    runs, cur = [], []
    for s in low_windows:
        if cur and s != cur[-1] + 1:
            runs.append(cur)
            cur = []
        cur.append(s)
    if cur:
        runs.append(cur)
    mouth_runs = [(r[0], r[-1] + 1) for r in runs if len(r) >= 3]
    for a0, a1 in mouth_runs:
        segno = [s["i"] for s in segs if s["start"] <= (a0 + a1) / 2 < s["end"]]
        flags.append("嘴：%d–%d 秒（段%s）说话时嘴部几乎不动（动作量低于全片中位数 %.1f 的 %d%%）" % (a0, a1, segno[0] if segno else "?", med_mouth, MOUTH_RATIO * 100))
    # ---- 手 & 色彩 按段
    base = np.median(np.array([colors[slice(int(s["start"] * fps), min(n, int(s["end"] * fps)))].mean(0) for s in segs]), axis=0)
    rows = []
    for s in segs:
        sl = slice(int(s["start"] * fps), min(n, int(s["end"] * fps)))
        row = {"i": s["i"], "mouth": float(mot["嘴"][sl][speak[sl]].mean()) if speak[sl].any() else None,
               "left_static": float((mot["左手"][sl] < STATIC_THR).mean()), "right_static": float((mot["右手"][sl] < STATIC_THR).mean())}
        c = colors[sl].mean(0)
        row["color_dE"] = float(np.linalg.norm(c - base))
        rows.append(row)
        for side, key in (("左手", "left_static"), ("右手", "right_static")):
            if row[key] > HAND_STATIC_MAX:
                flags.append("手：段%d 的%s有 %.0f%% 的帧近似静止（>%d%%）" % (s["i"], side + "（画面左侧）" if side == "左手" else side + "（画面右侧）", row[key] * 100, HAND_STATIC_MAX * 100))
        if row["color_dE"] > COLOR_DE_MAX:
            flags.append("色彩：段%d 的背景色与各段中位色相差 %.1f（>%d），整段色调明显不同" % (s["i"], row["color_dE"], COLOR_DE_MAX))
    # ---- 逐秒色彩：与输入图片比（按段平均会把段内几秒的变色平均掉）
    anomalies = [{"start": float(a0), "end": float(a1), "type": "mouth", "cover": "content_full",
                  "detail": "说话时嘴部几乎不动"} for a0, a1 in mouth_runs]
    img_p = os.path.join(od, "image.png")
    ref_img = cv2.imdecode(np.fromfile(img_p, dtype=np.uint8), cv2.IMREAD_COLOR) if os.path.isfile(img_p) else None
    if ref_img is not None:
        ref_img = cv2.resize(ref_img, (W, H), interpolation=cv2.INTER_AREA)
        ref_ab = cv2.cvtColor(ref_img[: int(H * 0.5)][:, edge_cols], cv2.COLOR_BGR2LAB).reshape(-1, 3).mean(0)[1:]
        for a0, a1, why in color_anomalies(colors, ref_ab, fps):
            anomalies.append({"start": float(a0), "end": float(a1), "type": "color", "cover": "content_pip", "detail": why})
            flags.append("色彩：%d–%d 秒 %s" % (a0, a1, why))
    anomalies.sort(key=lambda x: x["start"])
    json.dump({"video": video, "duration": round(n / fps, 2), "anomalies": anomalies},
              open(os.path.join(od, "anomalies.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("全片说话时嘴部动作量中位数 %.2f" % med_mouth)
    print("段   说话时嘴动量   左手静止帧   右手静止帧   色差ΔE")
    for r in rows:
        print("%-3d  %-12s  %-10s  %-10s  %.1f" % (r["i"], "-" if r["mouth"] is None else "%.2f" % r["mouth"],
                                                   "%.0f%%" % (r["left_static"] * 100), "%.0f%%" % (r["right_static"] * 100), r["color_dE"]))
    tot_l, tot_r = float((mot["左手"] < STATIC_THR).mean()), float((mot["右手"] < STATIC_THR).mean())
    print("全片：左手静止帧 %.0f%%，右手静止帧 %.0f%%" % (tot_l * 100, tot_r * 100))
    print()
    print("需要看一下的地方：" if flags else "没有触发任何阈值（仍建议抽帧看一遍）。")
    for f in flags:
        print(" -", f)
    if anomalies:
        print("异常时间段已写入 %s（%d 处）。不想重跑时可以交给包装环节遮盖：人物缩到小窗或暂时隐藏，主画面放图形。"
              % (os.path.join(od, "anomalies.json"), len(anomalies)))
    # ---- 嘴部逐秒图：每秒一格，标出这一秒音频是否在说话（红 S = 在说话）。帧间差分会被头部晃动干扰，
    #      "说话时嘴是否闭着"最终要看这张图（用 Read 工具看）。
    cap = cv2.VideoCapture(video)
    tiles = []
    mx0_, mx1_, my0_, my1_ = reg["嘴"]
    padx, pady = int((mx1_ - mx0_) * 0.35), int((my1_ - my0_) * 0.6)
    X0, X1, Y0, Y1 = max(0, mx0_ - padx), min(W, mx1_ + padx), max(0, my0_ - pady), min(H, my1_ + pady)
    for sec in range(int(n / fps)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int((sec + 0.5) * fps))
        ok, f = cap.read()
        if not ok:
            break
        t = cv2.resize(f[Y0:Y1, X0:X1], (220, int(220 * (Y1 - Y0) / max(1, X1 - X0))))
        sp = speak[int(sec * fps):int((sec + 1) * fps)].mean() >= 0.5
        cv2.putText(t, "%ds" % sec, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        if sp:
            cv2.putText(t, "S", (t.shape[1] - 26, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        tiles.append(t)
    cap.release()
    if tiles:
        cols = 8
        while len(tiles) % cols:
            tiles.append(np.zeros_like(tiles[0]))
        _imwrite(os.path.join(od, "mouth_strip.png"), np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]))
        print("嘴部逐秒图（红 S = 这一秒在说话；说话的秒里嘴一直闭着就是问题）：", os.path.join(od, "mouth_strip.png"))
    print("说明：这些是帧间差分的粗略指标，用来发现该看的地方；口型是否对准、手势是否自然仍要人看。")
    json.dump({"rows": rows, "flags": flags, "mouth_region": list(reg["嘴"]), "mouth_median": med_mouth,
               "hand_static_total": [tot_l, tot_r], "mouth_low_ranges": mouth_runs},
              open(os.path.join(od, "qa.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
