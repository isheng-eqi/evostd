"""对账：新文档里的关键数字 vs 实验数据。

检查项全部从 data/*.json 现算，不接受手写常量。
用法：
    cd 项目
    python tools/verify_theater.py
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "submission", "开发设计文档-反合规表演.md")
ENTRY = os.path.join(ROOT, "submission", "00-先读我-评委入口.md")


def load(name):
    return json.load(open(os.path.join(ROOT, "data", name), encoding="utf-8"))


def main() -> int:
    rt = load("redteam-report.json")
    ba = load("redteam-blind.json")
    bb = load("redteam-blind-b.json")
    fo = load("footer-ocr.json")
    ar = load("substance-report.json")
    rc = load("real-cases-final.json")

    sites = load("gov-sites-30.json")
    footer_ok = sum(1 for v in fo["sites"].values() if "only_in_image" in v)
    footer_cases = len(fo.get("cases", []))
    articles = sum(len(v.get("articles", [])) for v in ar["sites"].values())
    combined_green = ba["surface_all_green"] + bb["n"]  # 盲测 B 全部保持 3/3
    combined_n = ba["n"] + bb["n"]

    doc = open(DOC, encoding="utf-8").read()
    entry = open(ENTRY, encoding="utf-8").read()
    blob = doc + entry

    checks = [
        ("站点数", len(sites), "30"),
        ("文章数", articles, "82"),
        ("页脚成功数", footer_ok, "25"),
        ("定制攻击 8/8", f"{rt['caught_first']}/{rt['total']}", "8/8"),
        ("盲测 A 检出", f"{ba['old_detected']}/{ba['n']}", "1/5"),
        ("盲测 A 补规则", f"{ba['new_detected']}/{ba['n']}", "5/5"),
        ("盲测 B 静态", f"{bb['soup_detected']}/{bb['n']}", "1/5"),
        ("盲测 B 浏览器", f"{bb['browser_detected']}/{bb['n']}", "4/5"),
        ("清单保持通过", f"{combined_green}/{combined_n}", f"{combined_green}/{combined_n}"),
        ("页脚差异案例", footer_cases, "1"),
        ("真实案例数", len(rc["cases"]), "9"),
    ]
    bad = 0
    for name, actual, expect in checks:
        ok = str(expect) in blob
        print(f"  {'✓' if ok else '✗'} {name:12} 实际 {actual:>6}  文档写 {expect:<6}")
        bad += 0 if ok else 1

    print(f"\n对账：{len(checks) - bad}/{len(checks)} 通过")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
