"""查本机有没有百炼 API Key（只报来源，不显示内容）。

  python check_key.py            # 有：打印来源，退出码 0；没有：打印引导文案，退出码 3
  python check_key.py --lock     # 把默认密钥文件设为仅当前用户可读（Windows 用 icacls）

保存新密钥的做法见 SKILL.md：用户同意后，用 Write 工具写入默认密钥文件，再运行 --lock。
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C


def lock():
    p = C.DEFAULT_KEY_FILE
    if not os.path.isfile(p):
        print("默认密钥文件不存在：", p)
        return 1
    if os.name == "nt":
        user = os.environ.get("USERNAME", "")
        subprocess.run(["icacls", p, "/inheritance:r", "/grant:r", f"{user}:F"], check=True, capture_output=True)
    else:
        os.chmod(p, 0o600)
    print("已限制为仅当前用户可读：", p)
    return 0


def main():
    if "--lock" in sys.argv:
        sys.exit(lock())
    key, src = C.find_key()
    if key:
        print(json.dumps({"found": True, "source": src, "length": len(key)}, ensure_ascii=False))
        sys.exit(0)
    print(C.GUIDE_TEXT)
    sys.exit(3)


if __name__ == "__main__":
    main()
