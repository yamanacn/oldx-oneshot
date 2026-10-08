"""从 Remotion 工程里取出小窗区间，写成 av_check.py 要的 spec.json。

  python remotion_spec.py <工程目录> [--out spec.json]
  python av_check.py <工程目录>/spec.json <工程目录>/out/main.mp4

小窗区间在 Film.tsx 里用 T("口播里的几个字") 算出，这里不重算：Film.tsx 末尾的 logSpec(...) 会把算好的
区间和小窗参数打印出来，本脚本运行一次 `npx remotion compositions` 把它接住。
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import _common  # noqa: F401  统一 stdout 编码


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--out")
    a = ap.parse_args()
    proj = Path(a.project).resolve()
    r = subprocess.run("npx remotion compositions src/index.ts", cwd=proj, shell=True, capture_output=True)
    text = re.sub(r"\x1b\[[0-9;]*m", "", (r.stdout + r.stderr).decode("utf-8", "replace"))
    m = re.search(r"PIP_SPEC (\{.*\})", text)
    if not m:
        sys.exit("没有取到小窗区间：确认 Film.tsx 末尾调用了 logSpec(...)。命令输出的最后几行：\n" + text[-600:])
    got = json.loads(m[1])
    film = json.loads((proj / "src" / "data" / "film.json").read_text(encoding="utf-8"))
    pip = got["pip"]
    spec = {"presenter": "public/presenter.mp4", "size": [film["width"], film["height"]], "fps": str(film["fps"]),
            "pip": {"diameter": pip["diameter"], "margin": pip["margin"], "margin_x": pip.get("marginX"), "margin_y": pip.get("marginY"),
                    "transition": pip["transition"], "focus": pip["focus"], "focus_size": pip["focusSize"]},
            "segments": [{"start": round(s, 3), "end": round(e, 3), "mode": "content_pip", "content": None} for s, e in got["runs"]]}
    out = Path(a.out) if a.out else proj / "spec.json"
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out), "runs": [[s["start"], s["end"]] for s in spec["segments"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
