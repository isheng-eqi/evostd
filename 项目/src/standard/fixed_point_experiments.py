"""★ 不动点方向的三项一次性检验（docs/30）。

E1  裁决规则：命中裁决 vs 消融归因裁决 —— 误纳率与辨别力
E2  索取策略：随机 vs 触发式 vs 证伪优先
E3  硬锚点：从标准条款**机械抽取**的真值，能替代多少人工确认？

跑法（在 项目/src 目录下）：python -m standard.fixed_point_experiments
"""
from __future__ import annotations

import hashlib
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from standard.compiler import (  # noqa: E402
    Clause, ClauseKind, _CHECKER_BY_KIND, compile_standard,
)
from standard.grammar_v2 import make_checker  # noqa: E402
from standard.samples import CLAUSES, CONTROLLED_STANDARDS  # noqa: E402

RETIRED_STORAGE = ["oss://retired/legacy-2019"]
RETIRED_ASSETS = ["AST-2019-0007"]
WITHDRAWN_STANDARDS = ["GB/T 11111-2005"]
OK_STORAGE = "oss://assets/2026/ok"

CTX = {
    "controlled_standards": CONTROLLED_STANDARDS,
    "retired_storage": RETIRED_STORAGE,
    "retired_assets": RETIRED_ASSETS,
    "withdrawn_standards": WITHDRAWN_STANDARDS,
    "bogus_registry": [OK_STORAGE],
}


def _h(n: int) -> str:
    return hashlib.sha256(str(n).encode()).hexdigest()


def compliant_base(i: int = 0) -> dict:
    return {"asset_id": f"AST-2026-{i:04d}", "modality": "表格", "secrecy": "内部",
            "owner_dept": "设备管理部", "collect_time": "2026-03-01",
            "ingest_time": "2026-03-05", "quality_score": 80.0, "completeness": 0.9,
            "file_hash": _h(i), "cited_standards": ["GB/T 33863.1-2017"],
            "storage_uri": OK_STORAGE}


# ---------------------------------------------------------------------------
#  候选判据池：3 真 + 2 诱饵（D2 会误报）
# ---------------------------------------------------------------------------
def candidate_pool():
    return [
        (Clause("C1", "存储位置不得失效", ClauseKind.FORBIDDEN,
                "存储位置不得指向已下线条目。",
                {"field": "storage_uri", "registry": "retired_storage"},
                standard="（自提案）"), True),
        (Clause("C2", "资产标识不得注销", ClauseKind.FORBIDDEN,
                "资产标识不得指向已注销条目。",
                {"field": "asset_id", "registry": "retired_assets"},
                standard="（自提案）"), True),
        (Clause("C3", "引用标准不得废止", ClauseKind.FORBIDDEN,
                "引用标准不得为已废止标准。",
                {"field": "cited_standards", "registry": "withdrawn_standards"},
                standard="（自提案）"), True),
        (Clause("D1", "质量评分下限", ClauseKind.RANGE,
                "质量评分应不低于 60。",
                {"field": "quality_score", "min": 60.0}, standard="（自提案）"), False),
        (Clause("D2", "存储位置黑名单（错误）", ClauseKind.FORBIDDEN,
                "存储位置不得属于该清单。",
                {"field": "storage_uri", "registry": "bogus_registry"},
                standard="（自提案）"), False),
    ]


def make_samples(rng: random.Random, n_each: int = 30) -> list[dict]:
    out = []
    for i in range(n_each):
        a = compliant_base(i); a["_label"] = "compliant"; out.append(a)
    for i in range(n_each):
        a = compliant_base(1000 + i); a["storage_uri"] = RETIRED_STORAGE[0]
        a["_label"] = "violating"; out.append(a)
    for i in range(n_each):
        a = compliant_base(2000 + i); a["asset_id"] = RETIRED_ASSETS[0]
        a["_label"] = "violating"; out.append(a)
    for i in range(n_each):
        a = compliant_base(3000 + i); a["cited_standards"] = [WITHDRAWN_STANDARDS[0]]
        a["_label"] = "violating"; out.append(a)
    rng.shuffle(out)
    return out


