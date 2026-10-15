"""数字人阶段的公共部分：固定参数、密钥与配置、HTTP、上传。

密钥只在内存里使用：任何地方都不打印、不写日志，只报告来源。
"""
import json
import math
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid

# ---------------------------------------------------------------- 音频核对
def verify_plan_audio(workdir, plan, legacy=False):
    """分段之后音频不许再改：每段 seg_NN.wav 和整条 merged.wav 都要和 plan.json 记下的指纹一致，否则退出码 1。
    plan.json 没有指纹（旧版本分段的，或者被手工改过）时也停下；legacy=True（旧项目，--legacy）才跳过。"""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    import _util
    redo = "然后重新分段：plan_segments.py（提示词按新分段重写，再跑 build_prompts.py）。"
    ok = _util.check_audio(os.path.join(workdir, "out", "merged.wav"), plan.get("audio_sha"), plan.get("total_seconds"), "分段", redo)
    for s in plan["segments"]:
        ok = _util.check_audio(s["audio"], s.get("audio_sha"), s.get("duration"), "分段", redo) or ok
    if not ok:
        if not legacy:
            import _cli
            _cli.report(next="python scripts/avatar/plan_segments.py --workdir %s" % workdir)
            _cli.fail(_cli.ERROR, "plan.json 里没有音频指纹：它不是现在的 plan_segments.py 写出来的（旧版本，或者被手工改过）。"
                      "重新运行 plan_segments.py（提示词按新分段重写）；确定是旧项目要沿用，加 --legacy。不要手工编辑时间文件。")
        print("注意：--legacy：plan.json 没有音频指纹，跳过“音频是否被改过”的核对。")
    return ok


# ---------------------------------------------------------------- 成片时间轴
def final_segments(od, plan):
    """各段在成片 final_avatar.mp4 上的起止。拼接时每段开头裁掉过几帧（finalize.json），成片就比配音短，
    分段计划里的时间（配音的时间轴）要换算过来；没有 finalize.json（旧项目、没裁）时和 plan.json 一样。
    每段多一个 head：这一段的片段开头被裁掉了多少秒（片段内时间 = 成片时间 - start + head）。"""
    fp = os.path.join(od, "finalize.json")
    fin = {}
    if os.path.isfile(fp):
        try:
            fin = {x["i"]: x for x in json.load(open(fp, encoding="utf-8")).get("segments", [])}
        except (OSError, ValueError):
            fin = {}
    out = []
    for s in plan["segments"]:
        f = fin.get(s["i"])
        if f:
            out.append(dict(s, start=f["start"], end=f["end"], duration=f["duration"], head=f["head"]))
        else:
            out.append(dict(s, head=0.0))
    return out


# ---------------------------------------------------------------- 固定参数
MAX_SEGMENT_SECONDS = 14.0       # 分段只设最长限制（用户定：每段 14 秒以内；18 秒时段的后半容易走样、多手）
RESOLUTION_MEGAPIXELS = 1.0      # "768P" = 约 100 万像素：16:9 时为 1376×768
RATIOS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "3:4": (3, 4), "4:3": (4, 3), "21:9": (21, 9)}
SIZE_MIN, SIZE_MAX = 256, 2144   # 边长上限/下限，且必须是 32 的倍数

BASE = "https://www.runninghub.cn/openapi/v2"
DEFAULT_APP_ID = "2106047373926555650"            # 已内置的 AI 应用（创建者发布）：别的账号的 Key 可直接调用，用户只需提供 Key，不需要自己的应用 ID
NODE_AUDIO, NODE_IMG, NODE_PROMPT = "348", "114", "9008"          # 图片只传一次，应用内部自己分发
NODE_WIDTH, NODE_HEIGHT = "139", "140"
NODE_SEED = "131"                                 # 随机种子（fieldName 为 noise_seed）；2026-10-13 工作流新增的输入
DEFAULT_SEED = 42                                 # 不指定时用它：同样的输入 + 同样的种子，结果可以重现
DEFAULT_INSTANCE = "plus"                         # 48G；用户授权过。ultra(84G) 未授权，不自行使用

HOME_DIR = os.path.join(os.path.expanduser("~"), ".runninghub")
KEY_FILE = os.path.join(HOME_DIR, "api_key")
CONFIG_FILE = os.path.join(HOME_DIR, "config.json")

# RunningHub 各会员档位的并发数（档位可能变，以官网为准）
PLAN_CONCURRENCY = {
    "免费": 1, "轻享版": 1, "轻享版Plus": 1, "基础版": 2, "基础版Plus": 3,
    "专业版": 3, "专业版Plus": 5, "Max": 5,   # Max 页面写"20并发（消费级 API KEY：5并发）"，这里用 API KEY 的 5
}

GUIDE_KEY = ("本机没有 RunningHub 的 API Key。请获取后，直接粘贴到对话输入框发送给我，其余的我来处理。\n"
             "获取方式：使用专属邀请链接注册 https://www.runninghub.cn?inviteCode=150e26b6 ，可以额外获得 1000 RH 币。\n"
             "注册后登录，在页面上方选择“API”，在 API 页面点击左上方的“获取密钥”，再在密钥页面新建密钥，复制密钥粘贴过来即可。")
PLAN_TABLE = ("RunningHub 各档会员的并发数：免费 1、轻享版 1、轻享版 Plus 1、基础版 2、基础版 Plus 3、"
              "专业版 3、专业版 Plus 5、Max 20（用 API KEY 时为 5）。")

WORKSPACE_ROOT = os.environ.get("DH_WORKSPACE", "").strip() or os.path.join(os.path.expanduser("~"), ".digital-human")


