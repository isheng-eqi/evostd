"""把 30 个政府网站首页渲染成静态 HTML，供体检规则使用。

为什么需要：不少政府网站的页脚标识、日期是脚本注入的，直接抓 HTML 会「看不见」，
既会误判网站不合规，也会让模型和人看到的不是同一份东西。

输出：
    data/rendered/<站名>.html
    data/rendered/<站名>.txt
"""
from __future__ import annotations

import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITES = os.path.join(ROOT, "data", "gov-sites-30.json")
OUT = os.path.join(ROOT, "data", "rendered")


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    sites = json.load(open(SITES, encoding="utf-8"))
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True,
                                    args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                  user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                                              "Chrome/124.0 Safari/537.36"))
        for name, url in sites.items():
            page = ctx.new_page()
            t0 = time.time()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(1500)
                html = page.content()
                text = page.inner_text("body")
                open(os.path.join(OUT, f"{name}.html"), "w", encoding="utf-8").write(html)
                open(os.path.join(OUT, f"{name}.txt"), "w", encoding="utf-8").write(text)
                print(f"   ✓ {name:4} {len(html):7} chars {time.time() - t0:.1f}s")
            except Exception as e:  # noqa: BLE001
                print(f"   ✗ {name:4} {type(e).__name__}: {str(e)[:80]}")
            finally:
                page.close()
        ctx.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
