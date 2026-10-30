"""S3：空栏目 / 占位栏目。

清单只看"栏目入口在不在"，不看"点进去有没有东西"。
这里抓两类硬证据：
1. 栏目页直接写着"暂无内容 / 暂无数据 / 正在建设中 / 敬请期待"；
2. 栏目页有导航、但列表条目为 0（需要渲染的判断，先不做，避免误报）。

用法：
    cd 项目
    python tools/empty_columns.py
输出：
    data/empty-columns.json
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
OUT = os.path.join(ROOT, "data", "empty-columns.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}
PLACEHOLDER = re.compile(r"暂无(内容|数据|信息|文章)|正在建设|敬请期待|内容建设中|该栏目暂无")
COLUMN = re.compile(r"/(columns?|col|list|xwzx|zwgk|zfxxgk|gk)/|index\.html?$")


def main() -> int:
    sites = json.load(open(SITES, encoding="utf-8"))
    cases = []
    scanned = 0
    for name, home in sites.items():
        try:
            r = requests.get(home, headers=UA, timeout=20)
            r.encoding = r.apparent_encoding
            s = BeautifulSoup(r.text, "lxml")
        except Exception:  # noqa: BLE001
            continue
        host = urlparse(home).netloc
        links, seen = [], set()
        for a in s.find_all("a", href=True):
            u = urljoin(home, a["href"]).split("#")[0]
            if host not in urlparse(u).netloc or u in seen:
                continue
            if not COLUMN.search(urlparse(u).path):
                continue
            seen.add(u)
            links.append(u)
            if len(links) >= 8:
                break
        for u in links:
            try:
                rr = requests.get(u, headers=UA, timeout=15)
                rr.encoding = rr.apparent_encoding
                text = BeautifulSoup(rr.text, "lxml").get_text(" ", strip=True)
                scanned += 1
                m = PLACEHOLDER.search(text)
                if m and len(text) < 3000:
                    cases.append({"site": name, "url": u, "marker": m.group(0),
                                  "text_len": len(text)})
                    print(f"   {name:4} {m.group(0)}  {u[:70]}")
            except Exception:  # noqa: BLE001
                continue
        time.sleep(0.2)

    json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "scanned_pages": scanned, "cases": cases},
              open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"\n扫描 {scanned} 个栏目页，占位 {len(cases)} 个 -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
