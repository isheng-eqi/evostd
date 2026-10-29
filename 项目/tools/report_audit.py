"""政府信息公开工作年度报告 · 结构体检（M1 换对象后的第一版）。

为什么换对象
------------
网站首页的页脚、日期大量由脚本注入，静态抓取看不见、渲染后又不稳定；
年度报告是一份**文本文件**（PDF/DOCX/HTML），六部分结构由国办模板规定，
判起来可复核、可复算。

用法：
    cd 项目
    python tools/report_audit.py --urls data/report-urls.json --out data/report-audit.json
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import time
from urllib.parse import urlparse

import fitz
import requests
from bs4 import BeautifulSoup
from docx import Document

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}

PARTS = [
    ("总体情况", ["总体情况"]),
    ("主动公开", ["主动公开政府信息情况", "主动公开"]),
    ("依申请公开", ["收到和处理政府信息公开申请情况", "依申请公开"]),
    ("复议诉讼", ["行政复议", "行政诉讼"]),
    ("存在问题", ["存在的主要问题", "存在问题"]),
    ("其他事项", ["其他需要报告的事项", "其他事项"]),
]


def fetch_text(url: str) -> tuple[str, str, int]:
    r = requests.get(url, headers=UA, timeout=40)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "").lower()
    path = urlparse(url).path.lower()
    if path.endswith(".pdf") or "pdf" in ctype:
        doc = fitz.open(stream=r.content, filetype="pdf")
        return "\n".join(p.get_text() for p in doc), "pdf", doc.page_count
    if path.endswith(".docx") or "word" in ctype:
        d = Document(io.BytesIO(r.content))
        return "\n".join(p.text for p in d.paragraphs), "docx", 0
    r.encoding = r.apparent_encoding
    s = BeautifulSoup(r.text, "lxml")
    for t in s(["script", "style"]):
        t.decompose()
    return re.sub(r"\s+", " ", s.get_text(" ")), "html", 0


def split_sections(text: str) -> dict[str, str]:
    """按「一、xxx」切分，返回 {标题关键词: 正文}。"""
    norm = re.sub(r"[ \t]+", " ", text)
    marks = list(re.finditer(r"([一二三四五六七八九十]+)、\s*([^\n]{0,40})", norm))
    out: dict[str, str] = {}
    for i, m in enumerate(marks):
        title = m.group(2).strip()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(norm)
        body = norm[m.end():end]
        for canon, kws in PARTS:
            if canon in out:
                continue
            if any(k in title for k in kws):
                out[canon] = body.strip()
                break
    return out


def audit(name: str, url: str) -> dict:
    res = {"name": name, "url": url, "ok": False, "format": "", "pages": 0,
           "chars": 0, "rules": [], "parts": {}}
    try:
        text, fmt, pages = fetch_text(url)
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        return res
    res.update({"ok": True, "format": fmt, "pages": pages, "chars": len(text)})

    secs = split_sections(text)
    res["parts"] = {k: len(v) for k, v in secs.items()}

    # R1 六部分齐全
    missing = [p for p, _ in PARTS if p not in secs]
    res["rules"].append({
        "id": "R1", "name": "六部分齐全",
        "status": "pass" if not missing else "fail",
        "detail": "六部分齐全" if not missing else "缺少：" + "、".join(missing),
        "evidence": "；".join(f"{k}({len(v)}字)" for k, v in secs.items())[:160],
    })

    # R2 统计期限
    flat = re.sub(r"\s+", "", text)
    m = re.search(r"(20\d{2})年1月1日[至到](20\d{2})年12月31日", flat)
    m2 = re.search(r"(20\d{2})年1月1日[至到]12月31日", flat)
    if m:
        res["rules"].append({"id": "R2", "name": "统计期限明确", "status": "pass",
                             "detail": f"{m.group(1)}-01-01 至 {m.group(2)}-12-31", "evidence": m.group(0)})
    elif m2:
        res["rules"].append({"id": "R2", "name": "统计期限明确", "status": "pass",
                             "detail": f"{m2.group(1)} 年度", "evidence": m2.group(0)})
    else:
        res["rules"].append({"id": "R2", "name": "统计期限明确", "status": "fail",
                             "detail": "未找到统计期限表述", "evidence": ""})

    # R3 依申请公开数据
    body = secs.get("依申请公开", "")
    nums = re.findall(r"\d+\s*[件条]", body)
    res["rules"].append({"id": "R3", "name": "依申请公开数据", "status": "pass" if nums else "fail",
                         "detail": f"命中数字 {len(nums)} 个" if nums else "该部分未出现数量",
                         "evidence": "、".join(nums[:5])})

    # R4 复议诉讼数据
    body = secs.get("复议诉讼", "")
    nums = re.findall(r"\d+\s*[件条]", body)
    res["rules"].append({"id": "R4", "name": "复议诉讼数据", "status": "pass" if nums else "fail",
                         "detail": f"命中数字 {len(nums)} 个" if nums else "该部分未出现数量",
                         "evidence": "、".join(nums[:5])})

    # R5 存在问题非空
    body = secs.get("存在问题", "").strip()
    casual = re.sub(r"\s+", "", body)
    status = "pass" if len(casual) >= 30 else ("fail" if casual else "abstain")
    res["rules"].append({"id": "R5", "name": "存在问题及改进", "status": status,
                         "detail": f"{len(casual)} 字" + ("" if casual else "，未找到该部分"),
                         "evidence": casual[:80]})

    # R6 模板引用
    ref = ("国办公开办函" in flat) or ("2021〕30号" in flat) or ("2021]30号" in flat)
    res["rules"].append({"id": "R6", "name": "国办模板引用", "status": "pass" if ref else "abstain",
                         "detail": "引用了国办公开办函〔2021〕30 号" if ref else "未引用模板文号（不必然违规）",
                         "evidence": ""})
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", default=os.path.join(ROOT, "data", "report-urls.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "report-audit.json"))
    args = ap.parse_args()

    urls = json.load(open(args.urls, encoding="utf-8"))
    out = []
    for name, url in urls.items():
        r = audit(name, url)
        out.append(r)
        if r["ok"]:
            bad = [x["id"] for x in r["rules"] if x["status"] == "fail"]
            print(f"   {name:6} {r['format']:4} {r['chars']:6}字 缺={bad or '无'}")
        else:
            print(f"   {name:6} ERR {r.get('error','')[:60]}")
        time.sleep(0.3)

    json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": out},
              open(args.out, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
