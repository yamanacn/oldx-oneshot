"""画面检查：镜头有没有被推近或拉远、段与段的接缝跳不跳，并生成全片总览图。

  python qc_frames.py --workdir <项目目录>          # qa_video.py 结束时会自动调用，一般不用单独跑

做法：
  * 镜头：逐段看云端返回的原始片段（avatar/clips/seg_NN.mp4）。每 0.5 秒取一帧，和这一段的第一帧做特征点匹配，
    用 RANSAC 估一个“缩放 + 平移”的整体变换——人物的手和头在动，但整幅画面的缩放只有镜头推拉才会有。
    缩放偏离 1 超过 CAMERA_SCALE_TOL、持续 ≥1 秒，就记为“镜头被推近／拉远”。只看缩放，不看平移
    （人物动作会让平移估计来回跳，实测在正常片段里也能到 10% 以上）。
    标定（2026-10-14，一条 12 段的竖屏片子）：正常段缩放 0.97–1.04；被拉远的段 0.80–0.89。
  * 接缝：按顺序逐帧解码成片（不跳着取帧——跳着取会落到附近的关键帧上，量偏），算相邻两帧的平均差。
    每个接缝取切点前后 2 帧里的最大值，和全片相邻帧差的中位数比，超过 SEAM_RATIO 倍标出。
    同时生成 avatar/seams.png：每行一个接缝，切点前两帧、后两帧。
  * 总览图：每 0.5 秒一格、每格宽 200 像素，按时间排成 avatar/sheets/overview_NN.jpg（每张 10 列 × 6 行 = 30 秒）。多手、手里多出东西、
    烧字幕这类问题脚本判断不了，要用 Read 工具看这几张图。
输出 avatar/qc_frames.json；镜头问题同时写进 avatar/anomalies.json（type = camera）。
"""
import argparse
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _cli  # noqa: E402

import cv2
import numpy as np

CAMERA_SCALE_TOL = 0.06    # 缩放偏离 1 超过它算镜头推拉
CAMERA_MIN_SEC = 1.0       # 至少持续这么久才标出
SAMPLE_SEC = 0.5
SEAM_RATIO = 3.0           # 接缝处帧差超过全片中位数的几倍标出
SHEET_COLS, SHEET_SEC, TILE_W = 10, 30, 200      # 每格宽 200 像素；每张 10 列 × 6 行 = 30 秒


from _util import imwrite as _imwrite  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402


def _small_gray(f, w=None):
    if w:
        f = cv2.resize(f, (w, int(f.shape[0] * w / f.shape[1])))
    return cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)


class _Matcher:
    def __init__(self):
        self.orb = cv2.ORB_create(2000)
        self.bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

    def scale(self, ref_kd, img):
        k0, d0 = ref_kd
        k1, d1 = self.orb.detectAndCompute(img, None)
        if d0 is None or d1 is None:
            return None
        m = self.bf.match(d0, d1)
        if len(m) < 30:
            return None
        a = np.float32([k0[x.queryIdx].pt for x in m])
        b = np.float32([k1[x.trainIdx].pt for x in m])
        M, inl = cv2.estimateAffinePartial2D(a, b, ransacReprojThreshold=3)
        if M is None or inl.sum() < 20:
            return None
        return float(np.hypot(M[0, 0], M[1, 0]))


