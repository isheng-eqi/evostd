"""自进化内在回路演示：两个 Domain Pack 共用同一个内核。

跑法（在 项目/src 目录下）：
    python -m evolve.run_demo

证明四件事：
  1. 回路能真的产生**代际上升曲线**（不是写死的，也不是模型自评的）
  2. **对照闸门**能对每条新引入的规则/模板做"扣住/施加"消融并给出裁决
  3. **审计**能抓出"代理指标涨、真实指标没涨"的漂移与"验证器无法区分好坏"的退化解
  4. **同一个内核，换 Domain Pack 即可换领域**——内核一行不改
"""
from __future__ import annotations

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evolve.core import (  # noqa: E402
    Archive, Auditor, Candidate, ContrastGate, EvalResult, EvolutionLoop, Ledger,
)
from evolve.domains.ontology_growth import OntologyGrowthDomain  # noqa: E402
from evolve.domains.skill_templates import SkillTemplateDomain  # noqa: E402

RUNS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runs")
GENERATIONS = 40
SEED = 42


# ---------------------------------------------------------------------------
def run_one(domain, label: str) -> dict:
    out_dir = os.path.join(RUNS_DIR, label)
    os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(SEED)

    evolve_v, heldout_v = domain.make_verifiers()

    print("\n" + "=" * 78)
    print(f"▶ {label}   领域包 = {domain.name}")
    print(f"  验证器 = {evolve_v.name}（确定性：同输入同输出）")
    print("=" * 78)

    auditor = Auditor(evolve_v)
    for name, cand in domain.probes():
        auditor.add_probe(name, cand)

    loop = EvolutionLoop(
        domain=domain, verifier=evolve_v, mutator=domain.mutator,
        archive=Archive(n_islands=3, capacity_per_island=40),
        gate=None,                       # 内核按 verifier.deterministic 自动选阈值
        auditor=auditor,
        ledger=Ledger(os.path.join(out_dir, "ledger.jsonl")),
        seed=SEED, out_dir=out_dir,
    )
    report = loop.run(generations=GENERATIONS, plateau_patience=12)

    # ---- 代际曲线 ----
    print("\n  【代际曲线】gen | 本代 | 存档最优 | 存档规模")
    print("  " + "-" * 60)
    for row in report["curve"]:
        gs = "  —  " if row["gen_score"] is None else f"{row['gen_score']:.4f}"
        bs = "  —  " if row["best_score"] is None else f"{row['best_score']:.4f}"
        print(f"  {row['gen']:>4} | {gs} |  {bs}  | {row['archive_size']:>3}")

    # ---- 最优候选 ----
    best_id = report["best"]["cid"]
    best = loop.archive.cands[best_id]
    ev = evolve_v.evaluate(best)
    ho = heldout_v.evaluate(best)
    gap = round(ev.score - ho.score, 4)

    print(f"\n  【最优候选 {best_id}】")
    if domain.kind == "extraction_rule":
        for r in best.payload["rules"]:
            print(f"    - {r['feature']} {r['op']} {r['value']}")
    else:
        for t in best.payload["library"]:
            print(f"    - [{t['when']}] {t['name']}: {' → '.join(t['steps'])}")
    print(f"    进化集 score = {ev.score:.4f}   {ev.metrics}")
    print(f"    保留集 score = {ho.score:.4f}   {ho.metrics}")

    print(f"\n  【代理-真实落差】gap = {gap:+.4f}  "
          f"{'✅ 提升是真的' if abs(gap) <= 0.05 else '★ 疑似过拟合，报警'}")

    # ---- 审计 ----
    a = report["audit"]
    print(f"\n  【审计】对抗探针通过率 = {a['probe_pass_rate']}（1.0 = 坏候选全部被排在后面）")
    for p in a["probes"]:
        print(f"    {'✓' if p['correctly_rejected'] else '✗'} {p['probe']:<16} score={p['score']:.4f}")
    c = a["collapse"]
    print(f"    塌缩检测 = {c.get('verdict')}  (spread={c.get('spread')}, n={c.get('n')})")

    v = report["run_validity"]
    if "margin" in v:
        print(f"    运行有效性：最优 {v['best_score']} vs 最差探针 {v['worst_probe_score']}"
              f"  margin={v['margin']:+.4f}")
    print(f"    判定：{v['verdict']}")

    # ---- 闸门 ----
    print(f"\n  【对照闸门】记录 {report['gate']['n_records']} 条，"
          f"裁决分布 {report['gate']['verdicts']}，判有害 {report['gate']['harmful'] or '（无）'}")

    print(f"\n  【预算】评估 {report['budget']['evals']} 次，"
          f"无效候选 {report['budget']['invalid']}，墙钟 {report['budget']['wall_s']}s")

    report["proxy_reality_gap"] = gap
    report["domain"] = domain.name
    report["label"] = label
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


# ---------------------------------------------------------------------------
def gate_demo_ontology(domain: OntologyGrowthDomain, rng: random.Random) -> None:
    """闸门有效性演示：故意引入一条有害规则，看闸门能否判它有害。"""
    base_rules = [{"feature": "def_count", "op": ">=", "value": 2}]
    bad_rule = {"feature": "freq", "op": ">=", "value": 20}
    parent = Candidate("demo-parent", domain.kind, {"rules": base_rules})
    child = Candidate("demo-child", domain.kind, {"rules": base_rules + [bad_rule]})
    sig = domain.contrast_signals(parent, child, EvalResult(valid=True, score=0.0), rng)
    g = ContrastGate(min_n=1, consecutive=1)
    for key, helped in sig.items():
        g.observe(key, "guided", helped)
        g.observe(key, "withheld", not helped)
        g.judge(key)
    print("\n" + "=" * 78)
    print("▶ 闸门有效性演示（扣住 / 施加 配对消融）")
    print(f"  母本：def_count>=2              → 得分 {domain._score(base_rules):.4f}")
    print(f"  子代：再加陷阱规则 freq>=20     → 得分 {domain._score(base_rules + [bad_rule]):.4f}")
    for k, rec in g.records.items():
        print(f"  · 规则 {k}: delta={rec.delta:+.2f} → 裁决「{rec.verdict}」")
        print(f"    {rec.reason}")


def main() -> None:
    print("=" * 78)
    print("自进化内核 · 两个 Domain Pack 共用同一套机制")
    print("  内核 = core.py（候选/验证器协议/闸门/存档/审计/台账/主循环）")
    print("  领域 = domains/*.py（只实现 语料 + 验证器 + 变异算子 + 探针）")
    print("=" * 78)

    r1 = run_one(OntologyGrowthDomain(seed=7), "demo-ontology")
    r2 = run_one(SkillTemplateDomain(seed=11), "demo-skill-templates")
    gate_demo_ontology(OntologyGrowthDomain(seed=7), random.Random(SEED))

    print("\n" + "=" * 78)
    print("【两个领域的汇总】")
    print(f"  {'领域':<28}{'最优':>8}{'保留集':>9}{'落差':>9}{'探针':>7}{'有效性':>8}")
    for r in (r1, r2):
        print(f"  {r['domain']:<28}{r['best']['score']:>8.4f}"
              f"{(r['best']['score'] - r['proxy_reality_gap']):>9.4f}"
              f"{r['proxy_reality_gap']:>+9.4f}"
              f"{r['audit']['probe_pass_rate']:>7}"
              f"{('ok' if 'ok' == r['run_validity']['verdict'] else 'INVALID'):>8}")
    print("=" * 78)


if __name__ == "__main__":
    main()
