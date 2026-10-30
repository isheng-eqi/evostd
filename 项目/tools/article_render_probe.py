"""文章页实读探针：渲染后抓日期与正文，找「日期不对劲」和「同文多次发布」。

跟静态抓取的区别：文章页很多是脚本渲染的，requests 只能拿到空壳。
这里每个页面都用浏览器打开再读。

用法：
    cd 项目
    python tools/article_render_probe.py
输出：
    data/substance-report.json
"""
from __future__ import annotations

import hashlib
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
OUT = os.path.join(ROOT, "data", "substance-report.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}

SITE_NAMES = ["河北", "山西", "天津", "北京", "上海", "江苏", "浙江", "安徽",
              "福建", "江西", "河南", "湖南", "广东", "四川", "陕西"]

DATE = r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})"
PUB = re.compile(r"(?:发布时间|发布日期|发文日期|更新日期)\s*[:：]?\s*" + DATE)
WRITTEN = re.compile(r"(?:成文日期|印发日期)\s*[:：]?\s*" + DATE)
ANY_DATE = re.compile(DATE)


def norm_url(u: str) -> str:
    return u.split("?")[0].split("#")[0]


def url_date(u: str):
    m = re.search(r"/(20\d{2})(\d{2})(?:/(\d{2}))?/", u)
    if m:
        return [int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)]
    m = re.search(r"/(20\d{2})-(\d{2})/(\d{2})/", u)
    if m:
        return [int(m.group(1)), int(m.group(2)), int(m.group(3))]
    return None


def body_from_html(html: str) -> str:
    s = BeautifulSoup(html, "lxml")
    for tag in s(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    ps = [re.sub(r"\s+", "", p.get_text("")) for p in s.find_all("p")]
    return "".join(x for x in ps if len(x) >= 25)


def find_date(pattern, text):
    m = pattern.search(text)
    if not m:
        return None
    try:
        return [int(m.group(1)), int(m.group(2)), int(m.group(3))]
    except (TypeError, ValueError):
        return None


def main() -> int:
    sites = {k: v for k, v in json.load(open(SITES, encoding="utf-8")).items() if k in SITE_NAMES}
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "sites": {}}

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                  user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"))
        for name, home in sites.items():
            # 首页链接用静态抓取即可
            try:
                r = requests.get(home, headers=UA, timeout=20)
                r.encoding = r.apparent_encoding
                s = BeautifulSoup(r.text, "lxml")
            except Exception as e:  # noqa: BLE001
                report["sites"][name] = {"error": str(e)[:60]}
                continue
            host = urlparse(home).netloc
            links, seen = [], set()
            for a in s.find_all("a", href=True):
                t = a.get_text(" ", strip=True)
                u = norm_url(urljoin(home, a["href"]))
                if len(t) < 8 or host not in urlparse(u).netloc or u in seen:
                    continue
                if not re.search(r"/20\d{2}|/art|/content", u):
                    continue
                seen.add(u)
                links.append(u)
                if len(links) >= 6:
                    break

            arts = []
            for u in links:
                page = ctx.new_page()
                try:
                    page.goto(u, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(800)
                    html = page.content()
                    text = page.inner_text("body")
                    body = body_from_html(html)
                    pub = find_date(PUB, text) or find_date(PUB, html)
                    written = find_date(WRITTEN, text) or find_date(WRITTEN, html)
                    if not pub:
                        m = ANY_DATE.search(text[:600])
                        if m:
                            pub = [int(m.group(1)), int(m.group(2)), int(m.group(3))]
                    arts.append({
                        "url": u, "title": (page.title() or "")[:50],
                        "len": len(body), "url_date": url_date(u),
                        "publish": pub, "written": written,
                        "fp": hashlib.md5(body.encode()).hexdigest()[:10] if len(body) > 200 else "",
                    })
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    page.close()
            report["sites"][name] = {"articles": arts}
            print(f"   {name:4} 文章 {len(arts)}")
        ctx.close()
        browser.close()

    # 分析
    issues = []
    for name, v in report["sites"].items():
        arts = [a for a in v.get("articles", []) if a["len"] > 200]
        for a in arts:
            if a["url_date"] and a["publish"]:
                if a["url_date"][:2] != a["publish"][:2]:
                    issues.append({"site": name, "kind": "URL 日期 ≠ 页面日期",
                                   "url": a["url"], "url_date": a["url_date"], "publish": a["publish"]})
            if a["written"] and a["publish"] and tuple(a["written"]) > tuple(a["publish"]):
                issues.append({"site": name, "kind": "成文日期晚于发布日期",
                               "url": a["url"], "written": a["written"], "publish": a["publish"]})
        # 正文指纹重复（不同 URL）
        groups = {}
        for a in arts:
            if a["fp"]:
                groups.setdefault(a["fp"], []).append(a)
        for fp, g in groups.items():
            urls = {x["url"] for x in g}
            if len(urls) < 2:
                continue
            dates = {tuple(x["publish"]) for x in g if x["publish"]}
            issues.append({"site": name, "kind": "同文多地址",
                           "urls": sorted(urls)[:4], "n": len(urls),
                           "dates": sorted(dates), "len": g[0]["len"]})

    report["issues"] = issues
    json.dump(report, open(OUT, "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=1)
    print(f"\n候选 {len(issues)} 条")
    for x in issues[:12]:
        print("   ", x["site"], x["kind"], "|", (x.get("url") or x.get("urls"))[:2] if isinstance(x.get("url") or x.get("urls"), list) else x.get("url", "")[:70])
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
