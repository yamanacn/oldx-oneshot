"""处理用户的口播图片：确定画幅和尺寸（"768P"= 约 100 万像素），必要时中心裁剪。

  python prepare_image.py --image 我的照片.jpg --workdir <项目目录>

做法：
  1. 在 16:9 / 9:16 / 1:1 / 3:4 / 4:3 / 21:9 里选最接近图片比例的一档；
  2. 比例不完全一致时，按该比例做"最大范围的中心裁剪"（不拉伸、不留黑边）；
  3. 目标尺寸 = 该比例下约 100 万像素、边长取 32 的倍数（16:9 → 1376×768，9:16 → 768×1376，
     1:1 → 1024×1024，4:3 → 1184×896，3:4 → 896×1184，21:9 → 1568×672）；
  4. 输出 <workdir>/avatar/image.png（裁好的图）、image_info.json、image_crop_check.jpg（原图上画出裁剪框，
     供人工确认头部没被裁掉）。
图片比目标尺寸小会被放大，会在 warnings 里提示。头部是否被裁掉需要看 image_crop_check.jpg，脚本不做人脸检测。
"""
import argparse
import json
import math
import os
import sys

from PIL import Image, ImageDraw, ImageOps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C


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
        sys.exit("找不到图片：" + a.image)
    img = ImageOps.exif_transpose(Image.open(a.image)).convert("RGB")
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
    if a.mouth_box:
        mb = [float(x) for x in a.mouth_box.split(",")]
        assert len(mb) == 4 and all(0 <= v <= 1 for v in mb) and mb[0] < mb[2] and mb[1] < mb[3], "--mouth-box 格式：x0,y0,x1,y1，0 到 1"
        info["mouth_box"] = mb
    json.dump(info, open(os.path.join(od, "image_info.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("画幅 %s，输出尺寸 %d×%d（约 %.2f 百万像素）" % (name, tw, th, tw * th / 1048576))
    print("原图 %d×%d，裁剪框 (%d,%d)-(%d,%d)" % (W, H, x0, y0, x0 + cw, y0 + ch))
    for w in warnings:
        print("提示：", w)
    print("请查看：", os.path.join(od, "image_crop_check.jpg"))


if __name__ == "__main__":
    main()
