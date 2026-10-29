"""用渲染后的首页重新判定 R2 / R5 / R8，并和静态抓取结果对比。

静态抓取看不见脚本注入的页脚与日期；渲染后再判，才接近人打开浏览器看到的东西。
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date

from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDER = os.path.join(ROOT, "data", "rendered")
STATIC = os.path.join(ROOT, "data", "m1-judgments.json")
OUT = os.path.join(ROOT, "data", "m1-rendered-labels.json")

sys.path.insert(0, os.path.join(ROOT, "tools"))
from gov_audit_probe import parse_dates  # noqa: E402

PATS = {
    "网站标识码": r"网站标识码[:：]?\s*\d{6,}",
    "ICP 备案号": r"ICP\s*备?\s*\d{6,}",
    "公安备案": r"公网安备\s*\d{6,}|公安备案",
    "主办单位": r"主办(单位)?[:：]",
    "联系电话": r"(联系电话|联系方式|服务热线|热线|电话)[:：]?\s*[\d\-]{7,}|12345",
}


def label_site(name: str) -> dict:
    hp = os.path.join(RENDER, f"{name}.html")
    tp = os.path.join(RENDER, f"{name}.txt")
    if not (os.path.exists(hp) and os.path.exists(tp)):
        return {"error": "no render"}
    html = open(hp, encoding="utf-8").read()
    text = open(tp, encoding="utf-8").read()
    soup = BeautifulSoup(html, "lxml")
    title = bool(soup.title and soup.title.get_text(strip=True))
    meta = soup.find("meta", attrs={"name": re.compile(r"^\s*description\s*$", re.I)})
    desc = bool(meta and (meta.get("content") or "").strip())

    footer = re.sub(r"\s+", " ", text)[-3000:]
    flags = {k: bool(re.search(p, footer)) for k, p in PATS.items()}
    found = sum(flags.values())

    today = date.today()
    dates = parse_dates(re.sub(r"\s+", " ", text)[:8000])
    if dates:
        days = (today - max(dates)).days
        r2 = "pass" if days <= 14 else "fail"
        r2_detail = f"最近日期 {max(dates)}，距今 {days} 天"
    else:
        r2 = "abstain"
        r2_detail = "正文未解析到日期"

    if found < 3:
        r5 = "abstain"
        r5_detail = f"页脚只取到 {found}/5 项"
    else:
        missing = [k for k, v in flags.items() if not v]
        r5 = "pass" if not missing else "fail"
        r5_detail = "齐全" if not missing else "缺少：" + "、".join(missing)

    return {
        "R8": {"status": "pass" if (title and desc) else "fail",
               "detail": f"title={title} desc={desc}"},
        "R2": {"status": r2, "detail": r2_detail},
        "R5": {"status": r5, "detail": r5_detail},
    }


def main() -> int:
    sites = json.load(open(os.path.join(ROOT, "data", "gov-sites-30.json"), encoding="utf-8"))
    rendered = {name: label_site(name) for name in sites}
    static = {}
    j = json.load(open(STATIC, encoding="utf-8"))
    for site in j["results"]:
        static[site["site"]] = {r["id"]: r["status"] for r in site["rules"] if r["id"] in ("R2", "R5", "R8")}

    diff = []
    for name, labels in rendered.items():
        if "error" in labels:
            continue
        for rid in ("R2", "R5", "R8"):
            a = static.get(name, {}).get(rid)
            b = labels[rid]["status"]
            if a != b:
                diff.append((name, rid, a, b, labels[rid]["detail"]))

    json.dump({"labels": rendered}, open(OUT, "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=1)
    print(f"渲染判定 {sum(1 for v in rendered.values() if 'error' not in v)} 站；与静态结果不同的 {len(diff)} 条：")
    for d in diff:
        print(f"   {d[0]:4} {d[1]} 静态={d[2]} 渲染={d[3]} | {d[4][:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
