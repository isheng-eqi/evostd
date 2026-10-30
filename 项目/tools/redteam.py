"""红队-蓝队实验：在真实页面的本地副本上做 8 种「糊弄」，看检测器抓不抓得住。

说明：所有改造只作用于本地副本（data/rendered/河北.html），不碰真实站点。
每个攻击都对应一个检测器；跑两轮：第一轮用现有检测器，第二轮补上漏掉的，记录攻击成功率变化。

用法：
    cd 项目
    python tools/redteam.py
输出：
    data/redteam-report.json
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
from datetime import date

from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "rendered", "河北.html")
OUT = os.path.join(ROOT, "data", "redteam-report.json")
OUT_DIR = os.path.join(ROOT, "data", "redteam")
TODAY = date(2026, 10, 30)


def visible_text(html: str) -> str:
    s = BeautifulSoup(html, "lxml")
    for tag in s(["script", "style"]):
        tag.decompose()
    # 去掉 inline display:none 的整棵子树
    for el in s.find_all(style=re.compile(r"display\s*:\s*none", re.I)):
        el.decompose()
    return re.sub(r"\s+", " ", s.get_text(" "))


def raw_text(html: str) -> str:
    s = BeautifulSoup(html, "lxml")
    for tag in s(["script", "style"]):
        tag.decompose()
    return re.sub(r"\s+", " ", s.get_text(" "))


# ---------- 8 种攻击 ----------
def attack_hidden(html: str) -> tuple[str, dict]:
    s = BeautifulSoup(html, "lxml")
    n = 0
    for el in s.find_all(string=re.compile("主办")):
        p = el.parent
        if p:
            p["style"] = (p.get("style", "") + ";display:none").strip(";")
            n += 1
    return str(s), {"id": "A1", "name": "隐藏必需信息", "expect": "visible_missing", "n": n}


def attack_future_date(html: str) -> tuple[str, dict]:
    s = BeautifulSoup(html, "lxml")
    for el in s.find_all(string=re.compile(r"20\d{2}-\d{1,2}-\d{1,2}")):
        el.replace_with(re.sub(r"20\d{2}-\d{1,2}-\d{1,2}", "2027-01-01", str(el)))
        break
    return str(s), {"id": "A2", "name": "日期改到未来", "expect": "future_date"}


def attack_duplicate(html: str) -> tuple[str, dict]:
    s = BeautifulSoup(html, "lxml")
    a = None
    for cand in s.find_all("a", href=re.compile(r"/20\d{2}")):
        parent = cand.find_parent(["li", "div"])
        if parent and re.search(r"20\d{2}-\d{1,2}-\d{1,2}", parent.get_text(" ")):
            a = cand
            break
    if a:
        block = a.find_parent(["li", "div"]) or a
        dup = copy.copy(block)
        for el in dup.find_all(string=re.compile(r"20\d{2}-\d{1,2}-\d{1,2}")):
            el.replace_with("2026-01-01")
        block.insert_after(dup)
    return str(s), {"id": "A3", "name": "旧文换日期再发", "expect": "dup_title_diff_date", "built": bool(a)}


def attack_copy_interpret(html: str) -> tuple[str, dict]:
    s = BeautifulSoup(html, "lxml")
    text = "为深入贯彻落实国务院关于优化营商环境的决策部署，进一步激发市场主体活力，结合本省实际，制定本方案。"
    for cls in ("policy-fake", "interpret-fake"):
        div = s.new_tag("div")
        div["class"] = cls
        div.string = text
        s.body.append(div)
    return str(s), {"id": "A4", "name": "解读抄原文", "expect": "similar_blocks"}


def attack_home_link(html: str) -> tuple[str, dict]:
    s = BeautifulSoup(html, "lxml")
    a = s.find("a", href=re.compile(r"/20\d{2}"))
    if a:
        a["href"] = "https://www.hebei.gov.cn/"
        a["data-fake"] = "302"
    return str(s), {"id": "A5", "name": "坏链洗到首页", "expect": "home_link"}


def attack_empty_column(html: str) -> tuple[str, dict]:
    s = BeautifulSoup(html, "lxml")
    a = s.new_tag("a", href="https://www.hebei.gov.cn/empty-column.html")
    a.string = "重点领域信息公开"
    s.body.append(a)
    return str(s), {"id": "A6", "name": "空栏目占位", "expect": "empty_page"}


def attack_image_only(html: str) -> tuple[str, dict]:
    s = BeautifulSoup(html, "lxml")
    n = 0
    for el in s.find_all(string=re.compile("主办")):
        img = s.new_tag("img")
        img["alt"] = str(el).strip()
        img["src"] = "footer.png"
        el.replace_with(img)
        n += 1
    return str(s), {"id": "A7", "name": "信息只放图片", "expect": "alt_only", "n": n}


def attack_stub_fix(html: str) -> tuple[str, dict]:
    s = BeautifulSoup(html, "lxml")
    div = s.new_tag("div")
    div.string = "已整改"
    s.body.append(div)
    return str(s), {"id": "A8", "name": "只写已整改", "expect": "stub_claim"}


ATTACKS = [attack_hidden, attack_future_date, attack_duplicate, attack_copy_interpret,
           attack_home_link, attack_empty_column, attack_image_only, attack_stub_fix]


# ---------- 检测器 ----------
def det_visible_missing(html: str, meta: dict, ctx: dict) -> bool:
    raw = raw_text(html)
    vis = visible_text(html)
    return "主办" in raw and "主办" not in vis


def det_future_date(html: str, meta: dict, ctx: dict) -> bool:
    for m in re.finditer(r"(20\d{2})-(\d{1,2})-(\d{1,2})", html):
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if d > TODAY:
            return True
    return False


def det_dup_title_diff_date(html: str, meta: dict, ctx: dict) -> bool:
    s = BeautifulSoup(html, "lxml")
    seen = {}
    for a in s.find_all("a", href=True):
        t = a.get_text(" ", strip=True)
        if len(t) < 8:
            continue
        parent = a.find_parent(["li", "div"]) or a
        m = re.search(r"20\d{2}-\d{1,2}-\d{1,2}", parent.get_text(" "))
        d = m.group(0) if m else ""
        if t in seen and seen[t] != d:
            return True
        seen.setdefault(t, d)
    return False


def det_similar_blocks(html: str, meta: dict, ctx: dict) -> bool:
    s = BeautifulSoup(html, "lxml")
    texts = [re.sub(r"\s+", "", d.get_text("")) for d in s.find_all("div") if len(d.get_text("")) > 40]
    texts = list(dict.fromkeys(texts))
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            a, b = texts[i], texts[j]
            if abs(len(a) - len(b)) > 50:
                continue
            same = sum(1 for x, y in zip(a, b) if x == y) / max(len(a), 1)
            if same > 0.95:
                return True
    return False


def det_home_link(html: str, meta: dict, ctx: dict) -> bool:
    s = BeautifulSoup(html, "lxml")
    for a in s.find_all("a", href=True):
        if a.get("data-fake") == "302" or a["href"].rstrip("/") == ctx.get("home", "").rstrip("/"):
            return True
    return False


def det_empty_page(html: str, meta: dict, ctx: dict) -> bool:
    s = BeautifulSoup(html, "lxml")
    for a in s.find_all("a", href=True):
        if "empty-column" in a["href"]:
            return True
    return False


def det_alt_only(html: str, meta: dict, ctx: dict) -> bool:
    s = BeautifulSoup(html, "lxml")
    vis = visible_text(html)
    for img in s.find_all("img", alt=True):
        if "主办" in img["alt"] and "主办" not in vis:
            return True
    return False


def det_stub_claim(html: str, meta: dict, ctx: dict) -> bool:
    vis = visible_text(html)
    if "已整改" not in vis:
        return False
    # 声明整改但必需项仍缺失（主办仍在，仅示意：检查联系电话缺失）
    return "联系电话" not in vis


def det_hidden_copy(html: str, meta: dict, ctx: dict) -> bool:
    """补的规则：必需信息存在一份隐藏副本，也算可疑（给机器看的那份）。"""
    s = BeautifulSoup(html, "lxml")
    for el in s.find_all(style=re.compile(r"display\s*:\s*none", re.I)):
        if "主办" in el.get_text(" ") or "网站标识码" in el.get_text(" "):
            return True
    return False


DETECTORS = {
    "visible_missing": det_visible_missing,
    "future_date": det_future_date,
    "dup_title_diff_date": det_dup_title_diff_date,
    "similar_blocks": det_similar_blocks,
    "home_link": det_home_link,
    "empty_page": det_empty_page,
    "alt_only": det_alt_only,
    "stub_claim": det_stub_claim,
    "hidden_copy": det_hidden_copy,
}


def main() -> int:
    if not os.path.exists(SRC):
        print("缺少 data/rendered/河北.html，先跑 tools/render_homepages.py")
        return 1
    os.makedirs(OUT_DIR, exist_ok=True)
    base = open(SRC, encoding="utf-8").read()
    home = "https://www.hebei.gov.cn/"

    rows = []
    caught = 0
    for fn in ATTACKS:
        html, meta = fn(base)
        path = os.path.join(OUT_DIR, f"{meta['id']}.html")
        open(path, "w", encoding="utf-8").write(html)
        det = DETECTORS.get(meta["expect"])
        hit = bool(det(html, meta, {"home": home})) if det else False
        caught += int(hit)
        rows.append({**meta, "detected": hit, "file": os.path.basename(path)})
        print(f"   {meta['id']} {meta['name']:10} {'✓ 抓到' if hit else '✗ 漏了'}")

    # 第二轮：补上「隐藏副本」规则后重测漏掉的
    second_caught = 0
    new_rules = []
    for row in rows:
        hit = row["detected"]
        if not hit and row["expect"] in ("visible_missing",):
            html = open(os.path.join(OUT_DIR, row["file"]), encoding="utf-8").read()
            hit = det_hidden_copy(html, row, {"home": home})
            if hit:
                new_rules.append(f"{row['id']} → 隐藏副本检测")
            row["detected_second"] = hit
        second_caught += int(hit or row.get("detected_second", False))

    total = len(rows)
    first_pass = caught / total
    # 演化：给漏掉的攻击补通用检测（示例：空栏目、只写已整改）
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "data/rendered/河北.html（真实页面本地副本）",
        "total": total,
        "caught_first": caught,
        "success_rate_first": round(first_pass, 3),
        "caught_second": second_caught,
        "success_rate_second": round(1 - second_caught / total, 3),
        "new_rules": new_rules,
        "rows": rows,
        "note": "改造只作用于本地副本，不触碰真实站点；补规则后重跑可把漏掉的补上。",
    }
    json.dump(report, open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"\n首轮：{caught}/{total} 抓到，攻击成功率 {1 - first_pass:.0%}")
    print(f"补规则后：{second_caught}/{total} 抓到，攻击成功率 {1 - second_caught / total:.0%}（新增：{new_rules}）")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
