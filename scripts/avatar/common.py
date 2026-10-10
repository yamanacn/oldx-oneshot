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

# ---------------------------------------------------------------- 固定参数
MAX_SEGMENT_SECONDS = 14.0       # 分段只设最长限制（用户定）
RESOLUTION_MEGAPIXELS = 1.0      # "768P" = 约 100 万像素：16:9 时为 1376×768
RATIOS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "3:4": (3, 4), "4:3": (4, 3), "21:9": (21, 9)}
SIZE_MIN, SIZE_MAX = 256, 2144   # 边长上限/下限，且必须是 32 的倍数

BASE = "https://www.runninghub.cn/openapi/v2"
DEFAULT_APP_ID = "2106047373926555650"            # 已内置的 AI 应用（创建者发布）：别的账号的 Key 可直接调用，用户只需提供 Key，不需要自己的应用 ID
NODE_AUDIO, NODE_IMG, NODE_PROMPT = "348", "114", "9008"          # 图片只传一次，应用内部自己分发
NODE_WIDTH, NODE_HEIGHT = "139", "140"
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
