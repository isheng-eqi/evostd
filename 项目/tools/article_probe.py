"""文章层探针：抓「日期不对劲」和「旧文新发」。

对每个站点抓若干文章页，检查三件事：
1. URL 里的日期 vs 页面显示的发布日期；
2. 成文日期 vs 发布日期（成文晚于发布 = 不可能）；
3. 同一站点内两篇文章正文高度重合（旧文新发 / 一稿多发）。

用法：
    cd 项目
    python tools/article_probe.py --limit 8
输出：
    data/article-report.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITES = os.path.join(ROOT, "data", "gov-sites-30.json")
OUT = os.path.join(ROOT, "data", "article-report.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}

URL_DATE = re.compile(r"/(20\d{2})(\d{2})(?:/(\d{2}))?/")
DATES = {
    "publish": re.compile(r"(?:发布时间|发布日期|发文日期|日\s*期)\s*[:：]?\s*(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})"),
    "written": re.compile(r"(?:成文日期|印发日期|发文日期)\s*[:：]?\s*(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})"),
}


def get(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=25)
    r.encoding = r.apparent_encoding
    return r.text


def url_date(u: str) -> tuple[int, int, int] | None:
    m = URL_DATE.search(u)
    if not m:
        return None
    g = [x for x in m.groups() if x]
    try:
        return int(g[0]), int(g[1]), int(g[2])
    except (IndexError, ValueError):
        return None


def norm_date(t) -> tuple[int, int, int] | None:
    if not t:
        return None
    try:
        return int(t[0]), int(t[1]), int(t[2])
    except (TypeError, ValueError):
        return None


def article_text(html: str) -> str:
    s = BeautifulSoup(html, "lxml")
    for tag in s(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    # 正文 = 长度 >= 25 的 <p> 拼接；比"找最大 div"稳，不会被页壳带偏
    ps = [re.sub(r"\s+", "", p.get_text("")) for p in s.find_all("p")]
    ps = [x for x in ps if len(x) >= 25]
    return "".join(ps)


def find_dates(html: str) -> tuple:
    s = BeautifulSoup(html, "lxml")
    text = re.sub(r"\s+", " ", s.get_text(" "))
    pub = None
    m = re.search(r"(?:发布|发文|更新)[^0-9]{0,10}(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})", text)
    if m:
        pub = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if not pub:
        m = re.search(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})", text[:800])
        if m:
            pub = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    written = None
    m = re.search(r"成文日期[^0-9]{0,6}(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})", text)
    if m:
        written = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return pub, written


def shingles(text: str, k: int = 24) -> set:
    return {text[i:i + k] for i in range(0, max(1, len(text) - k + 1), k)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=8, help="每站最多抓几篇")
    args = ap.parse_args()

    sites = json.load(open(SITES, encoding="utf-8"))
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "sites": {}}

    for name, home in sites.items():
        try:
            html = get(home)
        except Exception as e:  # noqa: BLE001
            report["sites"][name] = {"error": str(e)[:80]}
            continue
        s = BeautifulSoup(html, "lxml")
        host = urlparse(home).netloc
        links, seen = [], set()
        for a in s.find_all("a", href=True):
            t = a.get_text(" ", strip=True)
            u = urljoin(home, a["href"])
            if len(t) < 8 or host not in urlparse(u).netloc:
                continue
            if not re.search(r"/20\d{2}|/art|/content|/news|/detail", u):
                continue
            if u in seen:
                continue
                continue
            seen.add(u)
            links.append(u)
            if len(links) >= args.limit:
                break

        arts = []
        for u in links:
            try:
                h = get(u)
            except Exception:  # noqa: BLE001
                continue
            soup = BeautifulSoup(h, "lxml")
            title = soup.title.get_text(strip=True) if soup.title else ""
            body = article_text(h)
            pub, written = find_dates(h)
            ud = url_date(u)
            arts.append({"url": u, "title": title[:50], "len": len(body),
                         "url_date": ud, "publish": pub, "written": written,
                         "fp": hashlib.md5(body.encode()).hexdigest()[:10],
                         "sh": shingles(body)})
            time.sleep(0.2)

        issues = []
        for i, a in enumerate(arts):
            if a["url_date"] and a["publish"] and a["url_date"] != a["publish"]:
                issues.append({"kind": "URL 日期≠页面日期", "url": a["url"],
                               "url_date": a["url_date"], "publish": a["publish"]})
            if a["written"] and a["publish"] and a["written"] > a["publish"]:
                issues.append({"kind": "成文日期晚于发布日期", "url": a["url"],
                               "written": a["written"], "publish": a["publish"]})
            for b in arts[i + 1:]:
                if a["url"] == b["url"]:
                    continue
                sim = jaccard(a["sh"], b["sh"])
                if sim >= 0.85:
                    issues.append({"kind": "正文重复", "url": a["url"], "other": b["url"],
                                   "similarity": round(sim, 3)})

        for a in arts:
            a.pop("sh", None)
        report["sites"][name] = {"articles": len(arts), "issues": issues}
        print(f"   {name:4} 文章 {len(arts):2} · 问题 {len(issues)}")
        time.sleep(0.3)

    json.dump(report, open(OUT, "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=1)
    total = sum(len(v.get("issues", [])) for v in report["sites"].values())
    print(f"\n合计问题 {total} 条 -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
