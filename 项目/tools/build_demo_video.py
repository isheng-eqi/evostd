"""把真实截图拼成一版 60 秒字幕演示（无旁白）。

素材全部来自真实对象：河北政府网站页脚、体检报告页、北京报告页、基准对比图。
字幕即结论，每一句都能在截图或报告页里核对。

用法：
    cd 项目
    python tools/build_demo_video.py
输出：
    submission/videos/00-演示视频-60秒-字幕版.mp4
"""
from __future__ import annotations

import os
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT = os.path.join(ROOT, "submission", "screenshots")
WORK = os.path.join(ROOT, "submission", "videos", "_build")
OUT = os.path.join(ROOT, "submission", "videos", "00-演示视频-60秒-字幕版.mp4")
FF = r"C:\ictwork\ffmpeg\bin\ffmpeg.exe"
FONT_SRC = r"C:\Windows\Fonts\msyh.ttc"

SCENES = [
    ("s1", "河北-页脚.png", (0, 540, 1440, 900), 7,
     "河北省政府网站页脚：标识码、ICP、公安备案、主办单位都在",
     "没有联系电话。"),
    ("s2", "体检报告-河北.png", (0, 0, 1440, 430), 7,
     "14 项检查：8 项通过，2 项不合格，4 项弃权",
     "每条判定都绑定页面证据。"),
    ("s2b", "07-智能体工具绑定.png", (0, 0, 1440, 560), 8,
     "编排在 Nexent：36 个 MCP 工具、1 个检索工具、5 个技能",
     "判定在规则层：不调用模型。"),
    ("s3", "体检报告-河北.png", (40, 250, 520, 430), 6,
     "不合格 1：网站标识缺联系电话", "按检查指标，这一项扣 0.5 分。"),
    ("s4", "体检报告-河北.png", (520, 250, 1000, 430), 6,
     "不合格 2：页面缺 meta description", "普通人看不见，搜索引擎和读屏软件会读。"),
    ("s5", "体检报告-北京.png", (0, 0, 1440, 430), 7,
     "北京：页脚标识由脚本注入，正文不可见", "系统弃权，不判不合格。"),
    ("s6", "基准对比.png", None, 9,
     "同样 84 个判定：模型 0.643，逐项核对 0.52",
     "给它整份标准反而更低（0.583）。"),
]


def run(args, cwd):
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd)
    if p.returncode != 0:
        print("FAILED:", " ".join(args[:6]))
        print(p.stderr[-1200:])
        raise SystemExit(1)


def fit(src_abs: str, dst_name: str, w=1920, h=1080) -> str:
    im = Image.open(src_abs).convert("RGB")
    scale = min(w / im.width, h / im.height)
    nw, nh = int(im.width * scale), int(im.height * scale)
    im = im.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (w, h), (11, 18, 32))
    canvas.paste(im, ((w - nw) // 2, (h - nh) // 2))
    canvas.save(os.path.join(WORK, dst_name))
    return dst_name


def card(path_name: str, lines):
    im = Image.new("RGB", (1920, 1080), (11, 18, 32))
    d = ImageDraw.Draw(im)
    for text, size, y, color in lines:
        f = ImageFont.truetype(os.path.join(WORK, "font.ttc"), size)
        w = d.textlength(text, font=f)
        d.text(((1920 - w) / 2, y), text, font=f, fill=color)
    im.save(os.path.join(WORK, path_name))


def main() -> int:
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    shutil.copyfile(FONT_SRC, os.path.join(WORK, "font.ttc"))

    card("s0.png", [("合规体检", 72, 380, (255, 255, 255)),
                    ("一个会弃权的政务公开检查智能体", 32, 500, (156, 195, 255)),
                    ("2026 华为 ICT 大赛创新赛 · 赛题 3", 26, 570, (138, 150, 170))])
    card("s7.png", [("我们不猜。", 64, 440, (255, 255, 255)),
                    ("判不了，就说判不了。", 34, 560, (156, 195, 255))])

    segs = []

    def make_seg(src, secs, caption=None, idx=""):
        seg = f"seg{idx}.mp4"
        if caption:
            c1, c2 = caption
            open(os.path.join(WORK, f"c1_{idx}.txt"), "w", encoding="utf-8").write(c1)
            open(os.path.join(WORK, f"c2_{idx}.txt"), "w", encoding="utf-8").write(c2)
            vf = ("drawbox=y=900:w=iw:h=180:color=black@0.55:t=fill,"
                  "drawtext=fontfile=font.ttc:textfile=c1_%s.txt:fontsize=40:fontcolor=white:"
                  "x=(w-text_w)/2:y=935,"
                  "drawtext=fontfile=font.ttc:textfile=c2_%s.txt:fontsize=32:fontcolor=0xC9D6EA:"
                  "x=(w-text_w)/2:y=995" % (idx, idx))
            args = [FF, "-y", "-loglevel", "error", "-loop", "1", "-i", src,
                    "-t", str(secs), "-vf", vf, "-r", "25", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", seg]
        else:
            args = [FF, "-y", "-loglevel", "error", "-loop", "1", "-i", src,
                    "-t", str(secs), "-r", "25", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", seg]
        run(args, cwd=WORK)
        segs.append(seg)

    make_seg("s0.png", 3, None, "0")
    for i, (name, file, crop, secs, c1, c2) in enumerate(SCENES, 1):
        img = Image.open(os.path.join(SHOT, file)).convert("RGB")
        if crop:
            img = img.crop(crop)
        raw = os.path.join(WORK, f"{name}.png")
        img.save(raw)
        fitted = fit(raw, f"{name}_fit.png")
        make_seg(fitted, secs, (c1, c2), str(i))
        print(f"   {name} {secs}s")
    make_seg("s7.png", 7, None, "7")

    with open(os.path.join(WORK, "list.txt"), "w", encoding="utf-8") as f:
        for s in segs:
            f.write(f"file '{s}'\n")
    run([FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", "list.txt", "-c", "copy", OUT], cwd=WORK)
    print(f"[demo] {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
