"""判据语言契约（通用）+ 语言证据自洽 + 性能基线。

  W1 ★ 12 类判据逐类验证：**参数不全时必须大声失败，不能静默通过**
       这是把第 163/167 节修的「静默默认」这一类**通用化封死** ——
       以后新增判据类型若带静默默认，这里会立刻红。
  W2 std_grammar 的 language_versions 证据自洽（每条新判据都有对应条款）
  W3 性能基线登记（1 / 100 / 1000 条），只记录、不优化
  W4 查表区间判据在 ctx 缺表时**弃权**，而不是误判

用法：python tools/grammar_contract_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
_res = []


def rec(code, angle, ok, detail):
    _res.append((code, angle, ok, detail))


def main() -> int:
    os.environ.setdefault("EVOSTD_NOW", "2026-10-13")
    os.environ["EVOSTD_STATE"] = os.path.join(_SRC, "runs", "grammar_contract")
    shutil.rmtree(os.environ["EVOSTD_STATE"], ignore_errors=True)
    sys.path.insert(0, os.path.abspath(_SRC))
    import evostd.server as S
    from standard.compiler import Clause, ClauseKind
    from standard.grammar_v2 import (CHECKER_BY_KIND_V2, CHECKER_BY_KIND_V2_EXTRA,
                                 ClauseKindV2, make_checker)

    mfg = S.build_service("manufacturing")
    from domains.manufacturing.assets import all_assets as A
    raw = A()
    clean = [{k: v for k, v in x.items() if not k.startswith("_")} for x in raw]
    base = dict(clean[0])
    ctx = dict((S._svc().ctx or {}), _assets=clean)

    # ── W1 ★ 12 类判据：params={} 时必须大声失败 ────────────────
    # ★ `ClauseKindV2` 是**普通类**（放常量），不是 Enum —— list() 它会 TypeError。
    #   最稳的取法是直接读两张分派表的键：那就是"语言当前认识的全部类型"。
    kinds = list(ClauseKind) + list(CHECKER_BY_KIND_V2) + list(CHECKER_BY_KIND_V2_EXTRA)
    silent, loud = [], []
    for k in kinds:
        label = getattr(k, "value", str(k))
        try:
            ck = make_checker(Clause("W1-" + label, "", k, "t", {}, standard="测试"))
        except Exception:
            loud.append(label)
            continue
        # 构造没抛 → 跑一次，看是不是静默通过（ok=True 且没标 abstained）
        try:
            f = ck.check(base, ctx)
        except Exception:
            loud.append(label)
            continue
        ev = getattr(f, "evidence", {}) or {}
        if getattr(f, "ok", None) is True and not ev.get("abstained"):
            silent.append(label)
        else:
            loud.append(label)
    rec("W1", "12 类契约", not silent,
        f"{len(kinds)} 类中大声失败 {len(loud)} 类；"
        + (f"★ 静默通过 {len(silent)} 类：{silent}" if silent else "静默通过 0 类"))

    # ── W2 std_grammar 证据自洽 ────────────────────────────────
    #
    #   ★ `language_versions` 是 **dict**，不是列表 —— 第一版按列表取，直接 AttributeError。
    #     结构：{'v1': {n_clauses, kinds[...]}, 'v2': {n_clauses, kinds_added[...]},
    #            'actually_in_use': ...}
    try:
        g = S.std_grammar()
        lv = g.get("language_versions") or {}
        v1 = lv.get("v1") or {}
        v2 = lv.get("v2") or {}
        added = list(v2.get("kinds_added") or [])
        eff = g.get("effect") or {}

        # ① v1/v2 的条款数必须单调不减（语言只能加，不能悄悄减）
        n1, n2 = v1.get("n_clauses"), v2.get("n_clauses")
        mono = isinstance(n1, int) and isinstance(n2, int) and n2 >= n1

        # ② ★ 声明"新增"的每一类，都必须**真的有已采纳条款在用** ——
        #    否则就是"语言说自己变强了，但没人用它"。
        kinds_in_lib = set()
        for c in (S.std_clause_list().get("clauses") or []):
            if c.get("status") == "adopted":
                kinds_in_lib.add(c.get("kind"))
        unused = [k for k in added if k not in kinds_in_lib]

        # ③ 效果必须给出三组数，且零退化
        has_eff = all(k in eff for k in ("blindspots", "false_alarms", "F1"))
        no_reg = eff.get("regressions") == 0

        rec("W2", "语言证据", mono and not unused and has_eff and no_reg,
            f"v1 {n1} 条 → v2 {n2} 条（单调 {mono}）；新增 {added}；"
            f"**未被使用的类别 {len(unused)} 个**{unused}；"
            f"效果 {eff.get('blindspots')} / 误报 {eff.get('false_alarms')} / "
            f"F1 {eff.get('F1')} / 退化 {eff.get('regressions')}")
    except Exception as ex:
        rec("W2", "语言证据", False, f"抛 {type(ex).__name__}: {str(ex)[:56]}")

    # ── W3 性能基线（只记录，不优化）──────────────────────────
    #
    #   ★ 基线**必须标注负载**。第一版只测"全量混合"，得出 45s；
    #     而 multi_angle B6 用"8 条合规资产循环"得出 18.4s ——
    #     **同一句"1000 条"在两个测试里差 2.5 倍，不是回归，是负载不同。**
    #     合规资产不产生违例，少了一整层 dict 构造。
    #     所以两种负载都登记，以后比较才不会拿苹果比橘子。
    try:
        comp = [a for a, r in zip(clean, raw) if r.get("_label") == "compliant"]
        out = []
        for tag, src in (("合规×1000", comp), ("混合×1000", clean)):
            batch = [dict(src[i % len(src)]) for i in range(1000)]
            t0 = time.time()
            mfg.std_verify(batch)
            dt = time.time() - t0
            out.append(f"{tag} {dt:.2f}s（{dt/1000*1000:.1f}ms/条）")
        rec("W3", "性能基线", True, " | ".join(out))
    except Exception as ex:
        rec("W3", "性能基线", False, f"抛 {type(ex).__name__}: {str(ex)[:56]}")

    # ── W4 查表区间：ctx 缺表时必须弃权 ────────────────────────
    try:
        from standard.grammar_v2 import RangeByTableChecker
        spec = {"field": "check_interval_hours", "key_field": "equipment_class",
                "table_key": "this_table_does_not_exist"}
        ck = make_checker(Clause("W4", "", ClauseKindV2.RANGE_BY_TABLE, "t", spec,
                                 standard="测试"))
        f = ck.check(base, ctx)
        ev = getattr(f, "evidence", {}) or {}
        rec("W4", "缺表弃权",
            getattr(f, "ok", None) is True and bool(ev.get("abstained")),
            f"ctx 缺表 → ok={getattr(f,'ok',None)} abstained={ev.get('abstained')} "
            f"理由={str(ev.get('reason'))[:40]}")
    except Exception as ex:
        rec("W4", "缺表弃权", False, f"抛 {type(ex).__name__}: {str(ex)[:56]}")

    # ── 汇总 ────────────────────────────────────────────────────
    W = 108
    print("=" * W)
    print("判据语言契约（通用）· 语言证据 · 性能基线")
    print("=" * W)
    print(f"  {'项':<5}{'角度':<12}{'结果':<7}说明")
    print(f"  {'-'*5}{'-'*12}{'-'*7}{'-'*80}")
    npass = nfail = 0
    for code, angle, ok, detail in _res:
        mark = "PASS" if ok else "FAIL"
        npass += 1 if ok else 0
        nfail += 0 if ok else 1
        print(f"  {code:<5}{angle:<12}{mark:<7}{detail[:80]}")
    print()
    print(f"  合计 {len(_res)} 项：PASS {npass} / FAIL {nfail}")
    return 0 if nfail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
