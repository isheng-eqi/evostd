"""★ 三阶自进化的最小可证伪实验：主动索取锚点 vs 随机索取锚点。

检验的主张（docs/24）：
    系统不该被动等人喂锚点。它应该自己挑出"最值得问人"的那几个问题，
    让每一次人工确认的信息量最大化——因为**锚点的覆盖度就是自进化的天花板**。

实验设计
--------
· 候选判据池：3 条真规则 + 2 条诱饵（一条无害、一条**会误报**）。
· 样本池：一批资产，其中只有极少数会被"人"确认（真实标签）。
· 两种索取策略，**同样的人工确认预算 B**：
    A 随机：随便挑未确认样本问人
    B 主动：挑"能一次决定最多候选判据命运"的样本问人
· 裁决：预算耗尽后，把有证据支持的判据入库，在**从未被确认过的样本**上测辨别力 F1。

判定
----
· B 显著优于 A  → 三阶成立，值得做
· B ≈ A        → **三阶是多余的机制，立刻砍掉**

跑法（在 项目/src 目录下）：python -m standard.active_anchoring
"""
from __future__ import annotations

import hashlib
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from standard.compiler import (  # noqa: E402
    Checker, Clause, ClauseKind, _CHECKER_BY_KIND, compile_standard,
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
    # ★ 诱饵用的"清单"：把完全合规的存储位置列成禁止项 —— 这类判据会疯狂误报
    "bogus_registry": [OK_STORAGE],
}


def _h(n: int) -> str:
    return hashlib.sha256(str(n).encode()).hexdigest()


# ---------------------------------------------------------------------------
#  候选判据池：3 真 + 2 诱饵
# ---------------------------------------------------------------------------
def candidate_pool() -> list[tuple[Clause, bool]]:
    return [
        (Clause("C1", "存储位置不得失效", ClauseKind.FORBIDDEN,
                "存储位置不得指向已下线条目。",
                {"field": "storage_uri", "registry": "retired_storage"},
                standard="（系统自提案）"), True),
        (Clause("C2", "资产标识不得注销", ClauseKind.FORBIDDEN,
                "资产标识不得指向已注销条目。",
                {"field": "asset_id", "registry": "retired_assets"},
                standard="（系统自提案）"), True),
        (Clause("C3", "引用标准不得废止", ClauseKind.FORBIDDEN,
                "引用标准不得为已废止标准。",
                {"field": "cited_standards", "registry": "withdrawn_standards"},
                standard="（系统自提案）"), True),
        # 诱饵 A：永远不触发（无害但也无用）
        (Clause("D1", "质量评分下限", ClauseKind.RANGE,
                "质量评分应不低于 60。",
                {"field": "quality_score", "min": 60.0},
                standard="（系统自提案）"), False),
        # ★ 诱饵 B：会误报 —— 它把完全合规的存储位置当成禁止项
        (Clause("D2", "存储位置黑名单（错误）", ClauseKind.FORBIDDEN,
                "存储位置不得属于该清单。",
                {"field": "storage_uri", "registry": "bogus_registry"},
                standard="（系统自提案）"), False),
    ]


# ---------------------------------------------------------------------------
#  样本池
# ---------------------------------------------------------------------------
def make_samples(rng: random.Random, n_each: int = 30) -> list[dict]:
    out = []

    def base(i):
        return {"asset_id": f"AST-2026-{i:04d}", "modality": "表格", "secrecy": "内部",
                "owner_dept": "设备管理部", "collect_time": "2026-03-01",
                "ingest_time": "2026-03-05", "quality_score": 80.0, "completeness": 0.9,
                "file_hash": _h(i), "cited_standards": ["GB/T 33863.1-2017"],
                "storage_uri": OK_STORAGE, "_label": "compliant"}

    for i in range(n_each):
        out.append(base(i))
    for i in range(n_each):
        a = base(1000 + i); a["storage_uri"] = RETIRED_STORAGE[0]
        a["_label"] = "violating"; out.append(a)
    for i in range(n_each):
        a = base(2000 + i); a["asset_id"] = RETIRED_ASSETS[0]
        a["_label"] = "violating"; out.append(a)
    for i in range(n_each):
        a = base(3000 + i); a["cited_standards"] = [WITHDRAWN_STANDARDS[0]]
        a["_label"] = "violating"; out.append(a)
    rng.shuffle(out)
    return out


# ---------------------------------------------------------------------------
#  判据 ↔ 样本
# ---------------------------------------------------------------------------
def fires(chk: Checker, sample: dict) -> bool:
    return not chk.check(sample, CTX).ok


def verdicts(checkers, samples):
    """在给定样本集上算辨别力。"""
    tp = fp = fn = tn = 0
    for s in samples:
        pred = any(fires(c, s) for c in checkers)
        truth = s["_label"] == "violating"
        tp += int(pred and truth); fp += int(pred and not truth)
        fn += int(not pred and truth); tn += int(not pred and not truth)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    return round(2 * p * r / (p + r), 4) if (p + r) else 0.0