def fires(chk, sample: dict) -> bool:
    return not chk.check(sample, CTX).ok


def f1(lib, items) -> tuple[float, int, int]:
    tp = fp = fn = 0
    for s in items:
        pred = any(fires(c, s) for c in lib)
        truth = s["_label"] == "violating"
        tp += int(pred and truth); fp += int(pred and not truth); fn += int(not pred and truth)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    return (round(2 * p * r / (p + r), 4) if (p + r) else 0.0), fp, fn


# ---------------------------------------------------------------------------
#  E3：从标准条款机械抽取"硬锚点"
# ---------------------------------------------------------------------------
def derive_hard_anchors(clauses) -> list[tuple[dict, str, str]]:
    """★ 不需要人：条款文本自己就定义了可判定的真值。返回 (资产, 标签, 来源条款)。"""
    out = []
    for c in clauses:
        if not c.compilable:
            continue
        p, f = c.params, c.params.get("field")
        if c.kind == ClauseKind.ENUM:
            for v in p["allowed"]:
                a = compliant_base(9000 + len(out)); a[f] = v
                out.append((a, "compliant", c.clause_id))
            a = compliant_base(9500 + len(out)); a[f] = "∉受控词表的值"
            out.append((a, "violating", c.clause_id))
        elif c.kind == ClauseKind.REQUIRED:
            a = compliant_base(9600 + len(out)); a.pop(f, None)
            out.append((a, "violating", c.clause_id))
        elif c.kind == ClauseKind.RANGE and f == "quality_score":
            lo, hi = p.get("min"), p.get("max")
            if lo is not None:
                a = compliant_base(9700 + len(out)); a[f] = lo - 1
                out.append((a, "violating", c.clause_id))
            if hi is not None:
                a = compliant_base(9800 + len(out)); a[f] = hi + 1
                out.append((a, "violating", c.clause_id))
    return out


# ---------------------------------------------------------------------------
#  一次运行
# ---------------------------------------------------------------------------
def run_once(rule: str, strategy: str, budget: int, seed: int, use_hard: bool):
    rng = random.Random(seed)
    pool = [(c, make_checker(c), real) for c, real in candidate_pool()]
    samples = make_samples(rng)
    unconf = list(samples)

    seed_lib = list(compile_standard(CLAUSES).checkers)
    hard = derive_hard_anchors(CLAUSES) if use_hard else []

    ev = {c.clause_id: {"caught": 0, "fa": 0, "decided": False, "attrib": 0}
          for c, _, _ in pool}
    label_seen_compliant = [0]      # ★ 必须在 observe 被调用前定义

    def observe(sample, label, source):
        """source: 'hard' = 机械锚点（免费），'human' = 人工确认"""
        for clause, chk, _ in pool:
            st = ev[clause.clause_id]
            if st["decided"]:
                continue
            if fires(chk, sample):
                if label == "violating":
                    st["caught"] += 1
                    # ★ 消融归因：只有"扣住它，原本就抓不到"才算它的贡献
                    if not any(fires(c, sample) for c in seed_lib):
                        st["attrib"] += 1
                else:
                    st["fa"] += 1
            if st["fa"] > 0:
                st["decided"] = True
            elif rule == "naive" and st["caught"] >= 1 and label_seen_compliant[0]:
                st["decided"] = True
            elif rule == "ablation" and st["attrib"] >= 1 and label_seen_compliant[0]:
                st["decided"] = True

    # 硬锚点先上（免费）
    for a, lab, src in hard:
        observe(a, lab, "hard")

    # 人工确认（付费）
    n_human = 0
    for _ in range(budget):
        if not unconf:
            break
        if strategy == "random":
            s = rng.choice(unconf)
        elif strategy == "trigger":
            s = max(unconf, key=lambda x: sum(
                1 for cl, ch, _ in pool if not ev[cl.clause_id]["decided"] and fires(ch, x)))
        else:  # falsification：优先索取"能推翻候选判据"的样本（合规样本优先）
            ok = [x for x in unconf if x["_label"] == "compliant"]
            s = rng.choice(ok) if ok else rng.choice(unconf)
        unconf.remove(s)
        lbl = s["_label"]
        if lbl == "compliant":
            label_seen_compliant[0] += 1
        n_human += 1
        observe(s, lbl, "human")

    key = "caught" if rule == "naive" else "attrib"
    admitted = [chk for c, chk, _ in pool
                if ev[c.clause_id][key] >= 1 and ev[c.clause_id]["fa"] == 0]
    lib = seed_lib + admitted
    bad = [c.clause_id for c, chk, _ in pool if chk in admitted and not _]
    f, fp, fn = f1(lib, unconf)
    return {"false_admitted": bad, "f1": f, "fp": fp, "fn": fn,
            "n_human": n_human, "n_hard": len(hard)}