# ---------------------------------------------------------------- 尺寸
def snap32(v):
    return min(SIZE_MAX, max(SIZE_MIN, int(round(v / 32.0)) * 32))


def size_for_ratio(ratio_name, megapixels=RESOLUTION_MEGAPIXELS):
    rw, rh = RATIOS[ratio_name]
    g = math.sqrt(megapixels * 1024 * 1024 / (rw * rh))
    return snap32(rw * g), snap32(rh * g)


# ---------------------------------------------------------------- 配置与密钥
def load_config():
    if os.path.isfile(CONFIG_FILE):
        return json.load(open(CONFIG_FILE, encoding="utf-8"))
    return {}


def save_config(cfg):
    os.makedirs(HOME_DIR, exist_ok=True)
    json.dump(cfg, open(CONFIG_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def app_id():
    return os.environ.get("RH_APP_ID", "").strip() or load_config().get("app_id") or DEFAULT_APP_ID


def workflow():
    """云端工作流的版本标签（用户改过工作流时记一个），返回 (标签, 记录时间)。没记过返回 ("未记录", None)。"""
    w = load_config().get("workflow") or {}
    return (w.get("label") or "未记录"), w.get("set_at")


def concurrency(override=None):
    if override:
        return max(1, int(override))
    return max(1, int(load_config().get("concurrency", 1)))     # 没配置过按 1 路，宁慢不失败


def find_key():
    k = os.environ.get("RUNNINGHUB_API_KEY", "").strip()
    if k:
        return k, "环境变量 RUNNINGHUB_API_KEY"
    f = os.environ.get("RH_KEY_FILE", "").strip()
    if f and os.path.isfile(f):
        k = open(f, encoding="utf-8").read().strip()
        if k:
            return k, "RH_KEY_FILE 指向的文件"
    if os.path.isfile(KEY_FILE):
        k = open(KEY_FILE, encoding="utf-8").read().strip()
        if k:
            return k, "默认密钥文件 " + KEY_FILE
    return None, None


def require_key():
    k, _ = find_key()
    if not k:
        print(GUIDE_KEY)
        sys.exit(3)
    return k


# ---------------------------------------------------------------- HTTP
def http_json(url, payload, key, timeout=60):
    """返回 (status, data)；status=0 表示连接层错误（可重试）。"""
    req = urllib.request.Request(url, json.dumps(payload).encode("utf-8"),
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
    except Exception as e:
        return 0, {"message": type(e).__name__}


# ---------------------------------------------------------------- 费用
REF_COINS_PER_SEC = 10.0      # 没有本机记录时用的参考值：2026-10 新模型实测 56 段，中位 10.0 币/秒配音（7.0–13.2）


def cost_history():
    """本机做过的项目里，每秒配音实际花了多少：返回 {"coins": [每秒币数...], "money": [每秒元...]}。
    费用来自云端返回的 usage（新模型返回 RH 币 consumeCoins，旧模型返回人民币 consumeMoney）。"""
    import glob
    out = {"coins": [], "money": []}
    for rp in glob.glob(os.path.join(WORKSPACE_ROOT, "projects", "*", "avatar", "result.json")):
        try:
            res = json.load(open(rp, encoding="utf-8")).get("results", {})
            plan = json.load(open(os.path.join(os.path.dirname(rp), "plan.json"), encoding="utf-8"))
        except (OSError, ValueError):
            continue
        dur = {str(s["i"]): s["duration"] for s in plan.get("segments", [])}
        for k, v in res.items():
            u, sec = v.get("usage") or {}, v.get("seconds") or dur.get(k)
            if not sec:
                continue
            for field, bucket in (("consumeCoins", "coins"), ("consumeMoney", "money")):
                try:
                    if u.get(field) not in (None, ""):
                        out[bucket].append(float(u[field]) / sec)
                except (TypeError, ValueError):
                    pass
    return out


def cost_estimate(seconds):
    """按本机实测估算这次要花多少 RH 币：(低, 中, 高, 依据)。"""
    h = cost_history()["coins"]
    if len(h) >= 5:
        h = sorted(h)
        lo, mid, hi = h[len(h) // 10], h[len(h) // 2], h[min(len(h) - 1, len(h) * 9 // 10)]
        basis = "按本机最近 %d 段的实际费用（每秒配音 %.1f–%.1f 币，中位 %.1f）" % (len(h), lo, hi, mid)
    else:
        lo, mid, hi = REF_COINS_PER_SEC * 0.7, REF_COINS_PER_SEC, REF_COINS_PER_SEC * 1.3
        basis = "本机记录不够，按参考值每秒配音约 %.0f 币" % REF_COINS_PER_SEC
    return round(lo * seconds), round(mid * seconds), round(hi * seconds), basis


def upload(path, key):
    """上传文件，返回 data.fileName（用作 fieldValue）。失败抛 RuntimeError。"""
    boundary = uuid.uuid4().hex
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f'Content-Type: {ctype}\r\n\r\n').encode() + open(path, "rb").read() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + "/media/upload/binary", body,
                                 {"Content-Type": "multipart/form-data; boundary=" + boundary,
                                  "Authorization": "Bearer " + key})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=300))
    except urllib.error.HTTPError as e:
        raise RuntimeError("上传失败 HTTP %s" % e.code)
    except Exception as e:
        raise RuntimeError("上传失败 " + type(e).__name__)
    if r.get("code") != 0:
        raise RuntimeError("上传失败：" + json.dumps(r, ensure_ascii=False)[:200])
    return r["data"]["fileName"]
