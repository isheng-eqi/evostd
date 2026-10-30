"""栏目页日期 vs 内容页日期。

检查指标里点名过「栏目页发布时间与内容页不一致」。
做法：首页/栏目页上，链接旁边通常有一个日期；打开详情页，看它自己写的发布日期，两者对不上就是候选。

用法：
    cd 项目
    python tools/list_vs_page.py
输出：
    data/list-date-report.json
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
OUT = os.path.join(ROOT, "data", "list-date-report.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}
DATE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")
PUB = re.compile(r"(?:发布时间|发布日期|发文日期|更新日期)\s*[:：]?\s*(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")

NAMES = ["北京", "天津", "河北", "山西", "上海", "江苏", "浙江", "安徽", "福建", "河南"]


def norm(u):
    return u.split("?")[0].split("#")[0]


def nearby_date(a):
    """从链接往上找两级，取最近的日期。"""
    node = a
    for _ in range(3):
        if node is None:
            break
        text = node.get_text(" ", strip=True) if hasattr(node, "get_text") else ""
        m = DATE.search(text)
        if m:
            try:
                return [int(m.group(1)), int(m.group(2)), int(m.group(3))]
            except (TypeError, ValueError):
                return None
        node = node.parent
    return None


def main() -> int:
    sites = {k: v for k, v in json.load(open(SITES, encoding="utf-8")).items() if k in NAMES}
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "mismatches": [], "checked": 0}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"))
        for name, home in sites.items():
            try:
                r = requests.get(home, headers=UA, timeout=20)
                r.encoding = r.apparent_encoding
                s = BeautifulSoup(r.text, "lxml")
            except Exception:  # noqa: BLE001
                continue
            host = urlparse(home).netloc
            picked, seen = [], set()
            for a in s.find_all("a", href=True):
                t = a.get_text(" ", strip=True)
                u = norm(urljoin(home, a["href"]))
                if len(t) < 8 or host not in urlparse(u).netloc or u in seen:
                    continue
                if not re.search(r"/20\d{2}|/art|/content", u) or "index.html" in u:
                    continue
                d = nearby_date(a)
                if not d:
                    continue
                seen.add(u)
                picked.append((u, d))
                if len(picked) >= 6:
                    break
            for u, list_date in picked:
                page = ctx.new_page()
                try:
                    page.goto(u, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(600)
                    text = page.inner_text("body")
                    m = PUB.search(text)
                    if not m:
                        continue
                    pub = [int(m.group(1)), int(m.group(2)), int(m.group(3))]
                    out["checked"] += 1
                    if pub != list_date:
                        out["mismatches"].append({"site": name, "url": u,
                                                  "list_date": list_date, "page_date": pub})
                        print(f"   {name} 不一致: 栏目 {list_date} vs 页面 {pub}  {u[:60]}")
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    page.close()
            print(f"   {name:4} 检查 {len(picked)} 条")
        ctx.close()
        browser.close()
    json.dump(out, open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"\n检查 {out['checked']} 条，日期不一致 {len(out['mismatches'])} 条 -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
