"""多角度测试：从 8 个角度打这个项目，逐项给 PASS / FAIL / INFO。

角度：
  A 正确性     正样本不该误报、负样本不该漏判、每条违例要可解释
  B 健壮性     空/畸形/缺字段/类型错/超大输入 —— 崩不崩、静默不静默
  C 确定性     同输入多次、跨实例，结果必须一致
  D 领域隔离   两个领域包交替装配，会不会串
  E 对抗       恒真判据 / 恒假判据 能不能被识别出来
  F 弃权可见   "我没判"和"我判了且合规"能不能分开
  G 跨标准     新功能：两部标准的关系算得对不对
  H 裁决台账   记录决定后，待裁决数会不会真的减少

用法：python tools/multi_angle_test.py
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
    os.environ["EVOSTD_STATE"] = os.path.join(_SRC, "runs", "multitest")
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
    from evostd import service as SV

    mfg = S.build_service("manufacturing")
    from domains.manufacturing.assets import all_assets as mfg_assets
    raw = mfg_assets()
    clean = [{k: v for k, v in a.items() if not k.startswith("_")} for a in raw]
    comp = [a for a, r in zip(clean, raw) if r.get("_label") == "compliant"]
    viol = [a for a, r in zip(clean, raw) if r.get("_label") == "violating"]

    # ── A 正确性 ────────────────────────────────────────────────
    r = mfg.std_verify(comp)["results"]
    bad = [x["asset_id"] for x in r if x["n_violations"] > 0]
    rec("A1", "正确性", not bad, f"合规资产 {len(comp)} 条，误报 {len(bad)} 条 {bad[:3]}")

    # ★ 必须在**全量批次**里检查：集合级唯一性判据需要"对照物"，
    #   只传违规资产会把重复对象排除掉，那条跨资产判据就没对象可比。
    r_all = {x["asset_id"]: x for x in mfg.std_verify(clean)["results"]}
    hit = sum(1 for a in viol if r_all.get(a["asset_id"], {}).get("n_violations", 0) > 0)
    rec("A2", "正确性", hit == len(viol), f"违规资产 {len(viol)} 条，全量批次中被判违规 {hit} 条")

    # A5 ★ 已知缺陷：跨资产判据在"批内无对照物"时**静默通过**，且不记弃权
    tgt = next((a for a in clean if a.get("asset_id") == "AST-MFG-2026-1016"), None)
    if tgt:
        v1 = mfg.std_verify([tgt])
        ab1 = (v1.get("abstained") or {}).get("by_clause") or {}
        silent = v1["results"][0]["n_violations"] == 0 and "第47条" not in ab1
        rec("A5", "正确性", not silent,
            ("★ 跨资产判据(第47条)在无对照物时静默通过且未记弃权 —— "
             "单条资产逐条校验时，重复检测永不生效" if silent else "跨资产判据正确弃权"))

    miss = [x for x in r if x["n_violations"] > 0 and not x["violations"][0].get("clause_text")]
    rec("A3", "正确性", not miss, f"违例是否都带条款原文：缺 {len(miss)} 条")

    # ★ 核心主张：格式全合法、语义错
    base = dict(comp[0])
    base["equipment_class"] = "A类"
    base["maintenance_cycle_days"] = 365
    rr = mfg.std_verify([base])["results"][0]
    rec("A4", "正确性", rr["n_violations"] == 1 and rr["violations"][0]["clause_id"] == "第45条",
        f"格式合法但语义错：命中 {rr['n_violations']} 条（{'/'.join(v['clause_id'] for v in rr['violations'])}）")

    # ── B 健壮性 ────────────────────────────────────────────────
    try:
        e = mfg.std_verify([])
        rec("B1", "健壮性", e.get("ok") is True, f"空数组 → ok={e.get('ok')} rate={e.get('compliant_rate')}")
    except Exception as ex:
        rec("B1", "健壮性", False, f"空数组抛异常 {type(ex).__name__}")

    try:
        e = S.std_verify("{ 坏 JSON")
        rec("B2", "健壮性", False, f"畸形 JSON 抛异常 → {type(e).__name__}（未优雅处理）")
    except Exception as ex:
        rec("B2", "健壮性", True, f"畸形 JSON 抛 {type(ex).__name__}（未静默通过）")

    e = S.std_verify("")
    rec("B3", "健壮性", e.get("ok") is False and "必须提供" in (e.get("error") or ""),
        f"空字符串 → ok={e.get('ok')}")

    miss_field = dict(comp[0]); miss_field.pop("collect_time", None)
    rr = mfg.std_verify([miss_field])["results"][0]
    rec("B4", "健壮性", rr["n_violations"] > 0, f"缺必填字段 → 抓到 {rr['n_violations']} 条")

    try:
        wrong = dict(comp[0]); wrong["revision"] = "不是数字"
        rr = mfg.std_verify([wrong])["results"][0]
        rec("B5", "健壮性", rr["n_violations"] > 0,
            f"类型不符(revision=字符串) → 抓到 {rr['n_violations']} 条（静默放行才是缺陷）")
    except Exception as ex:
        rec("B5", "健壮性", True, f"类型不符抛 {type(ex).__name__}（不算静默）")

    import time
    big = [dict(comp[i % len(comp)]) for i in range(1000)]
    t0 = time.time()
    rb = mfg.std_verify(big)
    dt = time.time() - t0
    rec("B6", "健壮性", rb.get("ok") is True, f"1000 条资产 → {dt:.2f}s，ok={rb.get('ok')}")

    # ── C 确定性 ────────────────────────────────────────────────
    js = json.dumps(comp, ensure_ascii=False)
    a1 = mfg.std_verify(comp)["compliant_rate"]
    a2 = mfg.std_verify(comp)["compliant_rate"]
    SV._ABSTAIN_CACHE.clear()
    a3 = mfg.std_verify(comp)["compliant_rate"]
    rec("C1", "确定性", a1 == a2 == a3, f"同输入三次（含清缓存）→ {a1} / {a2} / {a3}")

    # ── D 领域隔离 ──────────────────────────────────────────────
    gov = S.build_service("gov_catalog")
    from domains.gov_catalog.assets import all_assets as gov_assets
    gclean = [{k: v for k, v in a.items() if not k.startswith("_")} for a in gov_assets()]
    g_rate = gov.std_verify(gclean)["compliant_rate"]
    m_again = mfg.std_verify(comp)["compliant_rate"]   # 装配 gov 之后，回头用 mfg 实例
    rec("D1", "领域隔离", m_again == a1,
        f"装配政务后回头用制造业实例 → {m_again}（应等于 {a1}）")
    rec("D2", "领域隔离", g_rate is not None, f"政务包自身 → compliant_rate={g_rate}")

    # 制造业服务去验政务资产
    cross = mfg.std_verify(gclean)
    rec("D3", "领域隔离", True, f"制造业服务验政务资产 → ok={cross.get('ok')} 违例总数="
        f"{sum(x['n_violations'] for x in cross.get('results') or [])}（INFO：会判，但条款对不上）")

    # ── E 对抗 ──────────────────────────────────────────────────
    S.build_service("manufacturing")
    au = S.std_audit()
    probes = au.get("probes")
    rec("E1", "对抗", bool(probes), f"对抗探针结果：{json.dumps(probes, ensure_ascii=False)[:110]}")

    # ── F 弃权可见 ──────────────────────────────────────────────
    v = mfg.std_verify(comp)
    ab = v.get("abstained") or {}
    rec("F1", "弃权可见", "n_abstained_clauses" in ab,
        f"弃权字段存在：n={ab.get('n_abstained_clauses')} 条款={list((ab.get('by_clause') or {}).keys())}")
    rec("F2", "弃权可见", bool(v.get("skipped_note")), "skipped_note 是否给出解读提示")

    # ── G 跨标准 ────────────────────────────────────────────────
    sys.path.insert(0, os.path.abspath(os.path.join(_ROOT, "tools")))
    import importlib
    cs = importlib.import_module("cross_standard")
    from domains.manufacturing import standard_industrial as A
    from domains.manufacturing import standard_parallel as B
    cr = cs.compare(A.CLAUSES, A.STANDARD_NAME, B.CLAUSES, B.STANDARD_NAME)
    rec("G1", "跨标准", cr["counts"].get("冲突") == 1,
        f"共享字段 {len(cr['shared_fields'])} 个 → {cr['counts']}")

    # ── H 裁决台账 ──────────────────────────────────────────────
    p1 = S.std_pending()
    if p1["items"]:
        ref = p1["items"][0]["ref"]
        S.std_adjudicate(ref, "deferred", "test", "多角度测试占位")
        p2 = S.std_pending()
        rec("H1", "裁决台账", p2["n_pending"] < p1["n_pending"],
            f"待裁决 {p1['n_pending']} → {p2['n_pending']}（记录后应减少）")
    else:
        rec("H1", "裁决台账", False, "待裁决列表为空，无法测试")

    # ── 汇总 ────────────────────────────────────────────────────
    W = 96
    print("=" * W)
    print("多角度测试结果")
    print("=" * W)
    print(f"  {'项':<5}{'角度':<10}{'结果':<7}说明")
    print(f"  {'-'*5}{'-'*10}{'-'*7}{'-'*70}")
    npass = nfail = 0
    for code, angle, ok, detail in _res:
        mark = "PASS" if ok else "FAIL"
        npass += 1 if ok else 0
        nfail += 0 if ok else 1
        print(f"  {code:<5}{angle:<10}{mark:<7}{detail[:70]}")
    print()
    print(f"  合计 {len(_res)} 项：PASS {npass} / FAIL {nfail}")
    return 0 if nfail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
