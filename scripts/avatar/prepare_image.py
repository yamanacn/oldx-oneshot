"""处理用户的人物图片：确定画幅和尺寸（"768P"= 约 100 万像素），必要时中心裁剪。

  python prepare_image.py --image 我的照片.jpg --workdir <项目目录>

做法：
  1. 在 16:9 / 9:16 / 1:1 / 3:4 / 4:3 / 21:9 里选最接近图片比例的一档；
  2. 比例不完全一致时，按该比例做"最大范围的中心裁剪"（不拉伸、不留黑边）；
  3. 目标尺寸 = 该比例下约 100 万像素、边长取 32 的倍数（16:9 → 1376×768，9:16 → 768×1376，
     1:1 → 1024×1024，4:3 → 1184×896，3:4 → 896×1184，21:9 → 1568×672）；
  4. 输出 <workdir>/avatar/image.png（裁好的图）、image_info.json、image_crop_check.jpg（原图上画出裁剪框，
     供人工确认头部没被裁掉）。
图片比目标尺寸小会被放大，会在 warnings 里提示。头部是否被裁掉需要看 image_crop_check.jpg，脚本不做人脸检测。

提交前的风险预检（结果在 risks 里；有风险就告诉用户，让他换图或确认继续——生成出来救不回来）：
  * 偏糊（清晰度 < 30；实测正常的图 76–485）、过暗过亮（平均亮度 < 40 或 > 220）、对比太低；
  * 透明背景（PNG 的透明部分会变成黑底）；放大超过 1.5 倍；
  * 给了 --mouth-box 时：嘴离画面上边太近（头顶可能出画）、脸很小（远景，口型看不清）、人物明显不在中间（手势容易出画）；
  * 没给 --mouth-box：质检里的嘴部检查会不准。
头顶、双手、商品有没有被裁到，脚本看不出来，checklist 里列出要看 image_crop_check.jpg 确认的几项。
"""
import argparse
import json
import math
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _cli  # noqa: E402

from PIL import Image, ImageDraw, ImageOps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C


CHECKLIST = ["头顶上方留有空间、没被裁到", "两只手完整在画面里", "要展示的商品清楚、没被裁到", "嘴闭着、表情平静（不是的话 image_type 写非中性）"]


def image_risks(crop, alpha, upscale):
    """脚本能看出来的风险：糊、暗、亮、对比低、透明背景、放大太多"""
    import numpy as np
    g = np.asarray(crop.convert("L").resize((768, max(1, int(768 * crop.size[1] / crop.size[0])))), dtype=np.float32)
    lap = (g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]).var()
    out = []
    if lap < 30:
        out.append("图片偏糊（清晰度 %.0f，正常的图在 76 以上）：生成出来的人物会更糊，手和脸容易走样" % lap)
    if g.mean() < 40 or g.mean() > 220:
        out.append("图片%s（平均亮度 %.0f）：模型容易改动整体色调" % ("过暗" if g.mean() < 40 else "过亮", g.mean()))
    elif g.std() < 20:
        out.append("图片对比太低（%.0f）：人物和背景分不开，手的轮廓容易走样" % g.std())
    if alpha is not None and alpha.getextrema()[0] < 250:
        out.append("图片带透明背景：透明的部分会变成黑底。先换成不透明的图（例如铺上纯色背景）")
    if upscale > 1.5:
        out.append("原图太小，要放大 %.1f 倍：画质会明显变差，最好换一张更大的图" % upscale)
    return out


