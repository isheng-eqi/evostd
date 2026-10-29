"""从体检结果导出知识图谱（站点 → 判定 → 检查项 → 条款）。

为什么要有图：一条判定不是孤立的。它属于某个站点、对应某条检查项、依据某条条款。
导出成三元组后，可以回答「这个站哪些条款没通过」「这条检查项在多少个站上弃权」。

用法：
    cd 项目
    python tools/gov_kg.py --out data/gov-kg.json
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CLAUSES = {
    "R1": "单项否决：监测期内超过 15 秒打不开的次数占比 ≥5%",
    "R2": "单项否决：监测 2 周，首页无信息更新",
    "R3": "政策文件类一级栏目 6 个月内未更新，扣 5 分",
    "R4": "政策解读类一级栏目 6 个月内未更新，扣 5 分",
    "R5": "网站标识五项，每缺一项扣 0.5 分",
    "R6": "未添加“我为政府网站找错”入口，扣 1 分",
    "R7": "未提供全站搜索或不可用，扣 4 分",
    "R8": "内容页无站点标签或内容标签，每个扣 0.1 分",
    "R9": "首页链接打不开或错误，每条扣 0.2 分",
    "R10": "未完成 IPv6 改造，扣 1 分",
    "R11": "办事指南重点要素每缺一处扣 0.5 分",
    "R12": "单项否决：出现严重表述错误",
    "R13": "单项否决：未提供有效咨询渠道或超 3 个月未回应",
    "R14": "兼容性 / 一号登录",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--judgments", default=os.path.join(ROOT, "data", "m1-judgments.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "gov-kg.json"))
    args = ap.parse_args()

    data = json.load(open(args.judgments, encoding="utf-8"))
    nodes, edges = [], []

    for rid, text in CLAUSES.items():
        nodes.append({"id": f"clause:{rid}", "type": "条款", "label": text})

    for site in data["results"]:
        nodes.append({"id": f"site:{site['site']}", "type": "站点",
                      "label": site["site"], "url": site["url"]})
        for r in site["rules"]:
            vid = f"verdict:{site['site']}:{r['id']}"
            nodes.append({"id": vid, "type": "判定", "label": r["status"],
                          "detail": r["detail"][:80]})
            edges.append({"from": f"site:{site['site']}", "to": vid, "rel": "有一项判定"})
            edges.append({"from": vid, "to": f"clause:{r['id']}", "rel": "依据条款"})

    # 规则节点（检查项）合并到条款节点，避免重复
    stats = Counter(r["status"] for s in data["results"] for r in s["rules"])
    out = {
        "schema": {"entities": ["站点", "判定", "条款"],
                   "relations": ["有一项判定", "依据条款"]},
        "counts": {"nodes": len(nodes), "edges": len(edges),
                   "sites": len(data["results"]),
                   "verdicts": stats["pass"] + stats["fail"] + stats["abstain"],
                   "pass": stats["pass"], "fail": stats["fail"], "abstain": stats["abstain"]},
        "nodes": nodes,
        "edges": edges,
    }
    json.dump(out, open(args.out, "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=1)
    c = out["counts"]
    print(f"节点 {c['nodes']} · 边 {c['edges']} · 站点 {c['sites']} · 判定 {c['verdicts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