def camera_check(od, segs, results):
    """返回 (每段的统计, 异常时间段列表)。时间换算到成片时间轴上（段起点 + 段内时间）。"""
    mt = _Matcher()
    rows, anomalies = [], []
    for s in segs:
        r = results.get(str(s["i"])) or {}
        path = os.path.join(od, "clips", "seg_%02d.mp4" % s["i"])
        if not os.path.isfile(path):
            path = r.get("file") or path
        if not os.path.isfile(path):
            rows.append({"i": s["i"], "status": "缺片段"})
            continue
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 24
        step = max(1, int(round(fps * SAMPLE_SEC)))
        ref, k, samples, fails = None, 0, [], 0
        while True:
            ok, f = cap.read()
            if not ok or k / fps > s.get("head", 0.0) + s["duration"] + 0.05:
                break
            if k % step == 0:
                g = _small_gray(f, 480)
                if ref is None:
                    ref = mt.orb.detectAndCompute(g, None)
                sc = mt.scale(ref, g)
                if sc is None:
                    fails += 1
                samples.append((k / fps, sc))
            k += 1
        cap.release()
        vals = [x for _, x in samples if x is not None]
        bad = [t for t, x in samples if x is not None and abs(x - 1) > CAMERA_SCALE_TOL]
        row = {"i": s["i"], "scale_min": round(min(vals), 3) if vals else None, "scale_max": round(max(vals), 3) if vals else None,
               "off_ratio": round(len(bad) / max(1, len(vals)), 2), "unmatched": fails, "samples": len(samples)}
        # 连续的偏离样本合成时间段
        run = []
        for t, x in samples + [(None, None)]:
            off = x is not None and abs(x - 1) > CAMERA_SCALE_TOL
            if off:
                run.append((t, x))
                continue
            if run and run[-1][0] - run[0][0] + SAMPLE_SEC >= CAMERA_MIN_SEC:
                worst = min(run, key=lambda y: -abs(y[1] - 1))[1]
                hd = s.get("head", 0.0)                       # 片段内时间 → 成片时间（拼接时裁过开头）
                anomalies.append({"start": round(max(s["start"], s["start"] + run[0][0] - hd), 2),
                                  "end": round(s["start"] + run[-1][0] - hd + SAMPLE_SEC, 2),
                                  "type": "camera", "segment": s["i"], "cover": "content_full",
                                  "detail": "镜头被%s（整幅画面缩放到 %.2f 倍）" % ("拉远" if worst < 1 else "推近", worst)})
            run = []
        row["status"] = "镜头有推拉" if any(a["segment"] == s["i"] for a in anomalies) else ("无法判断" if not vals else "正常")
        rows.append(row)
    return rows, anomalies


def read_sequential(video, want_frames=(), sample_step=None, tile_w=TILE_W):
    """顺序解码一遍：返回 (相邻帧差数组, {帧号: 原帧}, [(时间, 缩略图)], fps)。"""
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    want = set(want_frames)
    prev, diffs, keep, thumbs, k = None, [], {}, [], 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        g = _small_gray(f, 192).astype(np.float32)
        diffs.append(0.0 if prev is None else float(np.abs(g - prev).mean()))
        prev = g
        if k in want:
            keep[k] = f
        if sample_step and k % sample_step == 0:
            thumbs.append((k / fps, cv2.resize(f, (tile_w, int(f.shape[0] * tile_w / f.shape[1])))))
        k += 1
    cap.release()
    return np.array(diffs), keep, thumbs, fps


