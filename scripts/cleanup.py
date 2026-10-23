"""清理项目目录里用不上的文件，给磁盘减负。默认只预览（列出能清什么、多大），加 --yes 才删。不联网，不花钱。

  python scripts/cleanup.py --workdir <项目目录>                  # 预览这个项目
  python scripts/cleanup.py --workdir <项目目录> --yes            # 删第 1 类（随时能免费重新生成的）
  python scripts/cleanup.py --all                                  # 预览所有项目，按能清的大小排
  python scripts/cleanup.py --all --yes                            # 所有项目都删第 1 类
  python scripts/cleanup.py --workdir <项目目录> --cache --yes     # 另外删配音缓存
  python scripts/cleanup.py --workdir <项目目录> --takes --yes     # 另外删人物视频的历史版本
  python scripts/cleanup.py --workdir <项目目录> --archive avatar_旧版 --archive out_v2 --yes   # 删预览里列出的实验目录

三类：
  1 随时能免费重新生成（--yes 就删）：
      质检和检查图（avatar/sheets/、seams.png、mouth_strip.png、zoom*.jpg、image_crop_check.jpg、subtitles_check.jpg、jumpcut_points.jpg），
      旧的配音版本 out_prev/（配音缓存还在，用原来的设置重新合成不花钱），
      包装渲出的静帧和样段（package/out/ 里除 main.mp4 之外的文件；成片已经复制到 final/）。
  2 删了要再花钱、或者找不回来（要加对应参数，先问用户）：
      配音缓存 cache/（--cache：之后再合成要重新付配音的钱，金额很少），
      人物视频的历史版本 avatar/takes/（--takes：花钱生成的旧片段，删了就不能用 takes.py 换回）。
  3 手工留的实验目录和旧成片（avatar_*、out_*、package_*、avatar/clips_*、avatar/final_avatar_*.mp4 这类）：
      只列出名字和大小；用户点名才删（--archive 名字，路径相对项目目录）。
从不删：文案、音色、配音和逐词时间、分段计划和各段音频、提示词、当前片段 clips/、result.json、
       各版成片（final_avatar / final_jumpcut / final_subtitled.mp4、final/）、字幕文件、assets/。
目录里的 node_modules 是指向共用渲染依赖的链接：删目录时只拆这个链接，不进去删共用的依赖。
"""
import argparse
import glob
import os
import shutil
import stat
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _cli  # noqa: E402

ROOT = os.environ.get("DH_WORKSPACE", "").strip() or os.path.join(os.path.expanduser("~"), ".digital-human")
PROJECTS = os.path.join(ROOT, "projects")

ROOT_STD = {"cache", "out", "out_prev", "avatar", "package", "final", "assets"}           # 项目根目录下的标准子目录
AVATAR_STD_DIRS = {"clips", "prompts", "takes", "sheets"}
AVATAR_STD_VIDEOS = {"final_avatar.mp4", "final_jumpcut.mp4", "final_subtitled.mp4"}
CHECK_IMAGES = ["sheets", "seams.png", "mouth_strip.png", "zoom*.jpg", "image_crop_check.jpg", "subtitles_check.jpg",
                "jumpcut_points.jpg"]


def is_link(p):
    """符号链接，或 Windows 的目录联接（junction）：都只能拆链接，不能顺着进去删"""
    try:
        st = os.lstat(p)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)   # 0x400 = REPARSE_POINT


def size_of(p):
    if is_link(p):
        return 0
    if os.path.isfile(p):
        return os.path.getsize(p)
    total = 0
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if not is_link(os.path.join(root, d))]
        total += sum(os.path.getsize(os.path.join(root, f)) for f in files if not is_link(os.path.join(root, f)))
    return total


def remove(p):
    """删文件或目录；遇到链接只拆链接本身"""
    if is_link(p):
        try:
            os.unlink(p)
        except (IsADirectoryError, PermissionError, OSError):
            os.rmdir(p)                                    # Windows 的目录联接要用 rmdir 拆
        return
    if os.path.isfile(p):
        os.remove(p)
        return
    for e in os.scandir(p):
        remove(e.path)
    os.rmdir(p)


def mb(n):
    return round(n / 1048576, 1)


