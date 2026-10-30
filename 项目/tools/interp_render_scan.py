"""S2 渲染版：用浏览器读解读文章，找"只有图、没有字"的页。

静态抓取拿不到正文的站点，这里逐页渲染后再量：
正文可读文字 < 150 字且图片 >= 1 → 候选；同时存一张截图作为证据。

用法：
    cd 项目
    python tools/interp_render_scan.py
输出：
    data/interp-render.json
    data/interp-shots/<站名>.png
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
OUT = os.path.join(ROOT, "data", "interp-render.json")
SHOTS = os.path.join(ROOT, "data", "interp-shots")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}
TEXT_MIN = 150


def main() -> int:
    os.makedirs(SHOTS, exist_ok=True)
    sites = json.load(open(SITES, encoding="utf-8"))
    rows, cases = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                  user_agent=UA["User-Agent"])
        for name, home in sites.items():
            # 找解读栏目入口（静态首页即可）
            cols, arts = [], []
            try:
                r = requests.get(home, headers=UA, timeout=20)
                r.encoding = r.apparent_encoding
                s = BeautifulSoup(r.text, "lxml")
                for a in s.find_all("a", href=True):
                    if "解读" in a.get_text(" ", strip=True):
                        u = urljoin(home, a["href"])
                        if urlparse(u).netloc == urlparse(home).netloc and u not in cols:
                            cols.append(u)
                for cu in cols[:2]:
                    rr = requests.get(cu, headers=UA, timeout=15)
                    rr.encoding = rr.apparent_encoding
                    cs = BeautifulSoup(rr.text, "lxml")
                    for a in cs.find_all("a", href=True):
                        t = a.get_text(" ", strip=True)
                        if len(t) >= 8 and re.search(r"/20\d{2}|content|art", a["href"]):
                            arts.append(urljoin(cu, a["href"]))
                        if len(arts) >= 3:
                            break
                    if len(arts) >= 3:
                        break
            except Exception:  # noqa: BLE001
                pass
            if not arts:
                rows.append({"site": name, "status": "无解读入口"})
                continue
            hit = None
            for au in arts[:3]:
                page = ctx.new_page()
                try:
                    page.goto(au, wait_until="domcontentloaded", timeout=25000)
                    page.wait_for_timeout(700)
                    chars = page.evaluate(
                        "() => [...document.querySelectorAll('p')].reduce((n,p)=>n+(p.innerText||'').trim().length,0)")
                    imgs = page.evaluate("() => document.querySelectorAll('img').length")
                    if chars < TEXT_MIN and imgs >= 1:
                        shot = os.path.join(SHOTS, f"{name}.png")
                        page.screenshot(path=shot)
                        hit = {"site": name, "article": au, "text_chars": chars,
                               "images": imgs, "shot": os.path.relpath(shot, ROOT)}
                        break
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    page.close()
            rows.append({"site": name, **(hit or {"status": "未命中"})})
            if hit:
                cases.append(hit)
                print(f"   {name:4} {hit['text_chars']} 字 / {hit['images']} 图  {hit['article'][:60]}")
        ctx.close()
        browser.close()

    json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "rows": rows, "cases": cases},
              open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"\n渲染后命中 {len(cases)} 个 -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
