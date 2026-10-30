"""S2 扩面：把所有站点的「政策解读」栏目扫一遍，找"只有图、没有字"的解读。

判定：解读文章页正文可读文字 < 150 字，且图片 >= 1。
静态抓取拿不到正文的，标记为"待渲染"，不算案例。

用法：
    cd 项目
    python tools/interp_scan.py
输出：
    data/interp-scan.json
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
RENDER = os.path.join(ROOT, "data", "rendered")
OUT = os.path.join(ROOT, "data", "interp-scan.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}


def stats(html: str):
    s = BeautifulSoup(html, "lxml")
    for t in s(["script", "style"]):
        t.decompose()
    ps = [p.get_text(" ", strip=True) for p in s.find_all("p")]
    return sum(len(x) for x in ps), len(s.find_all("img"))


def main() -> int:
    rows = []
    for fn in sorted(os.listdir(RENDER)):
        if not fn.endswith(".html"):
            continue
        name = fn[:-5]
        home_html = open(os.path.join(RENDER, fn), encoding="utf-8", errors="ignore").read()
        s = BeautifulSoup(home_html, "lxml")
        cols, seen = [], set()
        for a in s.find_all("a", href=True):
            t = a.get_text(" ", strip=True)
            if "解读" not in t:
                continue
            u = urljoin("https://example.invalid/", a["href"]) if a["href"].startswith("/") else a["href"]
            if u in seen:
                continue
            seen.add(u)
            cols.append(u)
            if len(cols) >= 2:
                break
        if not cols:
            rows.append({"site": name, "status": "无解读入口"})
            continue
        found = None
        for cu in cols:
            try:
                r = requests.get(cu, headers=UA, timeout=15)
                r.encoding = r.apparent_encoding
                cs = BeautifulSoup(r.text, "lxml")
                arts = []
                for a in cs.find_all("a", href=True):
                    t = a.get_text(" ", strip=True)
                    if len(t) >= 8 and re.search(r"/20\d{2}|content|art", a["href"]):
                        arts.append(urljoin(cu, a["href"]))
                    if len(arts) >= 2:
                        break
                for au in arts:
                    rr = requests.get(au, headers=UA, timeout=15)
                    rr.encoding = rr.apparent_encoding
                    chars, imgs = stats(rr.text)
                    if chars < 150 and imgs >= 1:
                        found = {"site": name, "column": cu, "article": au,
                                 "text_chars": chars, "images": imgs}
                        break
                    if chars < 50:
                        found = {"site": name, "column": cu, "article": au,
                                 "text_chars": chars, "images": imgs, "maybe_shell": True}
                        break
            except Exception:  # noqa: BLE001
                continue
            if found:
                break
        rows.append({"site": name, **(found or {"status": "未命中"})})
        if found:
            print(f"   {name:4} {found.get('text_chars')} 字 / {found.get('images')} 图  {found['article'][:60]}")
        time.sleep(0.2)

    hits = [r for r in rows if "article" in r]
    json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "rows": rows},
              open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"\n命中 {len(hits)} 个 -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