def scan(wd):
    """一个项目里能清的东西：{类别: [(相对路径, 字节)]}"""
    out = {"regenerable": [], "cache": [], "takes": [], "archives": []}
    od = os.path.join(wd, "avatar")

    def add(cat, p):
        if os.path.lexists(p):
            out[cat].append((os.path.relpath(p, wd), size_of(p)))

    for pat in CHECK_IMAGES:
        for p in sorted(glob.glob(os.path.join(od, pat))):
            add("regenerable", p)
    add("regenerable", os.path.join(wd, "out_prev"))
    for pkg in sorted(glob.glob(os.path.join(wd, "package*"))):
        for p in sorted(glob.glob(os.path.join(pkg, "out", "*"))):
            if os.path.basename(p) != "main.mp4":
                add("regenerable", p)
    add("cache", os.path.join(wd, "cache"))
    add("takes", os.path.join(od, "takes"))
    for name in sorted(os.listdir(wd)) if os.path.isdir(wd) else []:
        p = os.path.join(wd, name)
        if os.path.isdir(p) and not is_link(p) and name not in ROOT_STD and not name.startswith("."):
            add("archives", p)
    if os.path.isdir(od):
        for name in sorted(os.listdir(od)):
            p = os.path.join(od, name)
            if os.path.isdir(p) and name.startswith("clips_"):
                add("archives", p)
            elif name.startswith("final_avatar_") and name.endswith(".mp4") and name not in AVATAR_STD_VIDEOS:
                add("archives", p)
    for cat in out:
        out[cat] = [x for x in out[cat] if x[1] > 0 or cat == "archives"]
    return out


LABEL = {"regenerable": "1 随时能免费重新生成（--yes 就删）", "cache": "2 配音缓存（--cache；删了以后重新合成要再付配音的钱）",
         "takes": "2 人物视频历史版本（--takes；花钱生成的，删了就换不回去）", "archives": "3 手工留的实验目录和旧成片（--archive 名字，先问用户）"}


def project_dirs():
    return sorted(d for d in glob.glob(os.path.join(PROJECTS, "*")) if os.path.isfile(os.path.join(d, "meta.json")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", help="项目目录")
    ap.add_argument("--all", action="store_true", help="所有项目（~/.digital-human/projects 下）")
    ap.add_argument("--cache", action="store_true", help="另外删配音缓存")
    ap.add_argument("--takes", action="store_true", help="另外删人物视频的历史版本")
    ap.add_argument("--archive", action="append", default=[], help="删一个实验目录或旧成片（相对项目目录，预览里列出的名字）")
    ap.add_argument("--yes", action="store_true", help="真的删。不加就只预览")
    a = ap.parse_args()
    if bool(a.workdir) == bool(a.all):
        _cli.fail(_cli.USAGE, "--workdir 和 --all 二选一")
    if a.all and (a.cache or a.takes or a.archive):
        _cli.fail(_cli.USAGE, "--cache、--takes、--archive 只能对单个项目用（--workdir），一个一个确认")
    dirs = project_dirs() if a.all else [a.workdir]
    if not a.all and not os.path.isdir(a.workdir):
        _cli.fail(_cli.USAGE, "项目目录不存在：%s" % a.workdir)

    cats = ["regenerable"] + (["cache"] if a.cache else []) + (["takes"] if a.takes else [])
    summary, freed, removed = [], 0, []
    for wd in dirs:
        found = scan(wd)
        todo = [(rel, n) for c in cats for rel, n in found[c]]
        arch = {rel.replace("\\", "/"): n for rel, n in found["archives"]}
        for name in a.archive:
            key = os.path.normpath(name).replace("\\", "/")
            if key not in arch:
                _cli.fail(_cli.USAGE, "%s 不在可删的实验目录里（预览里列出的才行）：%s" % (name, "、".join(arch) or "没有"))
            todo.append((key, arch[key]))
        row = {"project": wd, **{c: mb(sum(n for _, n in found[c])) for c in found},
               "archive_items": [{"path": k, "mb": mb(v)} for k, v in arch.items()]}
        summary.append(row)
        if not a.all or a.yes:
            print("== %s" % wd)
            for c in ("regenerable", "cache", "takes", "archives"):
                if found[c]:
                    print("  %s：%.1f MB" % (LABEL[c], mb(sum(n for _, n in found[c]))))
                    for rel, n in found[c][:12]:
                        print("      %-48s %8.1f MB" % (rel, mb(n)))
                    if len(found[c]) > 12:
                        print("      ……还有 %d 项" % (len(found[c]) - 12))
        if a.yes:
            for rel, n in todo:
                remove(os.path.join(wd, rel))
                freed += n
                removed.append(os.path.join(wd, rel))
    if a.all and not a.yes:
        summary.sort(key=lambda r: -(r["regenerable"] + r["cache"] + r["takes"] + sum(x["mb"] for x in r["archive_items"])))
        print("%-52s %8s %8s %8s %8s" % ("项目", "第1类", "缓存", "历史版本", "实验目录"))
        for r in summary:
            print("%-52s %8.1f %8.1f %8.1f %8.1f" % (r["project"][-52:], r["regenerable"], r["cache"], r["takes"],
                                                  sum(x["mb"] for x in r["archive_items"])))
    if a.yes:
        print("\n已删除 %d 项，腾出 %.1f MB。" % (len(removed), mb(freed)))
        _cli.report(removed=removed, freed_mb=mb(freed), projects=summary)
    else:
        print("\n[预览] 没有删除任何文件。第 1 类加 --yes 就删；第 2、3 类要先问用户，再加对应参数。")
        _cli.report(preview=True, projects=summary)


if __name__ == "__main__":
    _cli.run(main)
