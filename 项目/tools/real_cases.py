"""真实案例：过了清单、实质不足的站点。

两条判定标准（我们自己定的，写进材料，不冒充官方口径）：
S1 页面标签：存在 meta description，但长度 < 12 字（或等于站点名）→ 有标签、无描述；
S2 政策解读：解读文章正文可读文字 < 150 字，且图片 ≥ 1 → 有解读、没字。

用法：
    cd 项目
    python tools/real_cases.py
输出：
    data/real-cases.json
"""
from __future__ import annotations

import json
import os
import re
import time

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDER = os.path.join(ROOT, "data", "rendered")
JUDG = os.path.join(ROOT, "data", "m1-judgments.json")
OUT = os.path.join(ROOT, "data", "real-cases.json")
UA = {"User-Agent": "Mozilla/5.0 Chrome/124.0"}
TEXT_MIN = 150


def meta_case(name: str):
    html = open(os.path.join(RENDER, f"{name}.html"), encoding="utf-8", errors="ignore").read()
    s = BeautifulSoup(html, "lxml")
    m = s.find("meta", attrs={"name": re.compile(r"^\s*description\s*$", re.I)})
    desc = (m.get("content") or "").strip() if m else ""
    title = s.title.get_text(strip=True) if s.title else ""
    # 只在"标签存在但没内容"时算数；标签缺失属于清单层不合格，不重复计
    if desc and len(desc) < 12:
        return {"site": name, "kind": "S1 有标签无描述", "desc": desc, "len": len(desc), "title": title}
    return None


def paragraphs(html: str):
    s = BeautifulSoup(html, "lxml")
    for t in s(["script", "style"]):
        t.decompose()
    ps = [p.get_text(" ", strip=True) for p in s.find_all("p")]
    return sum(len(x) for x in ps), len(s.find_all("img"))


def interp_cases():
    j = json.load(open(JUDG, encoding="utf-8"))
    out = []
    for site in j["results"]:
        cols = [p for p in site["pages"] if "解读" in (p.get("title") or "") and "index" in p["url"]]
        if not cols:
            continue
        u = cols[0]["url"]
        try:
            r = requests.get(u, headers=UA, timeout=20)
            r.encoding = r.apparent_encoding
            s = BeautifulSoup(r.text, "lxml")
            links = []
            for a in s.find_all("a", href=True):
                t = a.get_text(" ", strip=True)
                if len(t) >= 8 and re.search(r"/20\d{2}|content|art", a["href"]):
                    links.append(urljoin(u, a["href"]))
                if len(links) >= 3:
                    break
            if not links:
                continue
            for a in links:
                rr = requests.get(a, headers=UA, timeout=20)
                rr.encoding = rr.apparent_encoding
                chars, imgs = paragraphs(rr.text)
                if chars < TEXT_MIN and imgs >= 1:
                    out.append({"site": site["site"], "kind": "S2 有解读无文字",
                                "url": a, "text_chars": chars, "images": imgs})
                    break
        except Exception:  # noqa: BLE001
            continue
    return out


def main() -> int:
    cases = []
    for fn in sorted(os.listdir(RENDER)):
        if fn.endswith(".html"):
            c = meta_case(fn[:-5])
            if c:
                cases.append(c)
    cases += interp_cases()
    json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "criteria": {"S1": "meta description < 12 字", "S2": "解读正文 < 150 字且含图片"},
               "cases": cases},
              open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"真实案例 {len(cases)} 个")
    for c in cases:
        print("  ", c["site"], c["kind"], "|", c.get("desc") or c.get("url", "")[:60],
              c.get("len", c.get("text_chars", "")), "img", c.get("images", ""))
    print("->", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
