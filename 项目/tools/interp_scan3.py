"""S2 最后一批：8 个大站，每站最多 20 篇解读/图解，静态初筛 + 渲染复核。

初筛：正文 < 150 字、图片 >= 1、无 video、标题含"解读/图解"。
复核：浏览器打开，正文 < 150 字且 >= 1 张大图（naturalWidth >= 500）。
复核通过才写入 cases，并存截图。

用法：
    cd 项目
    python tools/interp_scan3.py
输出：
    data/interp-scan3.json
"""
from __future__ import annotations

import json
import os
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITES = os.path.join(ROOT, "data", "gov-sites-30.json")
OUT = os.path.join(ROOT, "data", "interp-scan5.json")
SHOTS = os.path.join(ROOT, "data", "interp-shots5")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}
NAMES = ["国务院", "教育部", "工信部", "海南", "内蒙古", "河南"]
LIMIT = 20


def stats(html: str):
    s = BeautifulSoup(html, "lxml")
    for t in s(["script", "style"]):
        t.decompose()
    ps = [p.get_text(" ", strip=True) for p in s.find_all("p")]
    return sum(len(x) for x in ps), len(s.find_all("img")), bool(s.find("video"))


def main() -> int:
    os.makedirs(SHOTS, exist_ok=True)
    sites = {k: v for k, v in json.load(open(SITES, encoding="utf-8")).items() if k in NAMES}
    rows, cases = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True,
                                    args=["--no-proxy-server", "--ignore-certificate-errors"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True,
                                  user_agent=UA["User-Agent"])
        for name, home in sites.items():
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
                        if len(arts) >= LIMIT:
                            break
                except Exception:  # noqa: BLE001
                    continue
                if len(arts) >= LIMIT:
                    break
            hit = None
            checked = 0
            for au in arts:
                try:
                    rr = requests.get(au, headers=UA, timeout=15)
                    rr.encoding = rr.apparent_encoding
                    text, imgs, video = stats(rr.text)
                    title = BeautifulSoup(rr.text, "lxml").title
                    title = title.get_text(strip=True) if title else ""
                    if video or "视频" in title or not ("解读" in title or "图解" in title):
                        continue
                    if text >= 150 or imgs < 1:
                        continue
                    # 渲染复核
                    pg = ctx.new_page()
                    try:
                        pg.goto(au, wait_until="load", timeout=45000)
                        pg.wait_for_timeout(2000)
                        chars = pg.evaluate(
                            "() => [...document.querySelectorAll('p')].reduce((n,p)=>n+(p.innerText||'').trim().length,0)")
                        big = pg.evaluate(
                            "() => [...document.querySelectorAll('img')].filter(i=>i.naturalWidth>=500).length")
                        checked += 1
                        if chars < 150 and big >= 1:
                            shot = os.path.join(SHOTS, f"{name}.png")
                            pg.screenshot(path=shot)
                            hit = {"site": name, "article": au, "title": title[:40],
                                   "static_chars": text, "render_chars": chars,
                                   "big_images": big, "shot": os.path.relpath(shot, ROOT)}
                            break
                    finally:
                        pg.close()
                except Exception:  # noqa: BLE001
                    continue
            rows.append({"site": name, "checked": checked, **(hit or {"status": "未命中"})})
            if hit:
                cases.append(hit)
                print(f"   {name:4} {hit['render_chars']} 字 / 大图 {hit['big_images']}  {hit['title'][:30]}")
            else:
                print(f"   {name:4} 未命中（复核 {checked}）")
        ctx.close()
        browser.close()

    json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "rows": rows, "cases": cases},
              open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"\n复核通过 {len(cases)} 个 -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