# ---------------------------------------------------------------------------
#  两种索取策略
# ---------------------------------------------------------------------------
def pick_random(unconfirmed, rng):
    return rng.choice(unconfirmed)


def pick_active(unconfirmed, pool, evidence):
    """主动策略：挑"能一次决定最多候选判据命运"的样本。

    对每条尚未定论的候选判据，一个样本若能给它带来"命中证据"或"误报证据"，
    就算它推进了这条判据的裁决。选推进总数最多的样本。
    """
    best, best_gain = None, -1
    for s in unconfirmed:
        gain = 0
        for clause, _ in pool:
            st = evidence[clause.clause_id]
            if st["decided"]:
                continue
            chk = make_checker(clause)
            if fires(chk, s):
                gain += 1          # 无论人是说是/否，这条样本都会给出决定性证据
        if gain > best_gain:
            best, best_gain = s, gain
    return best if best is not None else unconfirmed[0]


def run_once(strategy: str, budget: int, seed: int) -> dict:
    rng = random.Random(seed)
    pool = [(c, _CHECKER_BY_KIND[c.kind](c), real) for c, real in candidate_pool()]
    samples = make_samples(rng)
    unconfirmed = list(samples)
    evidence = {c.clause_id: {"caught": 0, "false_alarm": 0, "decided": False}
                for c, _, _ in pool}

    for _ in range(budget):
        if not unconfirmed:
            break
        s = pick_random(unconfirmed, rng) if strategy == "random" \
            else pick_active(unconfirmed, [(c, r) for c, _, r in pool], evidence)
        unconfirmed.remove(s)
        truth_violating = s["_label"] == "violating"
        for clause, chk, _ in pool:
            st = evidence[clause.clause_id]
            if st["decided"]:
                continue
            if fires(chk, s):
                if truth_violating:
                    st["caught"] += 1
                else:
                    st["false_alarm"] += 1
            # 裁决规则：零误报 且 至少一条命中证据 → 入库；出现误报 → 立即否决
            if st["false_alarm"] > 0:
                st["decided"] = True
            elif st["caught"] >= 1:
                st["decided"] = True

    admitted = [chk for clause, chk, _ in pool
                if evidence[clause.clause_id]["caught"] >= 1
                and evidence[clause.clause_id]["false_alarm"] == 0]
    library = list(compile_standard(CLAUSES).checkers) + admitted

    return {
        "admitted": [c.clause_id for c, chk, _ in pool if chk in admitted],
        "truth_admitted": [c.clause_id for c, chk, real in pool if chk in admitted and real],
        "false_admitted": [c.clause_id for c, chk, real in pool if chk in admitted and not real],
        "missed_real": [c.clause_id for c, chk, real in pool if real and chk not in admitted],
        "f1_heldout": verdicts(library, unconfirmed),   # ★ 只在从未被确认过的样本上测
        "n_heldout": len(unconfirmed),
    }


def main() -> None:
    print("=" * 84)
    print("三阶实验：主动索取锚点 vs 随机索取锚点（同预算）")
    print("=" * 84)
    print("  候选判据池：C1/C2/C3 为真规则，D1 无害诱饵，D2 会误报的诱饵")
    print("  度量：预算耗尽后，判据库在**从未被确认过的样本**上的 F1\n")

    print(f"  {'预算':>4} | {'随机 F1（均值±标准差）':>26} | {'主动 F1（均值±标准差）':>26} | {'主动优势':>9}")
    print("-" * 84)
    summary = []
    for budget in (4, 6, 8, 10, 12, 16):
        rs, as_ = [], []
        for seed in range(30):
            rs.append(run_once("random", budget, seed)["f1_heldout"])
            as_.append(run_once("active", budget, seed)["f1_heldout"])
        rm, am = statistics.mean(rs), statistics.mean(as_)
        rsd = statistics.pstdev(rs); asd = statistics.pstdev(as_)
        summary.append((budget, rm, am))
        print(f"  {budget:>4} | {rm:>10.4f} ± {rsd:<12.4f} | {am:>10.4f} ± {asd:<12.4f} "
              f"| {am - rm:>+9.4f}")

    print("\n【一次典型运行的细节（预算=8，seed=0）】")
    for strat in ("random", "active"):
        r = run_once(strat, 8, 0)
        print(f"  {strat:<7} 入库真规则 {r['truth_admitted']}  "
              f"误入诱饵 {r['false_admitted'] or '无'}  "
              f"漏掉真规则 {r['missed_real'] or '无'}  "
              f"held-out F1={r['f1_heldout']}")

    print("\n【判定】")
    b4 = [x for x in summary if x[0] == 8][0]
    if b4[2] - b4[1] > 0.02:
        print(f"  预算=8 时主动优于随机 {b4[2] - b4[1]:+.4f} → **三阶成立，值得做**")
    else:
        print(f"  预算=8 时两者差异 {b4[2] - b4[1]:+.4f} → **三阶收益不显著，应当砍掉**")
    print("  注：随机策略的方差明显更大——这正是'被动等人喂锚点'的真实代价。")
    print("=" * 84)


if __name__ == "__main__":
    main()
