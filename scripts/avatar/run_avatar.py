"""把各段提交到 RunningHub 的数字人应用，按会员并发数并行；有完整的重试与断点续跑。

  python run_avatar.py --workdir <项目目录>                  # 只做检查和预览，不联网、不花钱
  python run_avatar.py --workdir <项目目录> --yes            # 上传文件并提交（会产生费用）
  python run_avatar.py --workdir <项目目录> --yes --only 2   # 只重跑第 2 段，其余沿用已有结果

并发数 = min(段数, 会员档位对应并发)，档位用 check_setup.py --set-plan 记录；没记录按 1 路。
超出并发的段在本地排队。实例默认 plus（48G，用户已授权）；ultra 需要显式 --instance ultra，本技能不会自行升级。

重试与续跑（每一层各管各的，避免重复计费）：
  * 上传、下载：各自重试 3 次（2/4/8 秒退避）。云端已成功时，下载失败只重下，**绝不重新提交**。
  * 提交：遇到并发/排队/限流类拒绝（含 HTTP 429、连接错误）就等 20 秒再提交，最多 12 次。
  * 轮询：连接错误容忍约 3 分钟；超时（--timeout-min）后**不重新提交**，任务号保留在 result.json 的 pending 里，
    再次运行本脚本会继续等同一个任务。
  * 任务失败：先判断类型。余额不足、参数/权限错误这类重试也没用，直接停下并报告；其余（含显存不足的 805）
    退避 15 秒后重提交，最多 --retries 次（默认 1，失败任务不计费）。
  * 任务号一提交就写入 result.json 的 pending；脚本被中断后重跑，会先续上这些任务，不会全部重新提交。
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

lock = threading.Lock()
NON_RETRYABLE = re.compile(r"余额不足|余额|欠费|RH币不足|insufficient|balance|arrears|参数错误|invalid ?param|未授权|无权限|unauthorized|forbidden", re.I)
RATE_LIMIT_WORDS = ("并发", "排队", "队列", "限流", "concurr", "queue", "limit", "频繁")


def log(msg):
    with lock:
        print(time.strftime("%H:%M:%S"), msg, flush=True)


class State:
    """result.json：results（已完成）、errors、pending（已提交未完成的任务号）。每次变化立即落盘。"""

    def __init__(self, path):
        self.path, self.lk = path, threading.Lock()
        self.d = {"results": {}, "errors": {}, "pending": {}}
        if os.path.isfile(path):
            self.d.update(json.load(open(path, encoding="utf-8")))
            for k in ("results", "errors", "pending"):
                self.d.setdefault(k, {})

    def _save(self):
        json.dump(self.d, open(self.path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    def set(self, section, i, value):
        with self.lk:
            self.d[section][str(i)] = value
            self._save()

    def drop(self, section, i):
        with self.lk:
            self.d[section].pop(str(i), None)
            self._save()

    def get(self, section, i):
        return self.d[section].get(str(i))


def with_retry(fn, what, tries=3, base=2.0):
    last = None
    for k in range(tries):
        try:
            return fn()
        except PermissionError:
            raise
        except Exception as e:
            last = e
            if k < tries - 1:
                wait = base * (2 ** k)
                log("%s失败（%s），%.0f 秒后重试（%d/%d）" % (what, str(e)[:80], wait, k + 1, tries - 1))
                time.sleep(wait)
    raise RuntimeError("%s重试 %d 次仍失败：%s" % (what, tries, last))


def submit(i, body, key):
    for _ in range(12):
        st, r = C.http_json("%s/run/ai-app/%s" % (C.BASE, C.app_id()), body, key)
        task = r.get("taskId")
        if task and r.get("status") != "FAILED":
            return task
        msg = json.dumps({k: r.get(k) for k in ("status", "errorCode", "errorMessage", "message")}, ensure_ascii=False)
        if st in (401, 403):
            raise PermissionError("密钥被拒绝（HTTP %s）" % st)
        if NON_RETRYABLE.search(msg):
            raise RuntimeError("[不可重试] 提交被拒：" + msg[:300])
        if st in (0, 429) or any(k in msg for k in RATE_LIMIT_WORDS):
            log("[段%d] 暂时不能提交（%s），20 秒后重试" % (i, msg[:80]))
            time.sleep(20)
            continue
        raise RuntimeError("提交失败：" + msg[:300])
    raise RuntimeError("多次提交都被拒绝（并发/排队）")


def poll(i, task, key, timeout_s, interval=10, max_conn_errors=18):
    """返回 ("SUCCESS"|"FAILED"|"TIMEOUT"|"STALE", query_json)。连接错误累计超限抛 RuntimeError（任务保留）。"""
    t0, last, conn = time.time(), None, 0
    while time.time() - t0 < timeout_s:
        st, q = C.http_json(C.BASE + "/query", {"taskId": task}, key)
        if st in (401, 403):
            raise PermissionError("密钥被拒绝（HTTP %s）" % st)
        if st == 0 or st >= 500 or st == 429:
            conn += 1
            if conn > max_conn_errors:
                raise RuntimeError("连续 %d 次查询失败，任务 %s 可能仍在云端，重跑本脚本会继续查它" % (conn, task))
            time.sleep(interval)
            continue
        if st != 200 and 400 <= st < 500:
            return "STALE", q                      # 任务号不存在或已过期
        conn = 0
        s = q.get("status")
        if s != last:
            log("[段%d] 状态 %s" % (i, s))
            last = s
        if s in ("SUCCESS", "FAILED"):
            return s, q
        time.sleep(interval)
    return "TIMEOUT", None


def run_one(seg, prompt, img_val, w, h, key, instance, out_dir, timeout_min, tries, state, interval=10, backoff=15.0):
    i = seg["i"]
    for attempt in range(1, tries + 2):
        pend = state.get("pending", i)
        if pend and pend.get("task"):
            task = pend["task"]
            log("[段%d] 续上上次已提交的任务 %s" % (i, task))
        else:
            aud = with_retry(lambda: C.upload(seg["audio"], key), "[段%d] 上传音频" % i)
            body = {"nodeInfoList": [
                {"nodeId": C.NODE_AUDIO, "fieldName": "audio", "fieldValue": aud, "description": "音频"},
                {"nodeId": C.NODE_IMG, "fieldName": "image", "fieldValue": img_val, "description": "口播图"},
                {"nodeId": C.NODE_PROMPT, "fieldName": "string", "fieldValue": prompt, "description": "提示词"},
                {"nodeId": C.NODE_WIDTH, "fieldName": "value", "fieldValue": str(w), "description": "宽"},
                {"nodeId": C.NODE_HEIGHT, "fieldName": "value", "fieldValue": str(h), "description": "高"}],
                "instanceType": instance, "usePersonalQueue": "false"}
            task = submit(i, body, key)
            state.set("pending", i, {"task": task, "instance": instance, "submitted_at": time.strftime("%Y-%m-%d %H:%M:%S")})
            log("[段%d] 已提交 task %s（第 %d 次）" % (i, task, attempt))
        outcome, q = poll(i, task, key, timeout_min * 60, interval)
        if outcome == "SUCCESS":
            mp4 = [x for x in (q.get("results") or []) if x.get("outputType") == "mp4" and x.get("url")]
            if not mp4:
                state.drop("pending", i)
                raise RuntimeError("[不可重试] 任务成功但没有 mp4 结果，task %s" % task)
            dst = os.path.join(out_dir, "seg_%02d.mp4" % i)
            # 云端已成功（已计费）：下载失败只重下，绝不重新提交
            with_retry(lambda: urllib.request.urlretrieve(mp4[0]["url"], dst), "[段%d] 下载视频" % i, tries=4)
            log("[段%d] 已保存 %s" % (i, dst))
            res = {"i": i, "task": task, "file": dst, "usage": q.get("usage")}
            state.set("results", i, res)
            state.drop("pending", i)
            state.drop("errors", i)
            return res
        if outcome == "TIMEOUT":
            raise RuntimeError("[已保留] 超时（%d 分钟），task %s 可能还在云端，重跑本脚本会继续等它，不会重复提交" % (timeout_min, task))
        if outcome == "STALE":
            log("[段%d] 旧任务 %s 查询不到，丢弃后重新提交" % (i, task))
            state.drop("pending", i)
            continue
        # FAILED
        state.drop("pending", i)
        reason = "errorCode=%s %s | %s" % (q.get("errorCode"), q.get("errorMessage"), json.dumps(q.get("failedReason"), ensure_ascii=False)[:300])
        log("[段%d] 任务失败：%s" % (i, reason[:200]))
        if NON_RETRYABLE.search(reason):
            raise RuntimeError("[不可重试] " + reason)
        if attempt <= tries:
            log("[段%d] %.0f 秒后重新提交（失败任务不计费）" % (i, backoff))
            time.sleep(backoff)
            continue
        raise RuntimeError("任务失败，已重试 %d 次：%s" % (tries, reason))
    raise RuntimeError("段%d 没有得到结果" % i)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--instance", default=C.DEFAULT_INSTANCE, choices=["default", "plus", "ultra"])
    ap.add_argument("--concurrency", type=int)
    ap.add_argument("--only", type=int, nargs="*")
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--timeout-min", type=int, default=45)
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    od = os.path.join(a.workdir, "avatar")
    plan = json.load(open(os.path.join(od, "plan.json"), encoding="utf-8"))
    info = json.load(open(os.path.join(od, "image_info.json"), encoding="utf-8"))
    segs = plan["segments"]
    prompts = {}
    for s in segs:
        p = os.path.join(od, "prompts", "segment_%02d.txt" % s["i"])
        if not os.path.isfile(p):
            sys.exit("缺少提示词：%s（先运行 build_prompts.py）" % p)
        prompts[s["i"]] = open(p, encoding="utf-8").read()
    w, h = info["width"], info["height"]
    conc = min(C.concurrency(a.concurrency), len(segs))
    state = State(os.path.join(od, "result.json"))
    if a.only is not None:                                 # 指定重跑：丢掉这些段的旧结果和旧任务
        for i in a.only:
            state.drop("results", i)
            state.drop("pending", i)
            state.drop("errors", i)
    done = {int(k): v for k, v in state.d["results"].items() if os.path.isfile(v["file"])}
    todo = [s for s in segs if s["i"] not in done and (a.only is None or s["i"] in a.only)]
    resumable = [s["i"] for s in todo if state.get("pending", s["i"])]
    cfg = C.load_config()
    print("尺寸 %d×%d，实例 %s，应用 %s" % (w, h, a.instance, C.app_id()))
    print("会员档位：%s，并发 %d（共 %d 段，本次处理 %d 段，沿用已有 %d 段）" % (cfg.get("plan", "未记录"), conc, len(segs), len(todo), len(done)))
    if resumable:
        print("其中段%s 有上次已提交、未完成的云端任务，会先续上它们，不会重复提交。" % "、".join(map(str, resumable)))
    if "concurrency" not in cfg and a.concurrency is None:
        print("提示：还没记录会员档位，只按 1 路并发；可用 check_setup.py --set-plan 记录。")
    for s in todo:
        print("  段%d：%.1f 秒  提示词 %d 字  %s" % (s["i"], s["duration"], len(prompts[s["i"]]), os.path.basename(s["audio"])))
    if a.instance == "ultra":
        print("注意：ultra 未经用户授权，请确认用户同意后再用。")
    if not a.yes:
        print("[预览] 没有联网，也没有产生费用。确认后加 --yes。")
        return
    if not todo:
        print("没有需要提交的段。")
        return
    key = C.require_key()
    os.makedirs(os.path.join(od, "clips"), exist_ok=True)
    img_val = with_retry(lambda: C.upload(info["uploaded_image"], key), "上传图片")
    log("图片已上传")
    errors = {}
    with ThreadPoolExecutor(max_workers=max(1, conc)) as ex:
        futs = {ex.submit(run_one, s, prompts[s["i"]], img_val, w, h, key, a.instance,
                          os.path.join(od, "clips"), a.timeout_min, a.retries, state): s["i"] for s in todo}
        for f, i in futs.items():
            try:
                f.result()
            except PermissionError as e:
                print(str(e), "请重新获取 Key。")
                sys.exit(4)
            except Exception as e:
                errors[i] = str(e)
                state.set("errors", i, str(e))
    results = {int(k): v for k, v in state.d["results"].items()}
    total, known = 0.0, 0
    print("\n=== 汇总 ===")
    for i in sorted(results):
        u = results[i].get("usage") or {}
        cost = u.get("consumeMoney")
        try:
            if cost not in (None, ""):
                total += float(cost)
                known += 1
        except ValueError:
            pass
        print("段%d：成功 费用=%s 用时=%ss" % (i, cost if cost not in (None, "") else "接口没返回", u.get("taskCostTime")))
    for i in sorted(errors):
        print("段%d：失败 → %s" % (i, errors[i][:300]))
    if results and known == 0:      # 有的账号或应用的接口不返回费用：合计不是 0 元，是不知道
        print("本次费用：接口没有返回费用，不能按 0 元算——请在 RunningHub 后台查看。")
    elif known < len(results):
        print("本次已知费用合计 = %s（只有 %d/%d 段返回了费用，实际更多，以后台为准）" % (round(total, 4), known, len(results)))
    else:
        print("本次已知费用合计 =", round(total, 4))
    if errors:
        print("有失败的段，未拼接。排查后重跑本脚本即可：已成功的段不会重复提交，仍在云端的任务会被续上；"
              "想强制重做某段用 --only <段号>。不要自行换更大的实例。")
        sys.exit(1)


if __name__ == "__main__":
    main()
