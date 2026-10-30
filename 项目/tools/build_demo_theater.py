"""生成「反合规表演」60 秒演示视频：真实页脚 + 改造/检测对照卡。

素材：河北页脚真实截图（submission/screenshots/河北-页脚.png）+ 8 张文字卡。
数字来自 data/redteam*.json，可复跑。

用法：
    cd 项目
    python tools/build_demo_theater.py
输出：
    submission/videos/00-反合规表演-60秒.mp4
"""
from __future__ import annotations

import os
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT = os.path.join(ROOT, "submission", "screenshots")
WORK = os.path.join(ROOT, "submission", "videos", "_theater")
OUT = os.path.join(ROOT, "submission", "videos", "00-反合规表演-60秒.mp4")
FF = r"C:\ictwork\ffmpeg\bin\ffmpeg.exe"
FONT = r"C:\Windows\Fonts\msyh.ttc"

CARDS = [
    ("改造 1：隐藏「主办单位」", "HTML 里还在，页面上看不见", 5),
    ("清单检查：读文本", "14 条规则，仍然通过", 5),
    ("实质检查：浏览器计算样式", "发现隐藏副本 → 抓到", 5),
    ("改造 2：字号设为 0", "上一版检测器漏了", 5),
    ("补一条规则：字号 / 透明度", "抓到", 5),
    ("改造 3：日期换成图片", "文本规则永远看不见", 5),
    ("Qwen3-VL 读图", "把日期读出来", 5),
    ("13 种手法", "清单 4/5 仍全绿 · 检出 1/5 → 4/5", 7),
]


def card(path, title, sub, big=False):
    im = Image.new("RGB", (1920, 1080), (11, 18, 32))
    d = ImageDraw.Draw(im)
    f1 = ImageFont.truetype(FONT, 64 if not big else 82)
    f2 = ImageFont.truetype(FONT, 34 if not big else 40)
    w1 = d.textlength(title, font=f1)
    d.text(((1920 - w1) / 2, 420 if not big else 400), title, font=f1, fill=(255, 255, 255))
    w2 = d.textlength(sub, font=f2)
    d.text(((1920 - w2) / 2, 540 if not big else 530), sub, font=f2, fill=(156, 195, 255))
    im.save(path)


def run(args, cwd=None):
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    if p.returncode != 0:
        print(p.stderr[-1000:])
        raise SystemExit(1)


def main() -> int:
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    shutil.copyfile(FONT, os.path.join(WORK, "font.ttc"))

    segs = []

    def seg_from(img, secs, name):
        seg = os.path.join(WORK, f"{name}.mp4")
        run([FF, "-y", "-loglevel", "error", "-loop", "1", "-i", img, "-t", str(secs),
             "-r", "25", "-c:v", "libx264", "-pix_fmt", "yuv420p", seg])
        segs.append(seg)

    # 标题
    card(os.path.join(WORK, "s0.png"), "反合规表演", "一个会弃权的检查器，和它抓不到的东西", True)
    seg_from(os.path.join(WORK, "s0.png"), 3, "seg0")

    # 真实页脚
    foot = Image.open(os.path.join(SHOT, "河北-页脚.png")).convert("RGB")
    foot = foot.resize((1920, 1080))
    foot.save(os.path.join(WORK, "s1.png"))
    seg_from(os.path.join(WORK, "s1.png"), 6, "seg1")

    for i, (title, sub, secs) in enumerate(CARDS, 2):
        p = os.path.join(WORK, f"s{i}.png")
        card(p, title, sub)
        seg_from(p, secs, f"seg{i}")

    # 结尾
    card(os.path.join(WORK, "s10.png"), "清单通过 ≠ 合规", "判不了，就说判不了", True)
    seg_from(os.path.join(WORK, "s10.png"), 4, "seg10")

    with open(os.path.join(WORK, "list.txt"), "w", encoding="utf-8") as f:
        for s in segs:
            f.write("file '%s'\n" % os.path.basename(s))
    run([FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", "list.txt", "-c", "copy", os.path.basename(OUT)], cwd=WORK)
    shutil.move(os.path.join(WORK, os.path.basename(OUT)), OUT)
    print(f"[demo] {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
