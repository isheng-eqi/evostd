"""实验：让进化回路**直接搜索判据库**（把两半接起来）。

在此之前，项目里的"自进化"是两套互不相连的东西：
  · `evo_*` 进化回路 —— 进化合成适应度（ontology / skill 模板），与标准无关
  · 判据库          —— 由标准编译 + 人工提案，**不参与搜索**

本实验把它们接上：

    候选   = 一组判据（从机械生成的候选池里选，池里**故意混入过宽/过窄的坏判据**）
    适应度 = 在**合成评测集**上的辨别力 F1（真值来自语料表，不是拍的）
    硬门   = **零误报**（R2）—— 只要误伤一条合规资产，该候选直接判 0

看点：进化能不能**仅凭外部真值**，从"看起来都合理"的参数里选出对的那个。

复跑：python -m evolve.clause_library_experiment
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evolve.core import Archive, Auditor, EvolutionLoop, Ledger
from evolve.domains.clause_library import (ClauseLibraryDomain, ClauseLibraryVerifier,
                                           build_candidate_pool,
                                           synthesize_labeled_assets)

SEED = 11
GENERATIONS = 20


def build():
    from domains.manufacturing import standards
    from domains.manufacturing.assets import ctx as actx
    from standard.compiler import compile_standard
    from standard.grammar_v2_experiment import extracted_ctx, v2_clauses

    std = standards()
    base = [ck.clause for ck in compile_standard(std).checkers]
    have = {b.clause_id for b in base}
    base += [c for c in v2_clauses(getattr(std, "STANDARD_NAME", ""), "manufacturing")
             if c.clause_id not in have]
    ctx = dict(actx())
    ctx.update(extracted_ctx())
    ctx["now"] = "2026-10-13"
    assets = synthesize_labeled_assets()
    v = ClauseLibraryVerifier(assets, base, ctx)
    return v, ClauseLibraryDomain(v, seed=SEED), assets


def main() -> int:
    v, d, assets = build()
    W = 78
    print("=" * W)
    print("实验：进化回路直接搜索判据库（两半接上）")
    print(f"评测集：{len(assets)} 条合成样本（合规 {sum(1 for a in assets if a['_label']=='compliant')}"
          f" / 违规 {sum(1 for a in assets if a['_label']=='violating')}）")
    pool = build_candidate_pool()
    print(f"候选池：{len(pool)} 条（正确 ×{sum(1 for s in pool if s['clause_id'].endswith('-exact'))}"
          f" / 过宽 ×{sum(1 for s in pool if s['clause_id'].endswith('-wide'))}"
          f" / 过窄 ×{sum(1 for s in pool if s['clause_id'].endswith('-tight'))}）")
    print("=" * W)

    auditor = Auditor(v)
    for name, cand in d.probes():
        auditor.add_probe(name, cand)
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "runs",
                           "clause-library")
    os.makedirs(out_dir, exist_ok=True)
    loop = EvolutionLoop(domain=d, verifier=v, mutator=d.mutator,
                         archive=Archive(n_islands=3, capacity_per_island=40),
                         gate=None, auditor=auditor,
                         ledger=Ledger(os.path.join(out_dir, "ledger.jsonl")),
                         seed=SEED, out_dir=out_dir)
    rep = loop.run(generations=GENERATIONS, plateau_patience=10)

    print("\n  【代际曲线】gen | 本代得分 | 存档最优")
    print("  " + "-" * 56)
    for h in rep.get("curve", [])[:GENERATIONS]:
        print(f"    {h.get('gen', 0):>4} | {h.get('gen_score', 0):.4f}     | "
              f"{h.get('best_score', 0):.4f}")

    best_cid = loop.archive.best()
    best = loop.archive.cands[best_cid]
    r = v.evaluate(best)
    picked = [s["clause_id"] for s in best.payload.get("specs", [])]
    print()
    print("=" * W)
    print(f"最优候选：{best.cid}")
    print(f"  运行有效性：{rep.get('run_validity')}")
    rv = rep.get("run_validity") or {}
    print(f"  闸门裁决：{rep.get('gate', {}).get('verdicts')}")
    print(f"  选中的判据（{len(picked)} 条）：")
    for p in picked:
        tag = p.split("-")[-1]
        mark = "✓正确" if tag == "exact" else ("✗过宽" if tag == "wide" else "✗过窄")
        print(f"     {p:<30} {mark}")
    m = r.metrics
    print(f"  得分 {r.score:.4f} | F1 {m.get('f1')} | 精确率 {m.get('precision')} | "
          f"召回率 {m.get('recall')}")
    print(f"  混淆矩阵：TP {m.get('tp')} / FP {m.get('fp')} / FN {m.get('fn')} / TN {m.get('tn')}")
    if m.get("gated_by_R2"):
        print("  ★ 该候选被 R2（零误报）硬门拦截")
    print("=" * W)

    # ---- 结论：进化有没有仅凭外部真值挑出正确参数 ----
    n_exact = sum(1 for p in picked if p.endswith("-exact"))
    n_bad = sum(1 for p in picked if p.endswith(("-wide", "-tight")))
    print()
    if n_exact >= 2 and n_bad == 0 and r.score > 0:
        print("★ 结论：进化**仅凭外部真值**，从好坏混杂的候选池里")
        print("  选出了正确参数、淘汰了过宽与过窄的坏判据 —— 两条曲线接上了。")
    else:
        print(f"△ 结论：选中 正确 {n_exact} 条 / 坏 {n_bad} 条，得分 {r.score:.4f}，见上。")
    print("=" * W)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
