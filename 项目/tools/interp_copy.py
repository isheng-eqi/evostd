"""S4：政策解读抄原文。

找文字版解读页里指向政策原文的链接，把两份正文做 shingle 相似度：
- 解读正文 >= 300 字；
- 与原文相似度 >= 0.80 → 判为"解读只是原文转发"。

用法：
    cd 项目
    python tools/interp_copy.py
输出：
    data/interp-copy.json
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
OUT = os.path.join(ROOT, "data", "interp-copy.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}
NAMES = ["北京", "上海", "江苏", "天津", "安徽", "福建", "江西", "四川"]


def text_of(html: str) -> str:
    s = BeautifulSoup(html, "lxml")
    for t in s(["script", "style", "nav", "header", "footer"]):
        t.decompose()
    ps = [p.get_text("", strip=True) for p in s.find_all("p")]
    ps = [x for x in ps if len(x) >= 20]
    return re.sub(r"\s+", "", "".join(ps))


def shingles(text: str, k: int = 30) -> set:
    return {text[i:i + k] for i in range(0, max(1, len(text) - k + 1), 10)}


def main() -> int:
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
                if "解读" not in a.get_text(" ", strip=True):
                    continue
                u = urljoin(home, a["href"])
                if urlparse(u).netloc != urlparse(home).netloc or u in seen:
                    continue
                seen.add(u)
                cols.append(u)
                if len(cols) >= 2:
                    break
            arts = []
            for cu in cols:
                try:
                    rr = requests.get(cu, headers=UA, timeout=15)
                    rr.encoding = rr.apparent_encoding
                    cs = BeautifulSoup(rr.text, "lxml")
                    for a in cs.find_all("a", href=True):
                        if len(a.get_text(" ", strip=True)) >= 8 and re.search(r"/20\d{2}|content|art", a["href"]):
                            arts.append(urljoin(cu, a["href"]))
                        if len(arts) >= 5:
                            break
                except Exception:  # noqa: BLE001
                    continue
                if len(arts) >= 5:
                    break
            hit = None
            for au in arts:
                pg = ctx.new_page()
                try:
                    pg.goto(au, wait_until="domcontentloaded", timeout=40000)
                    pg.wait_for_timeout(1200)
                    html = pg.content()
                    body = text_of(html)
                    if len(body) < 300:
                        continue
                    # 找政策原文链接
                    s2 = BeautifulSoup(html, "lxml")
                    pol = None
                    for a in s2.find_all("a", href=True):
                        t = a.get_text(" ", strip=True)
                        if re.search(r"原文|政策文件|通知|办法|方案|意见", t) and len(t) <= 60:
                            u = urljoin(au, a["href"])
                            if urlparse(u).netloc == urlparse(au).netloc:
                                pol = u
                                break
                    if not pol:
                        continue
                    pr = requests.get(pol, headers=UA, timeout=20)
                    pr.encoding = pr.apparent_encoding
                    ptext = text_of(pr.text)
                    if len(ptext) < 300:
                        continue
                    sim = len(shingles(body) & shingles(ptext)) / max(1, len(shingles(body) | shingles(ptext)))
                    if sim >= 0.80:
                        hit = {"site": name, "interp": au, "policy": pol,
                               "chars": len(body), "policy_chars": len(ptext),
                               "similarity": round(sim, 3)}
                        break
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    pg.close()
            rows.append({"site": name, **(hit or {"status": "未命中"})})
            if hit:
                cases.append(hit)
                print(f"   {name:4} 相似度 {hit['similarity']}  {hit['interp'][:60]}")
            else:
                print(f"   {name:4} 未命中")
        ctx.close()
        browser.close()
    json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "rows": rows, "cases": cases},
              open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"\n命中 {len(cases)} 个 -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
