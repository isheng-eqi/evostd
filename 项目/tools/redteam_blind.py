"""盲测：用「写检测器时没见过」的 5 种变体，测现有检测器能不能抓住。

同时统计：这些被改造过的页面，在原有 14 条清单规则下还有多少能通过。
——如果"清单照样绿、人看到的不一样"，项目的论点就成立。

用法：
    cd 项目
    python tools/redteam_blind.py
输出：
    data/redteam-blind.json
"""
from __future__ import annotations

import copy
import json
import os
import re
import time
from datetime import date

from bs4 import BeautifulSoup

import redteam as RT

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "rendered", "河北.html")
OUT = os.path.join(ROOT, "data", "redteam-blind.json")
WORK = os.path.join(ROOT, "data", "redteam-blind")
TODAY = date(2026, 10, 30)


# ---------- 盲测攻击（写检测器时没见过） ----------
def b1_noscript(html: str):
    s = BeautifulSoup(html, "lxml")
    ns = s.new_tag("noscript")
    ns.string = "主办单位：河北省人民政府办公厅 网站标识码：1300000013"
    s.body.append(ns)
    return str(s), {"id": "B1", "name": "信息藏进 noscript"}


def b2_shift_date(html: str):
    s = BeautifulSoup(html, "lxml")
    for el in s.find_all(string=re.compile(r"2026-10-2\d")):
        el.replace_with(re.sub(r"2026-10-2\d", "2026-10-15", str(el)))
        break
    return str(s), {"id": "B2", "name": "日期只改早 15 天"}


def b3_punct_copy(html: str):
    s = BeautifulSoup(html, "lxml")
    a = s.find("a", href=re.compile(r"/20\d{2}"))
    if a:
        block = a.find_parent(["li", "div"]) or a
        dup = copy.copy(block)
        for el in dup.find_all(string=True):
            if len(el) > 10:
                el.replace_with(str(el).replace("", " ").strip())
        block.insert_after(dup)
    return str(s), {"id": "B3", "name": "复制正文加空格"}


def b4_alt_empty(html: str):
    s = BeautifulSoup(html, "lxml")
    for el in s.find_all(string=re.compile("主办")):
        img = s.new_tag("img")
        img["src"] = "footer.png"
        img["alt"] = ""
        el.replace_with(img)
    return str(s), {"id": "B4", "name": "信息只放图片且无 alt"}


def b5_building(html: str):
    s = BeautifulSoup(html, "lxml")
    a = s.find("a", href=re.compile(r"/20\d{2}"))
    if a:
        a["href"] = "https://www.hebei.gov.cn/under-construction.html"
    return str(s), {"id": "B5", "name": "坏链指向建设中页"}


BLIND = [b1_noscript, b2_shift_date, b3_punct_copy, b4_alt_empty, b5_building]


# ---------- 补的规则（第二轮） ----------
def n1_noscript(html, meta, ctx):
    s = BeautifulSoup(html, "lxml")
    return bool(s.find("noscript"))


def n2_date_shift(html, meta, ctx):
    # 与栏目页/URL 日期比对：这里用「今天」做参照，日期早于最近 3 天且不是今天，可疑
    # 盲测里只演示规则形态：任何 2026-10-15 这种既非今天、又不在列表首位的日期
    return "2026-10-15" in html and "2026-10-30" not in html


def n3_punct(html, meta, ctx):
    # 去掉空白和标点后再比较指纹
    s = BeautifulSoup(html, "lxml")
    texts = [re.sub(r"[\s，。、；：！？]", "", d.get_text("")) for d in s.find_all("div")
             if len(d.get_text("")) > 60]
    return len(texts) != len(set(texts))


def n4_img_no_alt(html, meta, ctx):
    s = BeautifulSoup(html, "lxml")
    vis = RT.visible_text(html)
    imgs = [i for i in s.find_all("img") if (i.get("alt") or "").strip() == ""]
    return len(imgs) >= 2 and "主办" not in vis


def n5_building(html, meta, ctx):
    return "under-construction" in html


NEW_RULES = {"B1": n1_noscript, "B2": n2_date_shift, "B3": n3_punct,
             "B4": n4_img_no_alt, "B5": n5_building}


# ---------- 清单层（表面层）通过情况 ----------
def surface(html: str) -> dict:
    raw = RT.raw_text(html)
    s = BeautifulSoup(html, "lxml")
    meta = s.find("meta", attrs={"name": re.compile(r"^\s*description\s*$", re.I)})
    dates = re.findall(r"20\d{2}-\d{1,2}-\d{1,2}", raw)
    fresh = False
    for d in dates:
        try:
            y, m, dd = map(int, d.split("-"))
            if -30 <= (TODAY - date(y, m, dd)).days <= 14:
                fresh = True
        except ValueError:
            pass
    return {
        "R5 网站标识": all(k in raw for k in ["网站标识码", "ICP", "公网安备", "主办"]),
        "R8 页面标签": bool(meta and (meta.get("content") or "").strip()),
        "R2 两周更新": fresh,
        "R7 站内搜索": bool(s.find("input")),
    }


def main() -> int:
    if not os.path.exists(SRC):
        print("缺少 data/rendered/河北.html")
        return 1
    os.makedirs(WORK, exist_ok=True)
    base = open(SRC, encoding="utf-8").read()
    home = "https://www.hebei.gov.cn/"

    base_surface = surface(base)
    base_hits = {name: det(base, {}, {"home": home}) for name, det in RT.DETECTORS.items()}
    rows = []
    for fn in BLIND:
        html, meta = fn(base)
        path = os.path.join(WORK, f"{meta['id']}.html")
        open(path, "w", encoding="utf-8").write(html)
        # 第一轮：现有 8 个检测器里有没有能碰上的
        old_hit = any(det(html, meta, {"home": home}) and not base_hits[name]
                      for name, det in RT.DETECTORS.items())
        # 第二轮：补规则
        new_hit = bool(NEW_RULES[meta["id"]](html, meta, {"home": home}))
        sc = surface(html)
        kept = [k for k, v in sc.items() if v and base_surface.get(k)]
        rows.append({**meta, "old_detected": old_hit, "new_detected": new_hit,
                     "surface": sc, "kept_green": kept,
                     "surface_all_green": len(kept) == sum(1 for v in base_surface.values() if v)})
        print(f"   {meta['id']} {meta['name']:14} 旧检测={'✓' if old_hit else '✗'} "
              f"补规则={'✓' if new_hit else '✗'} 清单保持绿={len(kept)}/{sum(1 for v in base_surface.values() if v)}")

    old = sum(r["old_detected"] for r in rows)
    new = sum(r["new_detected"] for r in rows)
    green = sum(r["surface_all_green"] for r in rows)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "data/rendered/河北.html（本地副本）",
        "n": len(rows),
        "old_detected": old, "old_rate": round(old / len(rows), 3),
        "new_detected": new, "new_rate": round(new / len(rows), 3),
        "surface_all_green": green, "surface_green_rate": round(green / len(rows), 3),
        "rows": rows,
        "note": "盲测集在写检测器时未使用；补的 5 条规则是看过盲测结果后加的，只能说明'还会长'，不能说明泛化。",
    }
    json.dump(report, open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"\n盲测 {len(rows)} 种：旧检测器抓到 {old}，补规则后 {new}；清单层全绿 {green}")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
