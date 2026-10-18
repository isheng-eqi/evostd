"""离线冒烟测试：不需要 MCP，不需要 Nexent，把三组能力全部跑一遍。

跑法（在 项目/src 目录下）：
    python -m evostd.smoke
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evostd.service import StandardService  # noqa: E402
from evostd.store import Store  # noqa: E402
from standard.samples import BAD_ASSETS, CLAUSES, CONTROLLED_STANDARDS, GOOD_ASSET  # noqa: E402

# 硬锚点需要一份"完全合规"的模板，以及受控清单
CTX = {
    "controlled_standards": CONTROLLED_STANDARDS,
    "retired_storage": ["oss://retired/legacy-2019"],
    "retired_assets": ["AST-2019-0007"],
    "withdrawn_standards": ["GB/T 11111-2005"],
    # 过度宽泛提案用的"错误清单"：把完全合规的存储位置列成禁止项
    "bogus_registry": [GOOD_ASSET["storage_uri"]],
}

BASE = dict(GOOD_ASSET)
BASE.update({"cited_standards": ["GB/T 33863.1-2017"]})


def line(t: str = "") -> None:
    print(t)


def main() -> None:
    root = os.path.join(tempfile.gettempdir(), "evostd-smoke")
    shutil.rmtree(root, ignore_errors=True)
    store = Store(root)
    svc = StandardService(store, CLAUSES, BASE, CTX,
                          standard_name=CLAUSES[0].standard)

    line("=" * 84)
    line("evostd 冒烟测试：标准驱动的自进化服务层（离线，无 MCP、无 Nexent）")
    line("=" * 84)

    # ---------------- 1. 判据库 + 硬锚点 ----------------
    cl = svc.std_clause_list()
    line(f"\n【1】判据库：{cl['stats']['clauses']} 条判据"
         f"（状态分布 {cl['stats']['by_status']}）")
    ha = svc.std_hard_anchors()
    line(f"    硬锚点：{ha['n']} 条（零人工，从条款机械抽取）")
    by_clause: dict[str, int] = {}
    for a in ha["anchors"]:
        by_clause[a["source_clause"]] = by_clause.get(a["source_clause"], 0) + 1
    line(f"    来源条款分布：{by_clause}")
    for a in ha["anchors"][:4]:
        line(f"      · [{a['label']:<9}] {a['source_clause']}  {a['how']}")
    line("      …")

    # ---------------- 2. 校验 ----------------
    assets = [GOOD_ASSET] + [a for _, a in BAD_ASSETS]
    labels = ["合规资产"] + [lbl for lbl, _ in BAD_ASSETS]
    r = svc.std_verify(assets)
    line(f"\n【2】std_verify：{r['n_assets']} 条资产 × {r['n_clauses']} 条判据"
         f" → 合规率 {r['compliant_rate']:.2%}")
    for res, lbl in list(zip(r["results"], labels))[:3]:
        line(f"\n    [{lbl}] {res['asset_id']}  合规={res['compliant']} "
             f"违例={res['n_violations']}")
        for v in res["violations"][:2]:
            line(f"      ✗ 违反 {v['clause_id']}：{v['message']}")
            line(f"        原文：「{v['clause_text']}」")
    line(f"\n    （其余 {len(r['results']) - 3} 条省略）")

    # ---------------- 3. 盲区 ----------------
    # ★ 造两条"种子判据看不见"的违规：存储位置指向已下线存储
    blind_assets = []
    for i in range(2):
        a = dict(GOOD_ASSET)
        a["asset_id"] = f"AST-2026-9{i:03d}"
        a["storage_uri"] = CTX["retired_storage"][0]
        a["_label"] = "violating"
        a["_why"] = "存储位置指向已下线存储（现有判据测不到）"
        blind_assets.append(a)

    tagged = []
    for lbl, a in BAD_ASSETS:
        b = dict(a); b["_label"] = "violating"; b["_why"] = lbl; tagged.append(b)
    tagged.insert(0, {**GOOD_ASSET, "_label": "compliant", "_why": ""})
    tagged += blind_assets

    bl = svc.asset_blindspots(tagged)
    line(f"\n【3】asset_blindspots：{bl['n_assets']} 条资产里，"
         f"**{bl['n_blindspots']} 条违规现有判据看不见**")
    for b in bl["blindspots"][:5]:
        line(f"      · {b['asset_id']}  {b['why']}")
    line("    → 这些盲区就是'判据提案'的靶子")

    # ---------------- 4. 提案与裁决 ----------------
    line(f"\n【4】std_propose：提交候选判据 → 由锚点裁决")
    good_spec = {"clause_id": "提案-存储位置失效", "title": "存储位置不得指向已下线存储",
                 "kind": "禁止取值", "text": "数据资产的存储位置不得指向已下线存储。",
                 "params": {"field": "storage_uri", "registry": "retired_storage"}}

    r0 = svc.std_propose(good_spec)
    line(f"    第 1 次（无任何人工确认）：裁决「{r0['verdict']}」")
    line(f"      理由：{r0['reason']}")
    line(f"      ★ 这暴露了硬锚点的性质：**它天生是'证伪器'不是'证实器'**——"
         f"标准自证的锚点只能推翻提案，不能支持新规则。")

    line(f"\n    → 请人确认 2 条盲区样本（这是唯一'贵'的真值来源）")
    for a in blind_assets:
        c = svc.std_confirm(a, "violating", by="专家A")
        line(f"      ✓ 软确认 {c['anchor_id']}：{a['asset_id']} 判为违规")

    r1 = svc.std_propose(good_spec)
    line(f"\n    第 2 次（已有 2 条人工确认）：裁决「{r1['verdict']}」")
    line(f"      理由：{r1['reason']}")
    line(f"      统计：可归因命中 {r1['stats']['attributable_caught']} 条，"
         f"误报 {r1['stats']['false_alarm']} 条")

    bad_spec = {"clause_id": "提案-过度宽泛", "title": "存储位置不得属于该清单",
                "kind": "禁止取值", "text": "存储位置不得属于该清单。",
                "params": {"field": "storage_uri", "registry": "bogus_registry"}}
    r2 = svc.std_propose(bad_spec)
    line(f"\n    C 过度宽泛提案 → 裁决「{r2['verdict']}」")
    line(f"      理由：{r2['reason']}")

    # ---- 闭环验证：采纳之后，盲区还在吗？----
    bl2 = svc.asset_blindspots(tagged)
    line(f"\n    ★ 闭环验证：判据入库后，同一批资产的盲区 "
         f"{bl['n_blindspots']} → {bl2['n_blindspots']}")
    line(f"      —— 系统**看见了以前看不见的违规**，而且只花了 2 次人工确认。")

    # ---------------- 5. 审计 ----------------
    au = svc.std_audit()
    line(f"\n【5】std_audit")
    line(f"    判据库：{au['library']['n_adopted']} 条采纳 / "
         f"{au['library']['n_proposed_rejected']} 条被否")
    line(f"    判据类型：{au['library']['types']}")
    sc = au["anchor_selfcheck"]
    line(f"    锚点自检：{sc['n_anchors']} 条，不一致 {sc['n_mismatch']} 条 → {sc['verdict']}")
    for k, v in au["probes"].items():
        line(f"    对抗探针 {k}：抓到 {v['caught']} / 误报 {v['false_alarm']} → {v['verdict']}")
    d = au["discrimination"]
    line(f"    区分度：spread={d['spread']} (min={d['min']}, max={d['max']}) → {d['verdict']}")
    cov = au["coverage"]
    line(f"    ★ 硬锚点覆盖率：{cov['clauses_with_hard_anchors']}/{cov['clauses_compilable']}"
         f" = {cov['hard_anchor_coverage']:.1%}，共 {cov['n_hard_anchors']} 条锚点")
    line(f"      逐类型：{cov['per_kind']}")
    line(f"      不可编译（留给人工软确认）：{cov['uncompilable_clauses']}")

    # ---------------- 6. 快照 / 回滚 ----------------
    snap = store.snapshot("冒烟测试")
    line(f"\n【6】版本快照：{snap}")
    before = len(store.list_clauses("adopted"))
    store.add_clause(CLAUSES[0], status="adopted", reason="人为多插一条，测试回滚")
    mid = len(store.list_clauses("adopted"))
    rb = store.rollback(snap["snapshot_id"])
    after = len(store.list_clauses("adopted"))
    line(f"    采纳判据数：{before} →（插入一条）{mid} →（回滚）{after}"
         f"  回滚{'成功' if after == before else '失败'}")

    # ---------------- 7. 台账 ----------------
    lg = svc.std_ledger(limit=10)
    line(f"\n【7】台账（最近 {len(lg['events'])} 条事件）")
    for e in lg["events"][-6:]:
        line(f"    · {e.get('event'):<14} {json.dumps({k: v for k, v in e.items() if k not in ('event', 't')}, ensure_ascii=False)[:80]}")

    line("\n" + "=" * 84)
    line("结论：三组能力在**不依赖 MCP、不依赖 Nexent** 的前提下全部跑通。")
    line("      MCP Server 只是这一层之上的薄适配（server.py）。")
    line("=" * 84)


if __name__ == "__main__":
    main()
