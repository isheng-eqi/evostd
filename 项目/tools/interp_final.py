"""最后一次猎捕：静态首页找不到"解读"入口的 8 个站，改用渲染后的首页找入口。

流程：渲染首页 → 找含"解读/图解"的链接 → 打开栏目 → 取最多 10 篇
→ 静态初筛（正文 < 150 字、有图、无 video、标题含解读/图解）
→ 渲染复核（正文 < 150 字、至少 1 张大图）→ 截图存证。

用法：
    cd 项目
    python tools/interp_final.py
输出：
    data/interp-final.json
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
OUT = os.path.join(ROOT, "data", "interp-final.json")
SHOTS = os.path.join(ROOT, "data", "interp-shots-final")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}
NAMES = ["吉林", "黑龙江", "上海", "贵州", "西藏", "宁夏", "新疆", "太原"]


def static_stats(html: str):
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
            page = ctx.new_page()
            cols = []
            try:
                page.goto(home, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
                hrefs = page.evaluate("""() => [...document.querySelectorAll('a')]
                    .filter(a => (a.innerText || '').match(/解读|图解/))
                    .map(a => a.href).filter(Boolean)""")
                for u in hrefs:
                    if urlparse(u).netloc == urlparse(home).netloc and u not in cols:
                        cols.append(u)
                    if len(cols) >= 3:
                        break
            except Exception:  # noqa: BLE001
                rows.append({"site": name, "status": "首页渲染失败"})
                page.close()
                continue
            finally:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass
            if not cols:
                rows.append({"site": name, "status": "无解读入口"})
                print(f"   {name:4} 首页无解读入口")
                continue
            arts = []
            for cu in cols:
                try:
                    rr = requests.get(cu, headers=UA, timeout=15)
                    rr.encoding = rr.apparent_encoding
                    cs = BeautifulSoup(rr.text, "lxml")
                    for a in cs.find_all("a", href=True):
                        if len(a.get_text(" ", strip=True)) >= 8 and re.search(r"/20\d{2}|content|art", a["href"]):
                            arts.append(urljoin(cu, a["href"]))
                        if len(arts) >= 10:
                            break
                except Exception:  # noqa: BLE001
                    continue
                if len(arts) >= 10:
                    break
            hit = None
            checked = 0
            for au in arts:
                try:
                    rr = requests.get(au, headers=UA, timeout=15)
                    rr.encoding = rr.apparent_encoding
                    text, imgs, video = static_stats(rr.text)
                    title = BeautifulSoup(rr.text, "lxml").title
                    title = title.get_text(strip=True) if title else ""
                    if video or "视频" in title or not ("解读" in title or "图解" in title):
                        continue
                    if text >= 150 or imgs < 1:
                        continue
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
                                   "render_chars": chars, "big_images": big,
                                   "shot": os.path.relpath(shot, ROOT)}
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
    print(f"\n命中 {len(cases)} 个 -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
