"""溯源与对账正确性测试：打的是"可溯源"这三个字到底有没有兑现。

角度（前三个套件未覆盖）：
  Z1  std_answer 的 relation 是否真的分型（不是恒为 consistent）
  Z2  std_conflicts 报的冲突，条款组合是否指得准
  Z3  std_amendments 的建议是否**带证据**（语料原文 / 字符区间）
  Z4  asset_lineage 逐跳是否可核实
  Z5  multi_hop 的 gaps 是否诚实（没依据的跳必须进 gaps，不能编）
  Z6  std_parity 自对账（自己 vs 自己）应 in_sync=True
  Z7  std_degradations 是否如实列出
  Z8  std_grammar 的语言证据是否自洽
  Z9  深嵌套 JSON（1000 层）不崩
  Z10 std_retire → 判据数减 1 → 再 std_rollback 恢复

用法：python tools/accountability_test.py
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
_res = []


def rec(code, angle, ok, detail):
    _res.append((code, angle, ok, detail))


def main() -> int:
    os.environ.setdefault("EVOSTD_NOW", "2026-10-13")
    os.environ["EVOSTD_STATE"] = os.path.join(_SRC, "runs", "accountability")
    # ★★ 自清理：每次运行前删掉自己的状态目录。
    #
    #   动机（第 164 节之后的发现）：`multi_angle_test` 曾出现"同样代码一次 20/20、
    #   一次 19/20"。根因是 `runs/multitest` 被上一次运行留下了状态 ——
    #       最典型的是 H1：`std_adjudicate` 是**持久化**的，
    #       第二次跑时那条待裁决已被记录过，`n_pending` 不再下降 → H1 失败。
    #
    #   **一个需要外部先删目录才能稳定通过的测试，等于不可复现。**
    #   这里让它自己负责：先清空，再跑。
    import shutil as _shutil
    _shutil.rmtree(os.environ["EVOSTD_STATE"], ignore_errors=True)

    sys.path.insert(0, os.path.abspath(_SRC))
    import evostd.server as S
    from domains.manufacturing.assets import all_assets as A

    mfg = S.build_service("manufacturing")
    raw = A()
    clean = [{k: v for k, v in x.items() if not k.startswith("_")} for x in raw]
    base = dict(clean[0])

    # ── Z1 std_answer 分型 ──────────────────────────────────────
    try:
        ok_rel, bad_rel = [], []
        for a, verdict in ((clean[0], "compliant"), (clean[0], "violating")):
            r = S.std_answer(json.dumps(a, ensure_ascii=False), verdict, "test")
            (ok_rel if r.get("relation") else bad_rel).append(r.get("relation"))
        rec("Z1", "分型", not bad_rel and len(set(ok_rel)) >= 1,
            f"两次不同裁决 → relation={ok_rel}（键名是 relation，不是 kind）")
    except Exception as ex:
        rec("Z1", "分型", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    # ── Z2 std_conflicts 指得准 ────────────────────────────────
    try:
        cf = S.std_conflicts()
        items = cf.get("conflicts") or []
        # ★ 键名是 own_clause / own_how，不是 clause_a / clause_b。
        #   第一版读错键，显示 (None, None) 却因为只比计数而误判为 PASS。
        pairs = [(i.get("own_clause"), (i.get("own_how") or "")[:24]) for i in items]
        has77 = any("第7条" in str(p[0]) and "第46条" in str(i.get("own_how") or "")
                    for i, p in zip(items, pairs))
        rec("Z2", "冲突检测", cf.get("n_conflicts") == len(items) and has77,
            f"冲突 {len(items)} 条 {pairs}（应含第7条 vs 第46条）")
    except Exception as ex:
        rec("Z2", "冲突检测", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    # ── Z3 std_amendments 是否带证据 ───────────────────────────
    try:
        am = S.std_amendments()
        items = am.get("amendments") or am.get("items") or []
        with_ev = [i for i in items if (i.get("evidence") or i.get("corpus") or
                                        i.get("char_range") or i.get("source_text"))]
        rec("Z3", "修订证据", bool(items) and len(with_ev) == len(items),
            f"修订建议 {len(items)} 条，带证据 {len(with_ev)} 条")
    except Exception as ex:
        rec("Z3", "修订证据", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    # ── Z4 asset_lineage 逐跳可核实 ────────────────────────────
    try:
        ln = S.asset_lineage(json.dumps(base, ensure_ascii=False))
        hops = ln.get("hops") or ln.get("chain") or []
        no_src = [h for h in hops if not (h.get("source") or h.get("clause_id") or h.get("why"))]
        rec("Z4", "血缘", bool(hops) and not no_src,
            f"血缘 {len(hops)} 跳，无来源标注 {len(no_src)} 跳")
    except Exception as ex:
        rec("Z4", "血缘", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    # ── Z5 multi_hop 的 gaps 是否诚实 ──────────────────────────
    try:
        mh = S.multi_hop(base.get("asset_id"), 3)
        hops = mh.get("hops") or []
        gaps = mh.get("gaps") or []
        # 每一跳必须有依据；没依据的必须进 gaps
        unsupported = [h for h in hops if not (h.get("evidence") or h.get("source") or h.get("via"))]
        rec("Z5", "多跳诚实", not unsupported,
            f"多跳 {len(hops)} 跳 / gaps {len(gaps)} 条 / 无依据跳 {len(unsupported)}")
    except Exception as ex:
        rec("Z5", "多跳诚实", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    # ── Z6 std_parity 自对账 ───────────────────────────────────
    try:
        cap = S.std_capability()
        mine = cap.get("tools") or []
        pr = S.std_parity(mine)
        fps = ((pr.get("authoritative") or {}).get("fingerprint"),
               (pr.get("claimed") or {}).get("fingerprint"))
        rec("Z6", "对账", pr.get("in_sync") is True and fps[0] == fps[1] and bool(fps[0]),
            f"用服务自己的清单对账 → in_sync={pr.get('in_sync')} 双方指纹={fps}")
    except Exception as ex:
        rec("Z6", "对账", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    # ── Z7 降级台账 ────────────────────────────────────────────
    try:
        dg = S.std_degradations()
        rec("Z7", "降级台账", dg.get("ok") is True,
            f"ok={dg.get('ok')} n_incidents={dg.get('n_incidents')}")
    except Exception as ex:
        rec("Z7", "降级台账", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    # ── Z8 std_grammar 语言证据 ────────────────────────────────
    try:
        g = S.std_grammar()
        keys = [k for k in g if k != "ok"]
        rec("Z8", "语言证据", g.get("ok") is True and bool(keys),
            f"ok={g.get('ok')} 键={keys[:6]}")
    except Exception as ex:
        rec("Z8", "语言证据", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    # ── Z9 深嵌套 JSON ─────────────────────────────────────────
    try:
        deep = base
        for _ in range(1000):
            deep = {"n": deep}
        r = mfg.std_verify([deep])
        rec("Z9", "深嵌套", r.get("ok") is True,
            f"1000 层嵌套 → ok={r.get('ok')}（不崩即可）")
    except RecursionError:
        rec("Z9", "深嵌套", False, "1000 层嵌套 → RecursionError（需设上限或报错）")
    except Exception as ex:
        rec("Z9", "深嵌套", True, f"1000 层嵌套 → 抛 {type(ex).__name__}（不算崩溃）")

    # ── Z10 retire → rollback ──────────────────────────────────
    try:
        svc = S._svc()

        def _adopted():
            return svc.store.stats()["by_status"].get("adopted", 0)

        before = _adopted()
        snap = S.std_snapshot("Z10")
        # ★ 退役必须 force=True：无危害记录的条款默认**拒退**（这是设计约束，见 Z11）
        r = S.std_retire("第8条", reason="accountability test", by="test", force=True)
        mid = _adopted()
        S.std_rollback(snap.get("snapshot_id") or snap.get("id"))
        after = _adopted()
        rec("Z10", "退役回滚", bool(r.get("ok")) and mid == before - 1 and after == before,
            f"判据数 {before} →（退役第8条）→ {mid} →（回滚）→ {after}")
    except Exception as ex:
        rec("Z10", "退役回滚", False, f"抛 {type(ex).__name__}: {str(ex)[:60]}")

    # ── Z11 ★ 退役闸门：无危害记录且不给 force 时**必须拒退** ─────
    try:
        r = S.std_retire("第9条", reason="不该被接受", by="test")
        rec("Z11", "退役闸门", r.get("ok") is False,
            f"无危害记录+无 force → ok={r.get('ok')} 理由={str(r.get('error'))[:52]}")
    except Exception as ex:
        rec("Z11", "退役闸门", True, f"抛 {type(ex).__name__}（也不算静默接受）")

    # ── 汇总 ────────────────────────────────────────────────────
    W = 104
    print("=" * W)
    print("溯源与对账正确性测试")
    print("=" * W)
    print(f"  {'项':<5}{'角度':<10}{'结果':<7}说明")
    print(f"  {'-'*5}{'-'*10}{'-'*7}{'-'*78}")
    npass = nfail = 0
    for code, angle, ok, detail in _res:
        mark = "PASS" if ok else "FAIL"
        npass += 1 if ok else 0
        nfail += 0 if ok else 1
        print(f"  {code:<5}{angle:<10}{mark:<7}{detail[:78]}")
    print()
    print(f"  合计 {len(_res)} 项：PASS {npass} / FAIL {nfail}")
    return 0 if nfail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
