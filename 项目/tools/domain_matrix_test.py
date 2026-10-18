"""领域矩阵：把关键检查在**两个领域**上各跑一遍。

为什么需要
----------
前面 6 个套件（multi_angle / extreme / contract / accountability / gate /
grammar_contract）**全部只跑制造业包**。
政务包只被验过三件事：判据活性、编译条数、盲区 2/2。

也就是说：**政务包没有多角度测试。** 一旦它有自己的坑（比如标识字段、语料、
受控表结构不同带来的差异），前面 6 个套件一个都看不见。

用法：python tools/domain_matrix_test.py
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
_res = []
DOMAINS = ("manufacturing", "gov_catalog")


def rec(code, dom, angle, ok, detail):
    _res.append((code, dom, angle, ok, detail))


def main() -> int:
    os.environ.setdefault("EVOSTD_NOW", "2026-10-13")
    os.environ["EVOSTD_STATE"] = os.path.join(_SRC, "runs", "domain_matrix")
    shutil.rmtree(os.environ["EVOSTD_STATE"], ignore_errors=True)
    sys.path.insert(0, os.path.abspath(_SRC))
    import evostd.server as S

    for dom in DOMAINS:
        svc = S.build_service(dom)
        try:
            mod = importlib.import_module(f"domains.{dom}.assets")
            raw = mod.all_assets()
        except Exception as ex:
            rec("M0", dom, "装配", False, f"导入资产失败 {type(ex).__name__}: {str(ex)[:50]}")
            continue
        clean = [{k: v for k, v in x.items() if not k.startswith("_")} for x in raw]
        comp = [a for a, r in zip(clean, raw) if r.get("_label") == "compliant"]
        viol = [a for a, r in zip(clean, raw) if r.get("_label") == "violating"]

        # M1 合规不误报
        r = svc.std_verify(clean)
        bad = [x["asset_id"] for x in r["results"]
               if x["asset_id"] in {a.get("asset_id") or a.get("catalog_id") for a in comp}
               and x["n_violations"] > 0]
        rec("M1", dom, "零误报", not bad,
            f"合规 {len(comp)} 条，误报 {len(bad)} 条 {bad[:3]}")

        # M2 违规全抓到（全量批次）
        byid = {x["asset_id"]: x for x in r["results"]}
        missed = []
        for a in viol:
            aid = a.get("asset_id") or a.get("catalog_id")
            if byid.get(aid, {}).get("n_violations", 0) == 0:
                missed.append(aid)
        rec("M2", dom, "零漏判", not missed,
            f"违规 {len(viol)} 条，漏判 {len(missed)} 条 {missed[:3]}")

        # M3 违例都带条款原文
        lack = [x["asset_id"] for x in r["results"]
                if x["n_violations"] > 0 and not x["violations"][0].get("clause_text")]
        rec("M3", dom, "可解释", not lack, f"违例缺条款原文 {len(lack)} 条")

        # M4 弃权可见（本次调用）
        ab = (r.get("abstained") or {})
        rec("M4", dom, "弃权可见",
            "n_abstained_clauses" in ab and "by_clause" in ab,
            f"本次弃权条款 {list((ab.get('by_clause') or {}).keys())}")

        # M5 确定性：同一输入两次完全一致
        r2 = svc.std_verify(clean)
        same = (r["compliant_rate"] == r2["compliant_rate"]
                and [x["n_violations"] for x in r["results"]]
                == [x["n_violations"] for x in r2["results"]])
        rec("M5", dom, "确定性", same,
            f"两次合规率 {r['compliant_rate']} / {r2['compliant_rate']}")

        # M6 判据活性：每条已采纳判据都被至少一条违规资产触发过
        fired = set()
        for x in r["results"]:
            for v in x["violations"]:
                fired.add(v["clause_id"])
        total = len(svc._lib_checkers())
        rec("M6", dom, "判据活性", len(fired) == total,
            f"被判据数 {total} / 实际触发 {len(fired)}（差 {total - len(fired)}）")

        # M7 每条违例的 clause_id 必须真实存在于库里
        cl = {c.get("clause_id") for c in (S.std_clause_list().get("clauses") or [])}
        ghost = {v["clause_id"] for x in r["results"] for v in x["violations"]
                 if v["clause_id"] not in cl}
        rec("M7", dom, "引用真实", not ghost, f"引用不存在的条款 {len(ghost)} 个 {list(ghost)[:3]}")

    W = 110
    print("=" * W)
    print("领域矩阵：同样的检查，两个领域各跑一遍")
    print("=" * W)
    print(f"  {'项':<4}{'领域':<14}{'角度':<10}{'结果':<7}说明")
    print(f"  {'-'*4}{'-'*14}{'-'*10}{'-'*7}{'-'*70}")
    npass = nfail = 0
    for code, dom, angle, ok, detail in _res:
        mark = "PASS" if ok else "FAIL"
        npass += 1 if ok else 0
        nfail += 0 if ok else 1
        print(f"  {code:<4}{dom:<14}{angle:<10}{mark:<7}{detail[:70]}")
    print()
    print(f"  合计 {len(_res)} 项：PASS {npass} / FAIL {nfail}")
    return 0 if nfail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