def seam_and_sheets(od, segs, video):
    cuts = [s["end"] for s in segs[:-1]]
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    cap.release()
    cut_frames = [int(round(c * fps)) for c in cuts]
    want = [k + d for k in cut_frames for d in (-2, -1, 0, 1)]
    diffs, keep, thumbs, fps = read_sequential(video, want, sample_step=max(1, int(round(fps * SAMPLE_SEC))))
    med = float(np.median(diffs[1:])) if len(diffs) > 1 else 0.0
    seams = []
    for c, k in zip(cuts, cut_frames):
        v = float(diffs[max(1, k - 2):k + 3].max()) if k < len(diffs) else None
        seams.append({"cut": round(c, 2), "jump": round(v, 2) if v is not None else None,
                      "ratio": round(v / med, 2) if v is not None and med > 0 else None})
    # 接缝图
    rows = []
    for c, k in zip(cuts, cut_frames):
        cells = []
        for d in (-2, -1, 0, 1):
            f = keep.get(k + d)
            if f is None:
                continue
            t = cv2.resize(f, (240, int(f.shape[0] * 240 / f.shape[1])))
            cv2.putText(t, "%.2fs %+d" % (c, d), (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)
            cells.append(t)
        if len(cells) == 4:
            rows.append(np.hstack(cells))
    if rows:
        _imwrite(os.path.join(od, "seams.png"), np.vstack(rows))
    # 总览图
    sd = os.path.join(od, "sheets")
    os.makedirs(sd, exist_ok=True)
    for f in os.listdir(sd):
        if f.startswith("overview_"):
            os.remove(os.path.join(sd, f))
    sheets, per = [], int(SHEET_SEC / SAMPLE_SEC)
    starts = list(range(0, len(thumbs), per))
    if len(starts) > 1 and len(thumbs) - starts[-1] < per // 6:     # 最后不足十分之一张：并进上一张，不单独出图
        starts.pop()
    bounds = list(zip(starts, starts[1:] + [len(thumbs)]))
    for n, (i, j_end) in enumerate(bounds, 1):
        group = []
        for t, img in thumbs[i:j_end]:
            img = img.copy()
            cv2.rectangle(img, (0, 0), (img.shape[1], 24), (0, 0, 0), -1)
            tt = round(t, 1)                                       # 先取整再拆分，免得 59.98 秒显示成 0:60.0
            cv2.putText(img, "%d:%04.1f" % (int(tt // 60), tt % 60), (5, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            group.append(img)
        while len(group) % SHEET_COLS:
            group.append(np.zeros_like(group[0]))
        p = os.path.join(sd, "overview_%02d.jpg" % n)
        _imwrite(p, np.vstack([np.hstack(group[j:j + SHEET_COLS]) for j in range(0, len(group), SHEET_COLS)]))
        sheets.append(p)
    return seams, med, sheets


def run(workdir, video=None, quiet=False):
    od = os.path.join(workdir, "avatar")
    plan = json.load(open(os.path.join(od, "plan.json"), encoding="utf-8"))
    segs = C.final_segments(od, plan)                     # 成片时间轴上的起止（拼接时裁过开头）
    rp = os.path.join(od, "result.json")
    results = json.load(open(rp, encoding="utf-8")).get("results", {}) if os.path.isfile(rp) else {}
    video = video or os.path.join(od, "final_avatar.mp4")
    cam_rows, cam_anoms = camera_check(od, segs, results)
    seams, med, sheets = seam_and_sheets(od, segs, video) if os.path.isfile(video) else ([], 0.0, [])
    flags = ["镜头：%.1f–%.1f 秒（段%d）%s" % (a["start"], a["end"], a["segment"], a["detail"]) for a in cam_anoms]
    flags += ["接缝：%.2f 秒处跳变 %.1f，是全片相邻帧差中位数的 %.1f 倍（>%.0f）" % (x["cut"], x["jump"], x["ratio"], SEAM_RATIO)
              for x in seams if x["ratio"] and x["ratio"] > SEAM_RATIO]
    unknown = [r["i"] for r in cam_rows if r.get("status") == "无法判断"]
    out = {"video": video, "camera": cam_rows, "seams": seams, "frame_diff_median": round(med, 2), "sheets": sheets, "flags": flags}
    json.dump(out, open(os.path.join(od, "qc_frames.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ap = os.path.join(od, "anomalies.json")
    an = json.load(open(ap, encoding="utf-8")) if os.path.isfile(ap) else {"video": video, "anomalies": []}
    an["anomalies"] = sorted([x for x in an.get("anomalies", []) if x.get("type") != "camera"] + cam_anoms, key=lambda x: x["start"])
    json.dump(an, open(ap, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if not quiet:
        print("镜头（每段和它自己的第一帧比，缩放 1.00 = 没动）：")
        print("  " + "  ".join("段%d %s" % (r["i"], r["status"] if r.get("scale_min") is None else
                                            "%.2f–%.2f%s" % (r["scale_min"], r["scale_max"], "" if r["status"] == "正常" else " " + r["status"]))
                              for r in cam_rows))
        if unknown:
            print("  段%s 特征点太少，判断不了，要人看总览图。" % "、".join(map(str, unknown)))
        if seams:
            print("接缝跳变（全片相邻帧差中位数 %.1f）：%s" % (med, "  ".join("%.1fs:%.1f" % (x["cut"], x["jump"]) for x in seams if x["jump"] is not None)))
        for f in flags:
            print(" -", f)
        if sheets:
            print("全片总览图（每 0.5 秒一格；多手、手里多出东西、烧字幕要靠看这几张图）：")
            for p in sheets:
                print("  ", p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--video")
    a = ap.parse_args()
    out = run(a.workdir, a.video)
    _cli.report(issues=out["flags"], qc_json=os.path.join(a.workdir, "avatar", "qc_frames.json"), sheets=out["sheets"],
                seams=out["seams"], camera=out["camera"])


if __name__ == "__main__":
    _cli.run(main)
