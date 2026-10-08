"""开工前检查：RunningHub Key、会员档位（决定并发数）、应用 ID。只报来源，不显示密钥。

  python check_setup.py                  # 检查
  python check_setup.py --lock           # 把默认密钥文件设为仅当前用户可读
  python check_setup.py --set-plan 基础版Plus      # 记录会员档位，并发数自动对应
  python check_setup.py --set-concurrency 3        # 或直接给并发数
  python check_setup.py --set-app-id <应用ID>      # 用自己账号发布的应用

退出码：0 齐了；3 没有 Key（打印引导文案）；5 没有记录会员档位（打印档位表）。
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C


def lock():
    if not os.path.isfile(C.KEY_FILE):
        print("默认密钥文件不存在：", C.KEY_FILE)
        return 1
    if os.name == "nt":
        subprocess.run(["icacls", C.KEY_FILE, "/inheritance:r", "/grant:r", f"{os.environ.get('USERNAME', '')}:F"],
                       check=True, capture_output=True)
    else:
        os.chmod(C.KEY_FILE, 0o600)
    print("已限制为仅当前用户可读：", C.KEY_FILE)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", action="store_true")
    ap.add_argument("--set-plan")
    ap.add_argument("--set-concurrency", type=int)
    ap.add_argument("--set-app-id")
    a = ap.parse_args()
    if a.lock:
        sys.exit(lock())
    cfg = C.load_config()
    if a.set_plan:
        if a.set_plan not in C.PLAN_CONCURRENCY:
            sys.exit("没有这个档位：%s。可选：%s" % (a.set_plan, "、".join(C.PLAN_CONCURRENCY)))
        cfg["plan"], cfg["concurrency"] = a.set_plan, C.PLAN_CONCURRENCY[a.set_plan]
    if a.set_concurrency:
        cfg["concurrency"] = a.set_concurrency
        cfg.setdefault("plan", "自定义")
    if a.set_app_id:
        cfg["app_id"] = a.set_app_id
    if a.set_plan or a.set_concurrency or a.set_app_id:
        C.save_config(cfg)
        print("已保存配置：", json.dumps(cfg, ensure_ascii=False))
        return
    key, src = C.find_key()
    if not key:
        print(C.GUIDE_KEY)
        sys.exit(3)
    print(json.dumps({"key": {"found": True, "source": src},
                      "plan": cfg.get("plan"), "concurrency": cfg.get("concurrency"),
                      "app_id": C.app_id()}, ensure_ascii=False))
    if "concurrency" not in cfg:
        print("还没有记录会员档位：先问用户是哪一档，再用 --set-plan 保存（没记录时只按 1 路并发）。")
        print(C.PLAN_TABLE)
        sys.exit(5)


if __name__ == "__main__":
    main()
