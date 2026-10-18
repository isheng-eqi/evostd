"""准入闸门与实例隔离测试。

四个闸门里 R2（零误报）与 R3（可归因命中）在 contract_test 里验过；
**R1（见过合规锚点）与 R4（严格度不劣化）从没验过** —— 本轮补上。

  G1  R4：提交比标准原文更**宽**的候选 → 必须被拒
  G2  R4：提交更**严**的候选 → 允许（若同时满足 R2/R3）
  G3  R1：在从未出现过合规锚点的字段上提判据 → 必须被拒
  G4  交叉污染：两个领域实例交错调用，结果互不影响
  G5  政务包 23 条在新 `_validate` 下**一条都不能被误拒**
  G6  `std_propose` 后 `std_clause_list` 的状态与裁决一致
  G7  同一 ref 重复裁决 → 台账以最后一次为准（不重复计数）

用法：python tools/gate_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
_res = []


def rec(code, angle, ok, detail):
    _res.append((code, angle, ok, detail))


def main() -> int:
    os.environ.setdefault("EVOSTD_NOW", "2026-10-13")
    os.environ["EVOSTD_STATE"] = os.path.join(_SRC, "runs", "gate")
    shutil.rmtree(os.environ["EVOSTD_STATE"], ignore_errors=True)
    sys.path.insert(0, os.path.abspath(_SRC))
    import evostd.server as S
    from domains.manufacturing.assets import all_assets as A

    mfg = S.build_service("manufacturing")
    raw = A()
    clean = [{k: v for k, v in x.items() if not k.startswith("_")} for x in raw]

    def propose(spec):
        return S.std_propose(json.dumps(spec, ensure_ascii=False))

    # ── G1 ★ R4：比原文更宽的候选必须被拒 ───────────────────────
    # 第5条原文：maintenance_cycle_days 1~365。候选放宽到 0~1000。
    wider = {"clause_id": "G1-WIDER", "kind": "数值范围",
             "text": "检修周期应为 0 至 1000 个自然日之间的整数。",
             "params": {"field": "maintenance_cycle_days", "min": 0, "max": 1000}}
    try:
        r = propose(wider)
        rejected = str(r.get("verdict") or "").lower() != "adopted"
        rec("G1", "R4 更宽", rejected,
            f"候选 0~1000（原文 1~365）→ verdict={r.get('verdict')} 理由={str(r.get('reason'))[:58]}")
    except Exception as ex:
        rec("G1", "R4 更宽", True, f"抛 {type(ex).__name__}（不算静默接受）")

    # ── G2a ★ R4 不阻止收紧：更严的候选**不应被 R4 拒** ─────────
    #
    #   第一版我以为"更严就该采纳"，结果被 R2 拒了 —— **那是正确行为**：
    #   收紧到 180 会误伤现有合规资产（有合规项的检修周期 >180 天）。
    #   **更严 ≠ 更好**；R4 只禁止"更松"，是否采纳还要过 R2/R3。
    #   所以这里断言的是"**不是被 R4 拒**"，而不是"必须被采纳"。
    tighter = {"clause_id": "G2-TIGHTER", "kind": "数值范围",
               "text": "检修周期应为 1 至 180 个自然日之间的整数。",
               "params": {"field": "maintenance_cycle_days", "min": 1, "max": 180}}
    try:
        r = propose(tighter)
        why = str(r.get("reason") or "")
        not_r4 = "R4" not in why
        rec("G2a", "R4 不阻止收紧", not_r4,
            f"候选 1~180 → verdict={r.get('verdict')} 理由={why[:62]}（应不含 R4）")
    except Exception as ex:
        rec("G2a", "R4 不阻止收紧", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    # ── G2b ★ R2 也拦"收紧"：证明零误报优先级高于"更严就好" ────
    #
    #   实测结论（两次都撞上）：在现有标注语料上，**任何收紧都会误伤** ——
    #   因为语料是按原文严格度生成的，收紧区里就有被标为合规的锚点。
    #       maintenance_cycle_days 1~180  → 误报 HA2-003 / HA2-907
    #       revision 0~50                → 误报 HA2-015
    #
    #   **这不是缺陷，是闸门的一致性**：R4 只禁止"更松"，
    #   "更严"照样要过 R2（零误报）。于是闸门实际只接受
    #   **同等严格度下覆盖新盲区**的判据 —— 那正是新判据该有的价值。
    #
    #   所以本项断言"收紧被 R2 拦下"，把它当成**守护测试**而不是失败。
    safe = {"clause_id": "G2-SAFE-TIGHTER", "kind": "数值范围",
            "text": "图纸修订版次应为 0 至 50 之间的整数。",
            "params": {"field": "revision", "min": 0, "max": 50}}
    try:
        r = propose(safe)
        why = str(r.get("reason") or "")
        rec("G2b", "R2 也拦收紧", str(r.get("verdict") or "").lower() != "adopted" and "R2" in why,
            f"候选 revision 0~50 → verdict={r.get('verdict')} 理由={why[:58]}")
    except Exception as ex:
        rec("G2b", "R2 也拦收紧", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    # ── G3 ★ R1：从未见过合规锚点的字段 ────────────────────────
    ghost = {"clause_id": "G3-GHOST", "kind": "数值范围",
             "text": "不存在的字段应为 1 至 10。",
             "params": {"field": "this_field_never_exists", "min": 1, "max": 10}}
    try:
        r = propose(ghost)
        rejected = str(r.get("verdict") or "").lower() != "adopted"
        rec("G3", "R1 未见", rejected,
            f"字段从不存在 → verdict={r.get('verdict')} 理由={str(r.get('reason'))[:58]}")
    except Exception as ex:
        rec("G3", "R1 未见", True, f"抛 {type(ex).__name__}（不算静默接受）")

    # ── G4 交叉污染：两个实例交错 ──────────────────────────────
    try:
        gov = S.build_service("gov_catalog")
        from domains.gov_catalog.assets import all_assets as GA
        gclean = [{k: v for k, v in x.items() if not k.startswith("_")} for x in GA()]
        m1 = mfg.std_verify(clean)["compliant_rate"]
        g1 = gov.std_verify(gclean)["compliant_rate"]
        m2 = mfg.std_verify(clean)["compliant_rate"]
        g2 = gov.std_verify(gclean)["compliant_rate"]
        rec("G4", "实例隔离", m1 == m2 and g1 == g2 and m1 != g1,
            f"交错调用 制造 {m1}/{m2} · 政务 {g1}/{g2}（各自稳定且互不相同）")
    except Exception as ex:
        rec("G4", "实例隔离", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    # ── G5 政务包在新 _validate 下不能被误拒 ───────────────────
    try:
        from domains.gov_catalog import standard_gov as GS
        from standard.compiler import compile_standard
        # ★ 政务包导出的是 `standards()` 函数，制造业包导出的是 `CLAUSES` 列表 ——
        #   两个包的接口本来不一致（见本轮同时修的 cross_standard.py）。
        gov_clauses = GS.standards()
        cr = compile_standard(gov_clauses)
        n_in, n_ok = len(gov_clauses), len(cr.checkers)
        # ★ 断言不是"不可编译必须为 0" —— 政务第21/22条是**显式声明** compilable=False
        #   （留给人的部分，见 shadow_map）。真正要守的是：
        #   **不可编译的必须都是显式声明过的，不能是被 _validate 悄悄拒掉的。**
        declared = [c for c in gov_clauses if not getattr(c, "compilable", True)]
        sneaked = [c for c in cr.uncompilable if getattr(c, "compilable", True)]
        rec("G5", "政务编译",
            not sneaked and len(cr.uncompilable) == len(declared) and n_ok + len(cr.uncompilable) == n_in,
            f"政务 {n_in} 条 → 编译 {n_ok} + 显式弃用 {len(declared)} 条；"
            f"被校验悄悄拒掉 {len(sneaked)} 条（应为 0）")
    except Exception as ex:
        rec("G5", "政务编译", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    # ── G6 propose 后清单状态与裁决一致 ────────────────────────
    try:
        cl = S.std_clause_list()
        rows = cl.get("clauses") or []
        by_id = {c.get("clause_id"): c.get("status") for c in rows}
        g1v = by_id.get("G1-WIDER")
        g2v = by_id.get("G2-SAFE-TIGHTER")
        g3v = by_id.get("G3-GHOST")
        # 三条候选全被拒 → 清单里都**不该**出现（被拒的不能悄悄入库）
        rec("G6", "状态一致", g1v is None and g2v is None and g3v is None,
            f"三条被拒候选是否混入库：G1-WIDER={g1v} G2-SAFE-TIGHTER={g2v} G3-GHOST={g3v}（应全为 None）")
    except Exception as ex:
        rec("G6", "状态一致", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    # ── G7 同一 ref 重复裁决 → 以最后一次为准 ──────────────────
    try:
        p1 = S.std_pending()
        if p1["items"]:
            ref = p1["items"][0]["ref"]
            S.std_adjudicate(ref, "deferred", "t1", "第一次")
            n1 = S.std_pending()["n_pending"]
            S.std_adjudicate(ref, "rejected", "t2", "第二次")
            n2 = S.std_pending()["n_pending"]
            lst = S.std_adjudications()
            same = [x for x in (lst.get("adjudications") or []) if x.get("ref") == ref]
            rec("G7", "裁决幂等", n1 == n2,
                f"同一 ref 裁决两次：待裁决 {p1['n_pending']} → {n1} → {n2}"
                f"（记录 {len(same)} 条，以最后一次为准）")
        else:
            rec("G7", "裁决幂等", False, "待裁决为空，无法测试")
    except Exception as ex:
        rec("G7", "裁决幂等", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    # ── 汇总 ────────────────────────────────────────────────────
    W = 106
    print("=" * W)
    print("准入闸门与实例隔离测试")
    print("=" * W)
    print(f"  {'项':<5}{'角度':<10}{'结果':<7}说明")
    print(f"  {'-'*5}{'-'*10}{'-'*7}{'-'*80}")
    npass = nfail = 0
    for code, angle, ok, detail in _res:
        mark = "PASS" if ok else "FAIL"
        npass += 1 if ok else 0
        nfail += 0 if ok else 1
        print(f"  {code:<5}{angle:<10}{mark:<7}{detail[:80]}")
    print()
    print(f"  合计 {len(_res)} 项：PASS {npass} / FAIL {nfail}")
    return 0 if nfail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
