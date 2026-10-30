"""盲测 B：换 5 种新变体，分别用「静态文本检测」和「浏览器可见性检测」去抓。

目的：证明两件事
1. 静态文本检测（BeautifulSoup 去 display:none）对 font-size:0 / visibility:hidden / hidden 属性会漏；
2. 换成浏览器 innerText 之后能补上一部分，但仍有漏（图片日期、canonical 偷换）。

用法：
    cd 项目
    python tools/redteam_blind_b.py
输出：
    data/redteam-blind-b.json
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import redteam as RT  # noqa: E402
import redteam_blind as RB  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "rendered", "河北.html")
OUT = os.path.join(ROOT, "data", "redteam-blind-b.json")
WORK = os.path.join(ROOT, "data", "redteam-blind-b")
TODAY = date(2026, 10, 30)


def c1_hidden_attr(html):
    s = BeautifulSoup(html, "lxml")
    for el in s.find_all(string=re.compile("主办")):
        p = el.parent
        if p:
            p["hidden"] = "hidden"
    return str(s), {"id": "C1", "name": "hidden 属性藏信息"}


def c2_font_zero(html):
    s = BeautifulSoup(html, "lxml")
    for el in s.find_all(string=re.compile("主办")):
        p = el.parent
        if p:
            p["style"] = (p.get("style", "") + ";font-size:0").strip(";")
    return str(s), {"id": "C2", "name": "字号 0 藏信息"}


def c3_date_image(html):
    s = BeautifulSoup(html, "lxml")
    for el in s.find_all(string=re.compile(r"20\d{2}-\d{1,2}-\d{1,2}")):
        img = s.new_tag("img")
        img["src"] = "date.png"
        img["alt"] = ""
        el.replace_with(img)
        break
    return str(s), {"id": "C3", "name": "日期换成图片"}


def c4_canonical(html):
    s = BeautifulSoup(html, "lxml")
    link = s.new_tag("link", rel="canonical", href="https://www.hebei.gov.cn/old-article.html")
    s.head.append(link)
    return str(s), {"id": "C4", "name": "canonical 指向旧文"}


def c5_visibility(html):
    s = BeautifulSoup(html, "lxml")
    for el in s.find_all(string=re.compile("主办")):
        p = el.parent
        if p:
            p["style"] = (p.get("style", "") + ";visibility:hidden").strip(";")
    return str(s), {"id": "C5", "name": "visibility 藏信息"}


BLIND_B = [c1_hidden_attr, c2_font_zero, c3_date_image, c4_canonical, c5_visibility]


def soup_detected(html, base_hits, home):
    return any(det(html, {}, {"home": home}) and not base_hits[name]
               for name, det in RT.DETECTORS.items())


def browser_check(page, html):
    page.set_content(html, wait_until="domcontentloaded")
    return page.evaluate("""() => {
      const vis = document.body.innerText, all = document.body.textContent;
      const out = [];
      for (const kw of ['主办','网站标识码']) {
        if (all.includes(kw) && !vis.includes(kw)) out.push('不可见:' + kw);
      }
      // v2：字号 0 / 透明度 0 / 被 visibility 隐藏
      for (const kw of ['主办','网站标识码']) {
        for (const el of document.querySelectorAll('body *')) {
          if (el.children.length) continue;
          if (!(el.textContent || '').includes(kw)) continue;
          const cs = getComputedStyle(el);
          if (parseFloat(cs.fontSize) < 4) out.push('字号为0:' + kw);
          if (parseFloat(cs.opacity) < 0.1) out.push('透明:' + kw);
          if (cs.visibility === 'hidden') out.push('visibility:' + kw);
        }
      }
      const c = document.querySelector('link[rel=canonical]');
      if (c && c.href && !c.href.includes('hebei.gov.cn/') ) out.push('canonical异常');
      return [...new Set(out)];
    }""")


def main() -> int:
    base = open(SRC, encoding="utf-8").read()
    home = "https://www.hebei.gov.cn/"
    os.makedirs(WORK, exist_ok=True)
    base_hits = {n: d(base, {}, {"home": home}) for n, d in RT.DETECTORS.items()}

    rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True, args=["--no-proxy-server"])
        page = browser.new_page()
        for fn in BLIND_B:
            html, meta = fn(base)
            open(os.path.join(WORK, f"{meta['id']}.html"), "w", encoding="utf-8").write(html)
            old = soup_detected(html, base_hits, home)
            findings = browser_check(page, html)
            # C3/C4 专用规则
            if "date.png" in html and not re.search(r"20\d{2}-\d{1,2}-\d{1,2}", RB.RT.raw_text(html)[:600]):
                findings.append("日期只剩图片")
            if "rel=\"canonical\"" in html and "old-article" in html:
                findings.append("canonical 指向旧文")
            sc = RB.surface(html)
            kept = sum(1 for k, v in sc.items() if v and RB.surface(base).get(k))
            rows.append({**meta, "soup": old, "findings": findings,
                         "browser": bool(findings), "surface_kept": kept})
            print(f"   {meta['id']} {meta['name']:14} 静态={'✓' if old else '✗'} "
                  f"浏览器+规则={'✓' if findings else '✗'} {findings} 清单={kept}")
        browser.close()

    soup_n = sum(r["soup"] for r in rows)
    br_n = sum(r["browser"] for r in rows)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n": len(rows), "soup_detected": soup_n, "browser_detected": br_n,
        "surface_avg": round(sum(r["surface_kept"] for r in rows) / len(rows), 2),
        "rows": rows,
        "note": "浏览器检测只覆盖'文字存在但不可见'；图片日期与 canonical 偷换仍需新规则。",
    }
    json.dump(report, open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"\n盲测 B：静态 {soup_n}/{len(rows)}，浏览器 {br_n}/{len(rows)} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
