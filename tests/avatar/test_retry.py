"""run_avatar.py 的重试与续跑逻辑：用模拟的云端测 10 种情况（不联网、不花钱）。  python tests/test_retry.py"""
import os, sys, json, shutil, tempfile, time, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts", "avatar"))
import common as C, run_avatar as R
R.time.sleep = lambda s: None          # 不真等
R.log = lambda m: LOG.append(m)
LOG = []

class Cloud:
    def __init__(self, **kw):
        self.kw = kw; self.submits = 0; self.downloads = 0; self.uploads = 0; self.polls = {}
    # --- 假的 C.upload / C.http_json / urlretrieve
    def upload(self, path, key):
        self.uploads += 1
        if self.kw.get("upload_fail_first") and self.uploads == 1:
            raise RuntimeError("上传失败 URLError")
        return "openapi/fake_" + os.path.basename(path)
    def http(self, url, payload, key, timeout=60):
        if "/run/ai-app/" in url:
            if self.kw.get("auth_fail"): return 401, {"message": "unauthorized"}
            self.submits += 1
            seq = self.kw.get("submit_replies", [])
            if self.submits <= len(seq):
                r = seq[self.submits - 1]; self.submits -= 1 if r.get("_retry") else 0
                if r.get("_retry"): self.kw["submit_replies"] = seq[1:]; return r["st"], r["body"]
            return 200, {"taskId": "T%d" % self.submits, "status": "RUNNING"}
        if url.endswith("/query"):
            tid = payload["taskId"]; n = self.polls[tid] = self.polls.get(tid, 0) + 1
            if self.kw.get("conn_errors") and n <= self.kw["conn_errors"]: return 0, {"message": "URLError"}
            if tid in self.kw.get("stale", ()): return 404, {"message": "not found"}
            if self.kw.get("never_finish"): return 200, {"status": "RUNNING"}
            fails = self.kw.get("fail_tasks", {})
            if tid in fails: return 200, {"status": "FAILED", "errorCode": fails[tid][0], "errorMessage": fails[tid][1], "failedReason": {}}
            if n < 2: return 200, {"status": "RUNNING"}
            return 200, {"status": "SUCCESS", "results": [{"outputType": "mp4", "url": "http://x/y.mp4"}], "usage": {"consumeMoney": "0.4", "taskCostTime": 100}}
    def retrieve(self, url, dst):
        self.downloads += 1
        if self.kw.get("download_fail_first") and self.downloads == 1: raise OSError("connection reset")
        open(dst, "wb").write(b"mp4")

def run(name, cloud, state=None, tries=1, timeout_min=1, expect=None):
    C.upload, C.http_json = cloud.upload, cloud.http
    urllib.request.urlretrieve = cloud.retrieve
    d = tempfile.mkdtemp(); st = state or R.State(os.path.join(d, "result.json"))
    seg = {"i": 1, "audio": "seg_01.wav", "duration": 9.0}
    LOG.clear(); err = None; res = None
    try:
        res = R.run_one(seg, "prompt", "img", 1376, 768, "k", "plus", d, timeout_min, tries, st, backoff=0)
    except Exception as e:
        err = e
    ok = expect(res, err, st, cloud) if expect else None
    print(("✓" if ok else "✗"), name, "| 提交%d 下载%d 上传%d" % (cloud.submits, cloud.downloads, cloud.uploads), "|", ("成功" if res else "异常: " + str(err)[:70]))
    return st, d

# A 提交被并发限制一次后成功；下载先失败一次——不应重新提交
run("A 提交遇并发限制→重试；下载失败→只重下", Cloud(submit_replies=[{"_retry": True, "st": 200, "body": {"status": "FAILED", "errorMessage": "并发数已满"}}], download_fail_first=True),
    expect=lambda r, e, s, c: r and c.downloads == 2 and c.submits == 1)
# B 第一次任务失败(805)，第二次成功
run("B 任务失败 805 → 自动重提交一次", Cloud(fail_tasks={"T1": ("805", "任务运行失败")}),
    expect=lambda r, e, s, c: r and c.submits == 2 and not s.d["pending"])
# C 余额不足：不可重试
run("C 余额不足 → 不重试直接停", Cloud(fail_tasks={"T1": ("900", "账户余额不足")}),
    expect=lambda r, e, s, c: e and "不可重试" in str(e) and c.submits == 1)
# D 连续失败两次（显存不足）→ 报告，不无限重试
run("D 连续失败两次 → 到次数上限后报告", Cloud(fail_tasks={"T1": ("805", "OOM"), "T2": ("805", "OOM")}),
    expect=lambda r, e, s, c: e and c.submits == 2)
# E 超时：保留任务号；再次运行续上同一任务
c = Cloud(never_finish=True)
st, d = run("E1 超时 → 保留任务号、不重新提交", c, timeout_min=0.0005,
    expect=lambda r, e, s, c: e and "已保留" in str(e) and c.submits == 1 and s.get("pending", 1)["task"] == "T1")
c.kw["never_finish"] = False
st2 = R.State(os.path.join(d, "result.json"))
run("E2 重跑 → 续上同一任务，不再提交", c, state=st2,
    expect=lambda r, e, s, c: r and c.submits == 1 and r["task"] == "T1" and not s.d["pending"])
# F 上传失败一次
run("F 上传音频失败一次 → 重试后继续", Cloud(upload_fail_first=True),
    expect=lambda r, e, s, c: r and c.uploads == 2 and c.submits == 1)
# G 查询连续连接错误几次
run("G 查询连接错误 5 次 → 容忍后成功", Cloud(conn_errors=5),
    expect=lambda r, e, s, c: r and c.submits == 1)
# H 旧任务号过期（404）→ 丢弃重新提交
d2 = tempfile.mkdtemp(); stH = R.State(os.path.join(d2, "result.json")); stH.set("pending", 1, {"task": "OLD"})
cH = Cloud(stale=("OLD",))
C.upload, C.http_json = cH.upload, cH.http; urllib.request.urlretrieve = cH.retrieve
r = R.run_one({"i": 1, "audio": "a.wav", "duration": 9}, "p", "img", 1376, 768, "k", "plus", d2, 1, 1, stH, backoff=0)
print("✓" if (cH.submits == 1 and r["task"] == "T1") else "✗", "H 旧任务查询不到(404) → 丢弃并重新提交 | 提交%d" % cH.submits)
# I 密钥被拒
run("I 提交时 401 → 直接抛密钥错误", Cloud(auth_fail=True),
    expect=lambda r, e, s, c: isinstance(e, PermissionError))
