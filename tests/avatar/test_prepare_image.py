"""图片预处理和风险预检（avatar/prepare_image.py）的离线测试。  python tests/avatar/test_prepare_image.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
PI = os.path.join(HERE, "..", "..", "scripts", "avatar", "prepare_image.py")
TMP = tempfile.mkdtemp(prefix="prep_test_")
cases = []


def check(name, ok, extra=""):
    print("✓" if ok else "✗", name, "" if ok else extra)
    cases.append(bool(ok))


def photo(w=900, h=1600, mode="RGB"):
    """有细节的假照片：彩色噪声 + 色块"""
    rng = np.random.default_rng(1)
    x = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
    x[h // 4:h // 2, w // 3:2 * w // 3] = (200, 160, 140)
    im = Image.fromarray(x)
    return im.convert(mode) if mode != "RGB" else im


def run(img, *extra):
    p = os.path.join(TMP, "in_%d.png" % len(os.listdir(TMP)))
    img.save(p)
    wd = tempfile.mkdtemp(dir=TMP)
    r = subprocess.run([sys.executable, PI, "--image", p, "--workdir", wd, *extra, "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        j = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
    except ValueError:
        j = {}
    return r, j


r, j = run(photo(), "--mouth-box", "0.44,0.40,0.56,0.45")
check("正常的图 + 嘴的位置：没有风险，输出 9:16 的 768×1376", r.returncode == 0 and j.get("risks") == [] and (j.get("width"), j.get("height")) == (768, 1376), j.get("risks"))
check("机读结果里有看图确认的清单", len(j.get("checklist", [])) == 4)
r, j = run(photo())
check("没给 --mouth-box：提示嘴部检查会不准", any("mouth-box" in x for x in j.get("risks", [])))
r, j = run(photo().filter(ImageFilter.GaussianBlur(6)), "--mouth-box", "0.44,0.40,0.56,0.45")
check("糊的图：报“偏糊”，next 让先告诉用户", any("偏糊" in x for x in j.get("risks", [])) and "告诉用户" in (j.get("next") or ""), j.get("risks"))
r, j = run(Image.fromarray((np.asarray(photo()) * 0.1).astype(np.uint8)), "--mouth-box", "0.44,0.40,0.56,0.45")
check("很暗的图：报“过暗”", any("过暗" in x for x in j.get("risks", [])), j.get("risks"))
rgba = photo(mode="RGBA")
a = np.asarray(rgba).copy()
a[:200, :, 3] = 0
r, j = run(Image.fromarray(a, "RGBA"), "--mouth-box", "0.44,0.40,0.56,0.45")
check("透明背景：报“透明背景”", any("透明背景" in x for x in j.get("risks", [])), j.get("risks"))
r, j = run(photo(300, 534), "--mouth-box", "0.44,0.40,0.56,0.45")
check("太小的图：报要放大", any("放大" in x for x in j.get("risks", [])), j.get("risks"))
r, j = run(photo(), "--mouth-box", "0.44,0.08,0.56,0.12")
check("嘴离上边太近：报头顶可能出画", any("上边" in x for x in j.get("risks", [])), j.get("risks"))
r, j = run(photo(), "--mouth-box", "0.80,0.40,0.82,0.41")
check("脸很小又靠边：两条都报", any("脸很小" in x for x in j.get("risks", [])) and any("不在画面中间" in x for x in j.get("risks", [])), j.get("risks"))
r, j = run(photo(), "--mouth-box", "0.6,0.4,0.5,0.45")
check("--mouth-box 写错：退出码 2", r.returncode == 2)

print("\n通过 %d/%d" % (sum(cases), len(cases)))
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if all(cases) else 1)
