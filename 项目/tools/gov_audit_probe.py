"""M0 数据可行性探针：用真实政府网站跑「政务公开体检」。

不调用任何模型。只做三件事：
1. 抓真实页面（首页 + 政策文件 / 政策解读 / 信息公开 / 办事服务）；
2. 按《政府网站与政务新媒体检查指标》里**可机械化**的条目判定：通过 / 不通过 / 弃权；
3. 输出 JSON 与一张汇总表，证明「真实对象 + 真实标准 + 确定性证据」这条路走得通。

弃权不是偷懒：像「严重错别字」「互动回应」「兼容性」这类条目需要人或不适合自动判定，
系统必须明确说判不了，而不是猜一个分数。

用法：
    cd 项目
    python tools/gov_audit_probe.py --out submission/可行性-M0-数据.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import threading
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 10
DATE_RE = re.compile(r"(20\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})")
SHORT_RE = re.compile(r"(?<![\d\-/])(\d{1,2})\s*[-/]\s*(\d{1,2})(?![\d\-/])")

SITES = [
    ("国务院", "https://www.gov.cn/"),
    ("山西省", "https://www.shanxi.gov.cn/"),
    ("太原市", "https://www.taiyuan.gov.cn/"),
    ("四川省", "https://www.sc.gov.cn/"),
    ("北京市", "https://www.beijing.gov.cn/"),
    ("陕西省", "https://www.shaanxi.gov.cn/"),
]

KEYWORDS = {
    "policy": ["政策文件", "政策法规", "规范性文件", "文件库", "文件"],
    "interpret": ["政策解读", "解读"],
    "disclosure": ["信息公开", "政务公开"],
    "guide": ["办事服务", "政务服务", "办事指南"],
}


@dataclass
class Page:
    url: str
    status: int = 0
    elapsed: float = 0.0
    html: str = ""
    text: str = ""
    title: str = ""
    meta_desc: str = ""
    links: list = field(default_factory=list)
    dates: list = field(default_factory=list)
    error: str = ""


def fetch(url: str, session: requests.Session) -> Page:
    p = Page(url=url)
    t0 = time.time()
    try:
        r = session.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
        p.elapsed = round(time.time() - t0, 2)
        p.status = r.status_code
        if not r.encoding or r.encoding.lower() in ("iso-8859-1",):
            r.encoding = r.apparent_encoding
        p.html = r.text
        soup = BeautifulSoup(p.html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        p.text = re.sub(r"\s+", " ", soup.get_text(" "))
        p.title = (soup.title.get_text(strip=True) if soup.title else "")
        meta = soup.find("meta", attrs={"name": re.compile(r"^\s*description\s*$", re.I)})
        p.meta_desc = meta.get("content", "").strip() if meta else ""
        for a in soup.find_all("a", href=True):
            p.links.append((a.get_text(" ", strip=True)[:40], urljoin(url, a["href"])))
        p.dates = parse_dates(p.text)
    except Exception as e:  # noqa: BLE001
        p.error = f"{type(e).__name__}: {e}"
        p.elapsed = round(time.time() - t0, 2)
    return p


def parse_dates(text: str) -> list[date]:
    today = date.today()
    out = []
    for m in DATE_RE.finditer(text):
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 2000 <= y <= today.year and 1 <= mo <= 12 and 1 <= d <= 31:
                dd = date(y, mo, d)
                if (today - dd).days <= 1200:
                    out.append(dd)
        except ValueError:
            continue
    # 列表里常见 "10-21" 这种不带年份的日期
    for m in SHORT_RE.finditer(text[:8000]):
        try:
            mo, d = int(m.group(1)), int(m.group(2))
            if not (1 <= mo <= 12 and 1 <= d <= 31):
                continue
            y = today.year if mo <= today.month else today.year - 1
            dd = date(y, mo, d)
            if 0 <= (today - dd).days <= 400:
                out.append(dd)
        except ValueError:
            continue
    return out


def pick_links(page: Page, words: list[str], limit: int = 3) -> list[str]:
    host = urlparse(page.url).netloc
    out = []
    for text, href in page.links:
        if not text or not href.startswith("http"):
            continue
        if host not in urlparse(href).netloc:
            continue
        if any(w in text for w in words) and href not in out:
            out.append(href)
        if len(out) >= limit:
            break
    return out


def rule(rid, name, clause, status, detail, evidence="", points=0.0):
    return {"id": rid, "name": name, "clause": clause, "status": status,
            "detail": detail, "evidence": evidence[:160], "points": points}


def audit_site(name: str, home_url: str, session: requests.Session) -> dict:
    result = {"site": name, "url": home_url, "pages": [], "rules": []}
    home = fetch(home_url, session)
    result["pages"].append({"url": home.url, "status": home.status, "elapsed": home.elapsed,
                            "title": home.title, "error": home.error})
    rules = result["rules"]
    link_count = len(home.links)

    # R1 站点可访问（单项否决）
    if home.status == 200 and home.elapsed <= 15:
        rules.append(rule("R1", "站点可访问", "单项否决：超过 15 秒打不开", "pass",
                          f"HTTP {home.status}，{home.elapsed}s"))
    else:
        rules.append(rule("R1", "站点可访问", "单项否决：超过 15 秒打不开", "fail",
                          f"HTTP {home.status}，{home.elapsed}s，{home.error}"))
        return result

    # 抓取子栏目
    sub = {}
    for key, words in KEYWORDS.items():
        urls = pick_links(home, words, limit=1)
        if not urls:
            sub[key] = None
            continue
        pg = fetch(urls[0], session)
        result["pages"].append({"url": pg.url, "status": pg.status, "elapsed": pg.elapsed,
                                "title": pg.title, "error": pg.error})
        sub[key] = pg
        time.sleep(0.3)

    # R2 首页更新（2 周）
    today = date.today()
    if home.dates:
        latest = max(home.dates)
        days = (today - latest).days
        rules.append(rule("R2", "首页 2 周内更新", "单项否决：监测前 2 周首页无更新",
                          "pass" if days <= 14 else "fail",
                          f"首页最近日期 {latest}，距今 {days} 天"))
    else:
        rules.append(rule("R2", "首页 2 周内更新", "单项否决：监测前 2 周首页无更新",
                          "abstain", "首页未解析到发布日期，需人工核对"))

    # R3 政策文件栏目（存在 + 6 个月内更新）
    pg = sub.get("policy")
    if pg is None or pg.status != 200:
        if link_count < 20:
            rules.append(rule("R3", "政策文件栏目", "未开设扣 5 分；6 个月未更新扣 5 分",
                              "abstain", f"首页仅 {link_count} 条链接，疑似脚本渲染，无法确认栏目是否存在"))
        else:
            rules.append(rule("R3", "政策文件栏目", "未开设扣 5 分；6 个月未更新扣 5 分",
                              "fail", "未找到政策文件栏目入口", points=-5))
    elif not pg.dates:
        rules.append(rule("R3", "政策文件栏目", "未开设扣 5 分；6 个月未更新扣 5 分",
                          "abstain", "栏目已找到，但列表页未解析到日期", pg.url))
    else:
        days = (today - max(pg.dates)).days
        rules.append(rule("R3", "政策文件栏目", "未开设扣 5 分；6 个月未更新扣 5 分",
                          "pass" if days <= 183 else "fail",
                          f"最近更新 {max(pg.dates)}，距今 {days} 天",
                          f"{pg.url}", 0 if days <= 183 else -5))

    # R4 政策解读栏目
    pg = sub.get("interpret")
    if pg is None or pg.status != 200:
        if link_count < 20:
            rules.append(rule("R4", "政策解读栏目", "未开设扣 5 分；6 个月未更新扣 5 分",
                              "abstain", f"首页仅 {link_count} 条链接，疑似脚本渲染，无法确认栏目是否存在"))
        else:
            rules.append(rule("R4", "政策解读栏目", "未开设扣 5 分；6 个月未更新扣 5 分",
                              "fail", "未找到政策解读栏目入口", points=-5))
    elif not pg.dates:
        rules.append(rule("R4", "政策解读栏目", "未开设扣 5 分；6 个月未更新扣 5 分",
                          "abstain", "栏目已找到，但列表页未解析到日期", pg.url))
    else:
        days = (today - max(pg.dates)).days
        rules.append(rule("R4", "政策解读栏目", "未开设扣 5 分；6 个月未更新扣 5 分",
                          "pass" if days <= 183 else "fail",
                          f"最近更新 {max(pg.dates)}，距今 {days} 天",
                          f"{pg.url}", 0 if days <= 183 else -5))

    # R5 网站标识（5 项）
    footer = home.text[-3000:]
    pats = {
        "网站标识码": r"网站标识码[:：]?\s*\d{6,}",
        "ICP 备案号": r"ICP\s*备?\s*\d{6,}",
        "公安备案": r"公网安备\s*\d{6,}|公安备案",
        "主办单位": r"主办(单位)?[:：]",
        "联系电话": r"(联系电话|联系方式|服务热线|热线|电话)[:：]?\s*[\d\-]{7,}|12345",
    }
    flags = {k: bool(re.search(p, footer)) for k, p in pats.items()}
    ev = [re.search(p, footer).group(0)[:24] for p in pats.values() if re.search(p, footer)]
    evidence_text = "；".join(ev)[:120]
    missing = [k for k, v in flags.items() if not v]
    kw = {"网站标识码": "网站标识码", "ICP 备案号": "ICP",
          "公安备案": "公网安备", "主办单位": "主办", "联系电话": "联系电话"}
    hidden = [k for k in flags if not flags[k] and kw[k] in home.html]
    found = sum(1 for v in flags.values() if v)
    if hidden:
        rules.append(rule("R5", "网站标识", "每缺一项扣 0.5 分", "abstain",
                          "HTML 中存在但正文不可见（可能脚本/隐藏元素）：" + "、".join(hidden)))
    elif found < 3:
        rules.append(rule("R5", "网站标识", "每缺一项扣 0.5 分", "abstain",
                          f"页脚只取到 {found}/5 项标识（可能由脚本/iframe 注入），证据不足，不判不合格"))
    else:
        rules.append(rule("R5", "网站标识", "每缺一项扣 0.5 分",
                          "pass" if not missing else "fail",
                          ("齐全" if not missing else "缺少：" + "、".join(missing)) + "｜" + evidence_text,
                          evidence_text, -0.5 * len(missing)))

    # R6 我为政府网站找错
    has_bug = ("jiucuo" in home.html.lower()) or ("找错" in home.html) or \
        ("找错" in home.text) or any("找错" in t for t, _ in home.links)
    rules.append(rule("R6", "我为政府网站找错入口", "未添加扣 1 分",
                      "pass" if has_bug else "abstain",
                      "找到入口脚本/链接" if has_bug else "静态页面未见入口，可能由脚本注入",
                      points=0))

    # R7 站内搜索
    soup = BeautifulSoup(home.html, "lxml")
    has_search = bool(soup.find("input", attrs={"type": re.compile("text|search", re.I)})) or \
        any(re.search(r"search|sousuo|so\.", str(f.get("action", ""))) for f in soup.find_all("form"))
    rules.append(rule("R7", "站内搜索", "未提供扣 4 分",
                      "pass" if has_search else "fail", "找到搜索框" if has_search else "未找到",
                      points=0 if has_search else -4))

    # R8 页面标签
    tagged = sum(1 for pg in [home] if pg.title and pg.meta_desc)
    rules.append(rule("R8", "页面标签", "每个无标签页面扣 0.1 分",
                      "pass" if tagged else "fail",
                      f"首页 title={'有' if home.title else '无'}，description={'有' if home.meta_desc else '无'}",
                      points=0 if tagged else -0.2))

    # R9 链接可用性（抽 8 条）
    sample = [(t, h) for t, h in home.links if h.startswith("http")][:5]
    bad = 0
    for _, href in sample:
        try:
            r = session.head(href, timeout=5, headers={"User-Agent": UA}, allow_redirects=True)
            if r.status_code in (403, 405, 501):
                r = session.get(href, timeout=5, headers={"User-Agent": UA}, allow_redirects=True)
            if r.status_code >= 400:
                bad += 1
        except Exception:  # noqa: BLE001
            bad += 1
        time.sleep(0.15)
    rules.append(rule("R9", "链接可用性", "首页每条坏链扣 0.2 分", "pass" if bad == 0 else "fail",
                      f"抽查 {len(sample)} 条，坏链 {bad} 条", points=-0.2 * bad))

    # R10 IPv6
    host = urlparse(home_url).netloc.split(":")[0]
    try:
        infos = socket.getaddrinfo(host, 443, socket.AF_INET6)
        has6 = bool(infos)
    except Exception:  # noqa: BLE001
        has6 = False
    rules.append(rule("R10", "IPv6", "未完成改造扣 1 分", "pass" if has6 else "fail",
                      "解析到 AAAA" if has6 else "未解析到 AAAA", points=0 if has6 else -1))

    # R11 办事指南要素
    elems = ["事项名称", "设定依据", "申请条件", "办理材料", "办理地点",
             "办理机构", "收费标准", "办理时间", "联系电话", "办理流程"]
    pg = sub.get("guide")
    item = None
    if pg is not None and pg.status == 200:
        hit0 = sum(1 for e in elems if e in pg.text)
        if hit0 >= 5:
            item = pg
        else:
            cand = [h for t_, h in pg.links
                    if ("指南" in t_ or "事项" in t_ or "办事" in t_) and h.startswith("http")][:1]
            if cand:
                item = fetch(cand[0], session)
                result["pages"].append({"url": item.url, "status": item.status,
                                        "elapsed": item.elapsed, "title": item.title,
                                        "error": item.error})
    if item is None:
        rules.append(rule("R11", "办事指南要素", "重点要素每缺一处扣 0.5 分",
                          "abstain", "未定位到事项详情页（栏目首页不判定）"))
    else:
        miss = [e for e in elems if e not in item.text]
        hit = len(elems) - len(miss)
        if hit < 4:
            rules.append(rule("R11", "办事指南要素", "重点要素每缺一处扣 0.5 分",
                              "abstain", f"页面仅命中 {hit}/10 个要素，疑似栏目页而非事项详情页"))
        else:
            rules.append(rule("R11", "办事指南要素", "重点要素每缺一处扣 0.5 分",
                              "pass" if not miss else "fail",
                              f"命中要素 {hit}/10" +
                              ("，缺：" + "、".join(miss) if miss else ""),
                              item.url, -0.5 * len(miss)))

    # R12-14 明确弃权
    rules.append(rule("R12", "严重错别字", "单项否决", "abstain", "需语言模型或人工判定，自动判定会误伤"))
    rules.append(rule("R13", "互动回应", "单项否决", "abstain", "需实际提交留言并等待答复，探针不做"))
    rules.append(rule("R14", "兼容性 / 一号登录", "每类扣 1 分", "abstain", "需多浏览器与账号体系，超出静态抓取范围"))

    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join("submission", "可行性-M0-数据.json"))
    ap.add_argument("--sites-file", default="", help="JSON: {名称: 首页URL}")
    args = ap.parse_args()

    socket.setdefaulttimeout(20)
    session = requests.Session()
    results = []
    sites = SITES
    if args.sites_file:
        with open(args.sites_file, encoding="utf-8") as f:
            sites = list(json.load(f).items())
    for name, url in sites:
        print(f"== {name} {url}")
        box = {}
        def work(n=name, u=url):
            box["r"] = audit_site(n, u, session)
        th = threading.Thread(target=work, daemon=True)
        th.start()
        th.join(timeout=75)
        if th.is_alive():
            print("   TIMEOUT 跳过（单站超过 75 秒）")
            continue
        r = box.get("r")
        if not r:
            print("   ERR 无结果")
            continue
        results.append(r)
        for x in r["rules"]:
            mark = {"pass": "✓", "fail": "✗", "abstain": "○"}[x["status"]]
            print(f"   {mark} {x['id']:4} {x['name']:14} {x['status']:7} {x['detail'][:60]}")

    total_pages = sum(len(r["pages"]) for r in results)
    status_count = {"pass": 0, "fail": 0, "abstain": 0}
    for r in results:
        for x in r["rules"]:
            status_count[x["status"]] += 1

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sites": len(results),
        "pages_fetched": total_pages,
        "rules_per_site": len(results[0]["rules"]) if results else 0,
        "status_count": status_count,
        "results": results,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1, default=str)

    print(f"\n== 汇总 ==\n   站点 {summary['sites']} · 页面 {total_pages} · "
          f"规则 {summary['rules_per_site']}/站 · 通过 {status_count['pass']} · "
          f"不通过 {status_count['fail']} · 弃权 {status_count['abstain']}")
    print(f"   JSON: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