def nearest_ratio(w, h):
    r = w / h
    return min(C.RATIOS, key=lambda k: abs(math.log(r / (C.RATIOS[k][0] / C.RATIOS[k][1]))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--mouth-box", help="嘴部在（裁剪后）画面中的位置，占宽高的比例 x0,y0,x1,y1，例如 0.42,0.46,0.58,0.58；由看图的人给出，供成片质检定位嘴部")
    a = ap.parse_args()
    if not os.path.isfile(a.image):
        _cli.fail(_cli.USAGE, "找不到图片：" + a.image)
    raw = ImageOps.exif_transpose(Image.open(a.image))
    alpha = raw.getchannel("A") if raw.mode in ("RGBA", "LA") else (raw.convert("RGBA").getchannel("A") if raw.mode == "P" and "transparency" in raw.info else None)
    img = raw.convert("RGB")
    W, H = img.size
    name = nearest_ratio(W, H)
    rw, rh = C.RATIOS[name]
    target = rw / rh
    tw, th = C.size_for_ratio(name)
    if W / H > target:                        # 偏宽：裁左右
        cw, ch = int(round(H * target)), H
    else:                                     # 偏高：裁上下
        cw, ch = W, int(round(W / target))
    x0, y0 = (W - cw) // 2, (H - ch) // 2
    crop = img.crop((x0, y0, x0 + cw, y0 + ch))
    warnings = []
    lost = 1 - (cw * ch) / (W * H)
    if lost > 0.005:
        warnings.append("中心裁剪掉了 %.0f%% 的画面，请看 image_crop_check.jpg 确认头部、手没被裁到" % (lost * 100))
    if cw < tw or ch < th:
        warnings.append("原图（裁后 %d×%d）小于目标 %d×%d，会被放大，画质会差一些" % (cw, ch, tw, th))
    # 上传前把过大的图缩到目标的 2 倍以内，减少上传量（不影响最终 %d×%d 的输出）
    if cw > tw * 2:
        k = tw * 2 / cw
        crop = crop.resize((int(cw * k), int(ch * k)), Image.LANCZOS)
    od = os.path.join(a.workdir, "avatar")
    os.makedirs(od, exist_ok=True)
    crop.save(os.path.join(od, "image.png"))
    chk = img.copy()
    d = ImageDraw.Draw(chk)
    lw = max(3, W // 300)
    d.rectangle((x0, y0, x0 + cw - 1, y0 + ch - 1), outline=(255, 60, 60), width=lw)
    chk.thumbnail((1100, 1100))
    chk.save(os.path.join(od, "image_crop_check.jpg"), quality=88)
    info = {"source": os.path.abspath(a.image), "source_size": [W, H], "ratio": name, "width": tw, "height": th,
            "crop_box": [x0, y0, x0 + cw, y0 + ch], "cropped_fraction_lost": round(lost, 4),
            "uploaded_image": os.path.join(od, "image.png"), "uploaded_size": list(crop.size), "warnings": warnings}
    risks = image_risks(crop, alpha, max(tw / cw, th / ch))
    if a.mouth_box:
        mb = [float(x) for x in a.mouth_box.split(",")]
        if not (len(mb) == 4 and all(0 <= v <= 1 for v in mb) and mb[0] < mb[2] and mb[1] < mb[3]):
            _cli.fail(_cli.USAGE, "--mouth-box 格式：x0,y0,x1,y1，都在 0 到 1 之间，例如 0.42,0.46,0.58,0.58")
        info["mouth_box"] = mb
        cx, cy, mw = (mb[0] + mb[2]) / 2, (mb[1] + mb[3]) / 2, mb[2] - mb[0]
        if cy < 0.18:
            risks.append("嘴离画面上边太近（在 %.0f%% 高度）：头顶几乎贴着上边，点头、侧倾时头会出画" % (cy * 100))
        if mw < 0.025:
            risks.append("脸很小（嘴宽只占画面 %.1f%%）：远景下口型看不清，嘴部检查也不准" % (mw * 100))
        if not 0.3 <= cx <= 0.7:
            risks.append("人物明显不在画面中间（嘴在 %.0f%% 宽度处）：靠边那只手的手势容易出画" % (cx * 100))
    else:
        risks.append("没给 --mouth-box：成片质检里的嘴部检查会按人物居中估算，可能不准。看图后补上嘴的位置")
    info["risks"] = risks
    json.dump(info, open(os.path.join(od, "image_info.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("画幅 %s，输出尺寸 %d×%d（约 %.2f 百万像素）" % (name, tw, th, tw * th / 1048576))
    print("原图 %d×%d，裁剪框 (%d,%d)-(%d,%d)" % (W, H, x0, y0, x0 + cw, y0 + ch))
    for w in warnings:
        print("提示：", w)
    for r in risks:
        print("风险：", r)
    print("请查看：", os.path.join(od, "image_crop_check.jpg"))
    print("看图确认：" + "；".join(CHECKLIST))
    nxt = ("先把上面的风险告诉用户，让他换图或确认继续；确认后运行 python scripts/pipeline.py segments --workdir %s" % a.workdir
           if [r for r in risks if not r.startswith("没给 --mouth-box")] else None)
    _cli.report(image=info["uploaded_image"], image_info=os.path.join(od, "image_info.json"), crop_check=os.path.join(od, "image_crop_check.jpg"),
                ratio=name, width=tw, height=th, cropped_fraction_lost=info["cropped_fraction_lost"], warnings=warnings,
                risks=risks, checklist=CHECKLIST, **({"next": nxt} if nxt else {}))


if __name__ == "__main__":
    _cli.run(main)
