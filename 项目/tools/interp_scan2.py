"""S2 二次扩面（静态）：每站最多 10 篇解读文章，找"只有图、没有字"的页面。

排除：视频解读（<video> 或标题含"视频"）、专题页（标题不含"解读/图解"）。
判定：正文 <p> 文字 < 150 字，且图片 >= 1。

用法：
    cd 项目
    python tools/interp_scan2.py
输出：
    data/interp-scan2.json
"""
from __future__ import annotations

import json
import os
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITES = os.path.join(ROOT, "data", "gov-sites-30.json")
OUT = os.path.join(ROOT, "data", "interp-scan2.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}
KNOWN = {"上海", "宁夏", "河北", "浙江", "山西", "应急部"}  # 已有案例


def stats(html: str):
    s = BeautifulSoup(html, "lxml")
    for t in s(["script", "style"]):
        t.decompose()
    ps = [p.get_text(" ", strip=True) for p in s.find_all("p")]
    text = sum(len(x) for x in ps)
    return text, len(s.find_all("img")), bool(s.find("video"))


def main() -> int:
    sites = json.load(open(SITES, encoding="utf-8"))
    rows, cases = [], []
    for name, home in sites.items():
        if name in KNOWN:
            rows.append({"site": name, "status": "已有案例"})
            continue
        try:
            r = requests.get(home, headers=UA, timeout=20)
            r.encoding = r.apparent_encoding
            s = BeautifulSoup(r.text, "lxml")
        except Exception:  # noqa: BLE001
            rows.append({"site": name, "status": "首页失败"})
            continue
        cols, seen = [], set()
        for a in s.find_all("a", href=True):
            t = a.get_text(" ", strip=True)
            if not ("解读" in t or "图解" in t):
                continue
            u = urljoin(home, a["href"])
            if urlparse(u).netloc != urlparse(home).netloc or u in seen:
                continue
            seen.add(u)
            cols.append(u)
            if len(cols) >= 3:
                break
        arts = []
        for cu in cols:
            try:
                rr = requests.get(cu, headers=UA, timeout=15)
                rr.encoding = rr.apparent_encoding
                cs = BeautifulSoup(rr.text, "lxml")
                for a in cs.find_all("a", href=True):
                    t = a.get_text(" ", strip=True)
                    if len(t) >= 8 and re.search(r"/20\d{2}|content|art", a["href"]):
                        arts.append(urljoin(cu, a["href"]))
                    if len(arts) >= 10:
                        break
            except Exception:  # noqa: BLE001
                continue
            if len(arts) >= 10:
                break
        hit = None
        for au in arts:
            try:
                rr = requests.get(au, headers=UA, timeout=15)
                rr.encoding = rr.apparent_encoding
                text, imgs, video = stats(rr.text)
                title = BeautifulSoup(rr.text, "lxml").title
                title = title.get_text(strip=True) if title else ""
                if video or "视频" in title:
                    continue
                if text < 150 and imgs >= 1:
                    hit = {"site": name, "article": au, "title": title[:40],
                           "text_chars": text, "images": imgs}
                    break
            except Exception:  # noqa: BLE001
                continue
        rows.append({"site": name, **(hit or {"status": "未命中"})})
        if hit:
            cases.append(hit)
            print(f"   {name:4} {text} 字 / {imgs} 图  {hit['title'][:36]}")
        time.sleep(0.15)

    json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "rows": rows, "cases": cases},
              open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"\n新增候选 {len(cases)} 个 -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
