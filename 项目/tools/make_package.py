"""打初赛提交包：先对账，再按清单打包，最后打印包内明细。

为什么要先对账
--------------
材料里数字对不上，是这类比赛最常见也最致命的失分点。
所以打包脚本的第一步不是 zip，而是跑 `verify_numbers.py --strict`：
**对不上就拒绝打包**，而不是"先打了再说"。

用法：
    cd 项目
    python tools/make_package.py
    python tools/make_package.py --out ../ICT自进化-初赛包.zip
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUB = os.path.join(ROOT, "submission")

# 包内清单：只放**当前口径**的材料；历史版本留在工作区，不进包
CURRENT_FILES = [
    "00-先读我-评委入口.md",
    "开发设计文档-模板版.md",
    "开发设计文档-模板版.docx",
    "Nexent智能体说明.md",
    "Nexent智能体说明.docx",
    "agent-config.json",
    "知识图谱构建方案.md",
    "平台核验证据.md",
    "新行业迁移手册.md",
    "数字对账.json",
    "成本基准.json",
    "agent-callgraph.png",
    "agent-callgraph.svg",
]
CURRENT_DIRS = [
    ("screenshots", ("*.png",)),
    ("kg", ("*.json", "*.dot", "*.svg", "*.png")),
    ("kg-gov", ("*.json", "*.dot", "*.svg", "*.png")),
    ("videos", ("*.mp4",)),
]
# 明确不打包的（历史口径 / 含联系方式 / 过时）
INTERNAL = [
    "答辩问答准备.md",           # 队内答辩准备
    "演示视频脚本-3分钟.md",      # 录制脚本（成片视频在包里）
    "开源发布说明.md",           # 内部发布流程与凭据操作记录
    "平台核验记录-20261021.md",  # 过程工作日志（事实已抽到 平台核验证据.md）
    "演示PPT.pptx",             # 录制视频用 / 总决赛答辩用
    "讲解稿-3分钟.md",           # 讲解提示稿
]
# 历史口径 / 含联系方式 / 过时：明确不打包
EXCLUDED = [
    "02-Agent配置详情.md", "03-需要同步提交的文件说明.md", "04-示例问答.md",
    "05-截图清单.md", "README.md", "开发设计文档.md",
]


def run_verify() -> tuple[bool, str]:
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "verify_numbers.py"),
                        "--strict"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT)
    tail = "\n".join((p.stdout or "").strip().splitlines()[-4:])
    return p.returncode == 0, tail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    ok, tail = run_verify()
    print("== 打包前对账 ==")
    print(tail)
    if not ok:
        print("\n★ 拒绝打包：材料数字与代码不一致。先修 `python tools/verify_numbers.py --strict`。")
        return 1

    stamp = dt.date.today().strftime("%Y%m%d")
    out = args.out or os.path.join(os.path.dirname(ROOT), f"ICT自进化-初赛包-{stamp}-{dt.datetime.now():%H%M}.zip")
    out = os.path.abspath(out)

    manifest = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in CURRENT_FILES:
            p = os.path.join(SUB, name)
            if os.path.exists(p):
                z.write(p, arcname=f"提交材料/{name}")
                manifest.append((f"提交材料/{name}", os.path.getsize(p)))
            else:
                print(f"[warn] 缺少 {name}")
        for d, patterns in CURRENT_DIRS:
            base = os.path.join(SUB, d)
            if not os.path.isdir(base):
                continue
            for fn in sorted(os.listdir(base)):
                if not any(fn.endswith(pt.replace("*", "")) for pt in patterns):
                    continue
                p = os.path.join(base, fn)
                z.write(p, arcname=f"提交材料/{d}/{fn}")
                manifest.append((f"提交材料/{d}/{fn}", os.path.getsize(p)))
        # 附带代码快照（评委可复现）
        for d in ("src", "tools", "skills"):
            base = os.path.join(ROOT, d)
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [x for x in dirnames if x not in ("__pycache__", "runs", ".git")]
                for fn in filenames:
                    if fn.endswith((".pyc", ".log")):
                        continue
                    p = os.path.join(dirpath, fn)
                    rel = os.path.relpath(p, ROOT).replace("\\", "/")
                    z.write(p, arcname=f"代码/{rel}")
                    manifest.append((f"代码/{rel}", os.path.getsize(p)))

        readme = (
            "ICT 自进化 · 初赛提交包\n"
            f"生成时间：{dt.datetime.now().isoformat(timespec='seconds')}\n\n"
            "目录：\n"
            "  提交材料/   设计文档、知识图谱方案、平台核验证据、迁移手册、对账与成本、截图与视频\n"
            "  代码/       可复现源码（src / tools / skills）\n\n"
            "不包含（历史口径）：\n  " + "\n  ".join(EXCLUDED) + "\n"
            "内部材料（留工作区，不提交）：\n  " + "\n  ".join(INTERNAL) + "\n\n"
            "注意：提交材料内页含队伍联系方式（设计文档首页），仅用于赛事提交；\n"
            "公开仓库已排除 项目/submission/，不会包含这些信息。\n\n"
            "复算：\n"
            "  cd 代码/src && set EVOSTD_NOW=2026-10-21\n"
            "  python ../tools/verify_numbers.py --strict   # 需要先补回 submission/ 产物\n"
        )
        z.writestr("README-包说明.txt", readme)

    size_mb = os.path.getsize(out) / 1024 / 1024
    print(f"\n== 已生成 ==")
    print(f"  {out}  ({size_mb:.2f} MB, {len(manifest)} 个文件 + README)")
    print("\n== 包内明细（前 20）==")
    for name, size in manifest[:20]:
        print(f"  {size/1024:8.1f} KB  {name}")
    if len(manifest) > 20:
        print(f"  …… 其余 {len(manifest)-20} 个文件")
    print("\n== 未打包（历史/含联系方式）==")
    for name in EXCLUDED:
        print(f"  - {name}")
    print("\n== 内部材料（留工作区，不进包）==")
    for name in INTERNAL:
        print(f"  - {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