def main() -> None:
    print("=" * 88)
    print("不动点方向的三项一次性检验")
    print("=" * 88)

    # ---------- E3 先跑：硬锚点能抽多少 ----------
    hard = derive_hard_anchors(CLAUSES)
    compiled = compile_standard(CLAUSES)
    from collections import Counter
    src = Counter(s for _, _, s in hard)
    comp = sum(1 for a, l, _ in hard if l == "compliant")
    viol = sum(1 for a, l, _ in hard if l == "violating")
    print(f"\n【E3】硬锚点机械化抽取（无需任何人工）")
    print(f"  可编译条款 {len(compiled.checkers)} 条 → 机械产出锚点 **{len(hard)} 条**"
          f"（合规 {comp} / 违规 {viol}）")
    print(f"  来源条款：{dict(src)}")
    print(f"  → 这些锚点**完全免费、外部、无争议**，因为条款文本自己就定义了真值。")

    # ---------- E1 / E2 ----------
    print(f"\n【E1+E2】裁决规则 × 索取策略（预算=8 次人工确认，30 个种子）")
    print(f"  {'裁决规则':<14}{'索取策略':<18}{'用硬锚点':<10}{'误纳D2比例':<12}{'平均F1':<10}")
    print("-" * 88)
    rows = []
    for rule in ("naive", "ablation"):
        for strat in ("random", "trigger", "falsify"):
            for use_hard in (False, True):
                d2, f1s = [], []
                for seed in range(30):
                    r = run_once(rule, strat, 8, seed, use_hard)
                    d2.append(1 if "D2" in r["false_admitted"] else 0)
                    f1s.append(r["f1"])
                m = statistics.mean(d2); f = statistics.mean(f1s)
                rows.append((rule, strat, use_hard, m, f))
                print(f"  {rule:<14}{strat:<18}{str(use_hard):<10}{m:<12.2f}{f:.4f}")

    print("\n【判定】")
    base = [r for r in rows if r[0] == "naive" and r[1] == "random" and not r[2]][0]
    best = max(rows, key=lambda r: (r[4], -r[3]))
    print(f"  基线（命中裁决 + 随机索取 + 无硬锚点）：误纳 {base[3]:.2f}，F1 {base[4]:.4f}")
    print(f"  最优组合：{best[0]} + {best[1]} + 硬锚点={best[2]} → "
          f"误纳 {best[3]:.2f}，F1 {best[4]:.4f}")
    abl = [r for r in rows if r[0] == "ablation" and r[1] == "random" and not r[2]][0]
    print(f"  仅换裁决规则（命中→消融归因）：误纳 {base[3]:.2f}→{abl[3]:.2f}，"
          f"F1 {base[4]:.4f}→{abl[4]:.4f}")
    h = [r for r in rows if r[0] == "ablation" and r[1] == "random" and r[2]][0]
    print(f"  再加硬锚点：误纳 {abl[3]:.2f}→{h[3]:.2f}，F1 {abl[4]:.4f}→{h[4]:.4f}")
    print("=" * 88)


if __name__ == "__main__":
    main()
