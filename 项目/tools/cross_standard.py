"""跨标准对齐：把两部标准各自编译，**机械算出**它们在共享字段上的关系。

为什么需要
----------
赛题要求②含"**标准体系对齐**"。单一标准无法证明这件事 ——
必须**至少两部标准**，并且能说清它们在同一个字段上是：

    一致     → 可互认
    A 更严   → A 可替代 B（单向包含）
    B 更严   → 同上，方向相反
    部分重叠 → 各自都有对方不允许的取值，不能互相替代
    冲突     → 取值域**不相交**，必须由标准化组织裁决
    仅一方覆盖 → 不构成对齐问题

★ 这些关系全部由**区间/集合运算**算出，不靠人工比对。

设计要点
--------
**两部标准各自独立编译，不合并。** 所以：

  · 运行中的判据库不变（仍是主标准那 38 条 / 103 个硬锚点）
  · 对齐关系是**算出来的一个视图**，不是第三份库

用法
----
    python tools/cross_standard.py
    python tools/cross_standard.py --json
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")


def _index(cls, std_name: str) -> tuple[dict, int, int]:
    """把一部标准编译后，整理成 {field: [ {kind, params, clause_id} ] }。"""
    from standard.compiler import compile_standard

    compiled = compile_standard(cls)
    n_in, n_compiled = len(cls), len(compiled.checkers)
    idx: dict = {}
    for ck in compiled.checkers:
        c = ck.clause
        p = getattr(c, "params", {}) or {}
        f = p.get("field") or p.get("left") or p.get("when_field")
        if not f:
            continue
        idx.setdefault(f, []).append({
            "kind": getattr(c.kind, "value", "?"),
            "params": p,
            "clause_id": c.clause_id,
            "standard": std_name,
        })
    return idx, n_in, n_compiled


def _cmp_range(a: dict, b: dict) -> tuple[str, str]:
    a1, a2 = a.get("min"), a.get("max")
    b1, b2 = b.get("min"), b.get("max")
    sa = (a1 if a1 is not None else float("-inf"), a2 if a2 is not None else float("inf"))
    sb = (b1 if b1 is not None else float("-inf"), b2 if b2 is not None else float("inf"))
    if sa == sb:
        return "一致", f"[{sa[0]}, {sa[1]}]"
    lo = max(sa[0], sb[0])
    hi = min(sa[1], sb[1])
    if lo > hi:
        return "冲突", f"A[{sa[1] and sa[0]},{sa[1]}] 与 B[{sb[0]},{sb[1]}] 不相交"
    a_in_b = sb[0] <= sa[0] and sa[1] <= sb[1]
    b_in_a = sa[0] <= sb[0] and sb[1] <= sa[1]
    if a_in_b:
        return "A 更严", f"A[{sa[0]},{sa[1]}] ⊂ B[{sb[0]},{sb[1]}]"
    if b_in_a:
        return "B 更严", f"B[{sb[0]},{sb[1]}] ⊂ A[{sa[0]},{sa[1]}]"
    return "部分重叠", f"交集 [{lo},{hi}]，但各有独占区间"


def _cmp_enum(a: dict, b: dict) -> tuple[str, str]:
    sa = set(a.get("allowed") or [])
    sb = set(b.get("allowed") or [])
    if sa == sb:
        return "一致", f"{sorted(sa)}"
    if not (sa & sb):
        return "冲突", f"A{sorted(sa)} 与 B{sorted(sb)} 无交集"
    if sa < sb:
        return "A 更严", f"A{sorted(sa)} ⊂ B{sorted(sb)}"
    if sb < sa:
        return "B 更严", f"B{sorted(sb)} ⊂ A{sorted(sa)}"
    return "部分重叠", f"交集 {sorted(sa & sb)}，B 独有 {sorted(sb - sa)}，A 独有 {sorted(sa - sb)}"


def _cmp_pattern(a: dict, b: dict) -> tuple[str, str]:
    pa, pb = a.get("pattern"), b.get("pattern")
    if pa == pb:
        return "一致", str(pa)
    return "需人工核对", f"A={pa!r} / B={pb!r}"


def compare(a_cls, a_name, b_cls, b_name) -> dict:
    ia, a_in, a_ok = _index(a_cls, a_name)
    ib, b_in, b_ok = _index(b_cls, b_name)

    rows = []
    for f in sorted(set(ia) & set(ib)):
        # 同一字段可能双方都有多条；逐对比较，取最"强"的结论
        for x in ia[f]:
            for y in ib[f]:
                if x["kind"] != y["kind"]:
                    # ★ 不同类别的判据是**互补**，不是冲突：例如 A 对同一字段既有
                    #   「条件必填」又有「受控词表」，与 B 的「受控词表」应比词表。
                    #   **只比同类**，跨类跳过 —— 否则会产出大量假"类型不同"。
                    continue
                elif x["kind"] == "数值范围":
                    verdict, detail = _cmp_range(x["params"], y["params"])
                elif x["kind"] == "受控词表":
                    verdict, detail = _cmp_enum(x["params"], y["params"])
                elif x["kind"] == "格式约束":
                    verdict, detail = _cmp_pattern(x["params"], y["params"])
                elif x["kind"] in ("必填约束", "条件必填"):
                    verdict, detail = "一致", "双方都要求该约束"
                else:
                    verdict, detail = "需人工核对", f"类型 {x['kind']} 无自动比较规则"
                rows.append({
                    "field": f, "verdict": verdict,
                    "a": f"{x['clause_id']} [{x['kind']}]", "b": f"{y['clause_id']} [{y['kind']}]",
                    "detail": detail,
                })

    only_a = sorted(set(ia) - set(ib))
    only_b = sorted(set(ib) - set(ia))
    # 同一字段上，某一方存在另一方**没有的类别**（互补，不算冲突）
    def _kinds(idx, f):
        return {d["kind"] for d in idx.get(f, [])}
    extra_kinds = []
    for f in sorted(set(ia) & set(ib)):
        ka, kb = _kinds(ia, f), _kinds(ib, f)
        if ka - kb or kb - ka:
            extra_kinds.append({"field": f,
                                "only_in_a": sorted(ka - kb),
                                "only_in_b": sorted(kb - ka)})
    order = ["冲突", "部分重叠", "需人工核对", "A 更严", "B 更严", "一致"]
    rows.sort(key=lambda r: (order.index(r["verdict"]) if r["verdict"] in order else 99,
                             r["field"]))
    counts: dict = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    return {
        "a": {"name": a_name, "n_in": a_in, "n_compiled": a_ok, "n_fields": len(ia)},
        "b": {"name": b_name, "n_in": b_in, "n_compiled": b_ok, "n_fields": len(ib)},
        "shared_fields": sorted(set(ia) & set(ib)),
        "rows": rows, "counts": counts,
        "only_a": only_a, "only_b": only_b, "extra_kinds": extra_kinds,
        "n_conflicts": counts.get("冲突", 0),
        "note": ("比较只覆盖**双方都有该字段约束**的情形。"
                 "`冲突` = 取值域不相交，现实中必须由标准化组织裁决，不能由本项目自行消解。"),
    }


def main() -> int:
    if "-h" not in sys.argv and "--help" not in sys.argv:
        pass
    os.environ.setdefault("EVOSTD_NOW", "2026-10-13")
    os.environ["EVOSTD_STATE"] = os.path.join(_SRC, "runs", "crossstd")
    sys.path.insert(0, os.path.abspath(_SRC))

    from domains.manufacturing import standard_industrial as A
    from domains.manufacturing import standard_parallel as B

    def _clauses_of(mod):
        """★ 两个领域包的导出形态**不一致**：
           `standard_industrial` 导出 `CLAUSES` 列表，
           `standard_gov` 导出 `standards()` 函数。
           这里两种都吃，免得第三个包按另一种写法就被打挂。"""
        if hasattr(mod, "CLAUSES"):
            return list(mod.CLAUSES)
        if hasattr(mod, "standards"):
            return list(mod.standards())
        raise AttributeError(f"{mod.__name__} 既没有 CLAUSES 也没有 standards()")

    r = compare(_clauses_of(A), A.STANDARD_NAME, _clauses_of(B), B.STANDARD_NAME)

    if "--json" in sys.argv:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0

    W = 92
    print("=" * W)
    print("跨标准对齐：两部标准在共享字段上的关系（机械计算）")
    print("=" * W)
    print(f"  A = {r['a']['name']}")
    print(f"      条款 {r['a']['n_in']} 条 → 编译 {r['a']['n_compiled']} 条 | 覆盖字段 {r['a']['n_fields']} 个")
    print(f"  B = {r['b']['name']}")
    print(f"      条款 {r['b']['n_in']} 条 → 编译 {r['b']['n_compiled']} 条 | 覆盖字段 {r['b']['n_fields']} 个")
    print(f"  共享字段 {len(r['shared_fields'])} 个：{', '.join(r['shared_fields'])}")
    print()
    print(f"  {'字段':<24}{'判定':<10}{'依据'}")
    print(f"  {'-'*24}{'-'*10}{'-'*52}")
    for x in r["rows"]:
        print(f"  {x['field']:<24}{x['verdict']:<10}{x['detail'][:56]}")
    print()
    print(f"  汇总：{r['counts']}")
    if r.get("extra_kinds"):
        print("  同字段上某一方另有其他类别约束（互补，不算冲突）：")
        for e in r["extra_kinds"]:
            print(f"    {e['field']:<24}仅A: {e['only_in_a'] or '-'}  仅B: {e['only_in_b'] or '-'}")
    if r["only_a"]:
        print(f"  仅 A 覆盖：{r['only_a']}")
    if r["only_b"]:
        print(f"  仅 B 覆盖：{r['only_b']}")
    print()
    print("  注意：`冲突` 指**取值域不相交**——现实中必须由标准化组织裁决，")
    print("        本项目**不会**自行消解它（那是权威行为，不是工程行为）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
