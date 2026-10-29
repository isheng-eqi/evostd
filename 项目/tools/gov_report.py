"""把体检结果渲染成一页 HTML 报告（现场演示用）。

用法：
    cd 项目
    python tools/gov_report.py --site 河北
输出：
    submission/report-河北.html
"""
from __future__ import annotations

import argparse
import html
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JUDGMENTS = os.path.join(ROOT, "data", "m1-judgments.json")
OUT_DIR = os.path.join(ROOT, "submission")

CLAUSES = {
    "R1": "单项否决：监测 1 周，超过 15 秒打不开的次数占比 ≥5%",
    "R2": "单项否决：监测 2 周，首页无信息更新",
    "R3": "发布解读：政策文件类一级栏目 6 个月内未更新，扣 5 分",
    "R4": "发布解读：政策解读类一级栏目 6 个月内未更新，扣 5 分",
    "R5": "网站标识：未列明标识码/ICP/公安备案/主办单位/联系方式，每缺一项扣 0.5 分",
    "R6": "功能设计：未添加“我为政府网站找错”入口，扣 1 分",
    "R7": "功能设计：未提供全站搜索或不可用，扣 4 分",
    "R8": "页面标签：内容页无站点标签或内容标签，每个扣 0.1 分",
    "R9": "可用性：首页链接打不开或错误，每条扣 0.2 分",
    "R10": "IPv6：未按要求完成改造，扣 1 分",
    "R11": "办事指南：重点要素每缺一处扣 0.5 分",
    "R12": "单项否决：出现严重表述错误",
    "R13": "单项否决：未提供有效咨询建言渠道，或留言超 3 个月未回应",
    "R14": "兼容性 / 一号登录：不能正常显示每类扣 1 分；未一号登录扣 2 分",
}

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Microsoft YaHei","PingFang SC",sans-serif;background:#f5f7fa;color:#0f172a}
.header{background:#0b1220;color:#fff;padding:40px 56px 32px}
.kicker{color:#7fb0ff;font-size:14px;letter-spacing:1px;margin-bottom:10px}
h1{font-size:34px;font-weight:700}
.url{color:#9aa7bd;font-size:14px;margin-top:8px}
.chips{display:flex;gap:12px;margin-top:24px}
.chip{padding:10px 18px;border-radius:10px;font-size:15px;font-weight:600}
.chip.pass{background:#123524;color:#4ade80;border:1px solid #1f6f46}
.chip.fail{background:#3a1216;color:#f87171;border:1px solid #7f1d1d}
.chip.abstain{background:#3a2c10;color:#fbbf24;border:1px solid #78601a}
.main{padding:32px 56px 56px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:16px 18px}
.card.fail{border-color:#fecaca;background:#fff7f7}
.card.abstain{border-color:#fde68a;background:#fffdf3}
.card.pass{border-color:#dcfce7}
.top{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.id{font-size:12px;color:#64748b;font-weight:700}
.name{font-size:16px;font-weight:700}
.dot{width:8px;height:8px;border-radius:50%;margin-left:auto}
.dot.pass{background:#22c55e}.dot.fail{background:#ef4444}.dot.abstain{background:#f59e0b}
.detail{font-size:13px;line-height:1.6;color:#334155;margin-bottom:8px}
.clause{font-size:12px;line-height:1.5;color:#94a3b8;border-top:1px dashed #e2e8f0;padding-top:8px}
.footer{margin-top:28px;font-size:12px;color:#94a3b8;line-height:1.8}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--judgments", default=JUDGMENTS)
    args = ap.parse_args()

    data = json.load(open(args.judgments, encoding="utf-8"))
    site = next((s for s in data["results"] if s["site"] == args.site), None)
    if not site:
        print(f"没有 {args.site} 的结果")
        return 1

    counts = {"pass": 0, "fail": 0, "abstain": 0}
    for r in site["rules"]:
        counts[r["status"]] += 1

    cards = []
    order = {"fail": 0, "abstain": 1, "pass": 2}
    for r in sorted(site["rules"], key=lambda x: (order[x["status"]], int(x["id"][1:]))):
        rid, st = r["id"], r["status"]
        label = {"pass": "通过", "fail": "不合格", "abstain": "弃权"}[st]
        cards.append(f"""
        <div class="card {st}">
          <div class="top"><span class="id">{rid}</span><span class="name">{html.escape(r['name'])}</span>
            <span class="dot {st}"></span></div>
          <div class="detail"><b>{label}</b>：{html.escape(r['detail'])}</div>
          <div class="clause">{html.escape(CLAUSES.get(rid, ''))}</div>
        </div>""")

    doc = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{html.escape(args.site)} · 合规体检报告</title><style>{CSS}</style></head><body>
<div class="header">
  <div class="kicker">政府网站信息公开 · 合规体检</div>
  <h1>{html.escape(args.site)}</h1>
  <div class="url">{html.escape(site['url'])}</div>
  <div class="chips">
    <div class="chip pass">通过 {counts['pass']}</div>
    <div class="chip fail">不合格 {counts['fail']}</div>
    <div class="chip abstain">弃权 {counts['abstain']}</div>
  </div>
</div>
<div class="main">
  <div class="grid">{''.join(cards)}</div>
  <div class="footer">
    判定依据：《政府网站与政务新媒体检查指标》。<br>
    判定只基于页面文本与链接；证据不足时明确弃权，不判“不合格”。<br>
    生成时间：{time.strftime('%Y-%m-%d %H:%M')}
  </div>
</div></body></html>"""

    out = os.path.join(OUT_DIR, f"report-{args.site}.html")
    open(out, "w", encoding="utf-8", newline="\n").write(doc)
    print(f"已生成 {out}（{counts}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
