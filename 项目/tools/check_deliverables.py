"""交付自检：链接、图片、占位符、docx 结构、产物完整性。

为什么需要
----------
提交材料最丢分的不是内容弱，是**点开图片 404**、**链接指向不存在的文件**、
**正文里还留着"待补"**。这些错误在最后一刻才被发现最要命，所以做成脚本。

用法：
    cd 项目
    python tools/check_deliverables.py            # 打印 + 写报告
    python tools/check_deliverables.py --json out.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUB = os.path.join(ROOT, "submission")

CURRENT_DOCS = [
    "00-先读我-评委入口.md",
    "开发设计文档-六节版.md",
    "知识图谱构建方案.md",
    "平台核验记录-20261021.md",
    "新行业迁移手册.md",
    "答辩问答准备.md",
    "演示视频脚本-3分钟.md",
    "开源发布说明.md",
]
HISTORICAL = [
    "02-Agent配置详情.md", "03-需要同步提交的文件说明.md", "04-示例问答.md",
    "05-截图清单.md", "README.md", "开发设计文档.md",
]
PLACEHOLDERS = ("TODO", "XXX", "待补充", "待填", "占位符", "lorem ipsum")
IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def is_external(ref: str) -> bool:
    return ref.startswith(("http://", "https://", "mailto:", "#"))


def check_doc(name: str, issues: list[dict]) -> dict:
    path = os.path.join(SUB, name)
    if not os.path.exists(path):
        issues.append({"level": "error", "file": name, "msg": "当前口径文档缺失"})
        return {"file": name, "exists": False}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    n_img = n_link = 0
    for ref in IMG_RE.findall(text):
        if is_external(ref):
            continue
        n_img += 1
        target = os.path.normpath(os.path.join(SUB, ref))
        if not os.path.exists(target):
            issues.append({"level": "error", "file": name, "msg": f"图片不存在：{ref}"})
    for ref in LINK_RE.findall(text):
        if is_external(ref):
            continue
        n_link += 1
        target = os.path.normpath(os.path.join(SUB, ref))
        if not os.path.exists(target):
            issues.append({"level": "error", "file": name, "msg": f"链接不存在：{ref}"})
    for token in PLACEHOLDERS:
        if token.lower() in text.lower():
            issues.append({"level": "warn", "file": name, "msg": f"含占位符：{token}"})
    return {"file": name, "exists": True, "images": n_img, "links": n_link}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    issues: list[dict] = []
    rows = [check_doc(n, issues) for n in CURRENT_DOCS]

    # docx 结构
    docx_path = os.path.join(SUB, "开发设计文档-六节版.docx")
    docx_info = {"exists": os.path.exists(docx_path)}
    if docx_info["exists"]:
        try:
            from docx import Document
            d = Document(docx_path)
            docx_info.update({
                "paragraphs": len(d.paragraphs),
                "tables": len(d.tables),
                "images": len(d.inline_shapes),
                "headings": sum(1 for p in d.paragraphs if p.style.name.startswith("Heading")),
            })
            if docx_info["images"] < 6:
                issues.append({"level": "warn", "file": "docx", "msg": "图片数偏少，可能未嵌入"})
        except Exception as exc:
            issues.append({"level": "error", "file": "docx", "msg": f"打不开：{exc}"})

    # 产物完整性
    expected = {
        "screenshots": 15,
        "videos": 1,
        "kg": 1,
        "kg-gov": 1,
    }
    counts = {}
    for d, minimum in expected.items():
        p = os.path.join(SUB, d)
        n = len(os.listdir(p)) if os.path.isdir(p) else 0
        counts[d] = n
        if n < minimum:
            issues.append({"level": "error", "file": d, "msg": f"文件数 {n} < 期望 {minimum}"})

    # 历史文档应带横幅
    for name in HISTORICAL:
        p = os.path.join(SUB, name)
        if os.path.exists(p):
            head = "".join(open(p, encoding="utf-8").readlines()[:12])
            if "历史版本" not in head:
                issues.append({"level": "warn", "file": name, "msg": "缺少历史版本横幅"})

    errors = [i for i in issues if i["level"] == "error"]
    warns = [i for i in issues if i["level"] == "warn"]

    print("== 当前口径文档 ==")
    for r in rows:
        if r.get("exists"):
            print(f"  {r['file']:32} 图 {r['images']:2} · 链接 {r['links']:2}")
        else:
            print(f"  {r['file']:32} 缺失")
    print(f"\n== docx ==  {docx_info}")
    print(f"== 产物 ==  {counts}")
    print(f"\n== 问题 == 错误 {len(errors)} · 警告 {len(warns)}")
    for i in issues:
        print(f"  [{i['level']}] {i['file']}: {i['msg']}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"docs": rows, "docx": docx_info, "counts": counts, "issues": issues},
                      f, ensure_ascii=False, indent=1)
        print(f"\n[check] 明细已写入 {args.json}")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
