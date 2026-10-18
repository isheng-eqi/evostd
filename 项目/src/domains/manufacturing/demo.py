"""制造业 Domain Pack · 端到端演示。

跑法（Windows PowerShell，在 项目/src 目录下）：
    $env:PYTHONIOENCODING="utf-8"; python -m domains.manufacturing.demo

它演示的是一条完整的"标准 → 判据 → 裁决"链路：
    ① 编译样例标准           → 编译率、8 类判据分布、不可编译条款（软确认区）
    ② 语料侧术语覆盖率审计    → 外部、不依赖本体、机械可算（硬锚点）
    ③ 逐条校验 20 条资产      → "违反第 N 条 + 原文 + 证据"
    ④ 合规率汇总              → 可直接当自进化的适应度（含误报自检）
    ⑤ ★ 盲区实测              → 哪些违规是**现有判据测不到**的，一条条列出来
    ⑥ 收口                    → 硬锚点 vs 软确认

第 ⑤ 步是这份演示的重点：docs/23 的教训是"盲区必须实测确认，不能凭设计者想象"，
所以这里不声明盲区，而是**用判据把盲区跑出来**，并且把"设计者猜错的那些"
一并打印——猜错本身也是要留档的发现。
"""
from __future__ import annotations

import os
import sys

# 允许直接 `python domains/manufacturing/demo.py` 也能跑（对齐 standard/demo.py 的习惯）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from standard.compiler import ClauseKind, StandardVerifier, compile_standard  # noqa: E402
from domains.manufacturing import assets as A  # noqa: E402
from domains.manufacturing import corpus as C  # noqa: E402
from domains.manufacturing.standard_industrial import (  # noqa: E402
    CLAUSES, STANDARD_NAME, STANDARD_NOTICE, STANDARD_NO,
    kind_distribution, uncompilable_clauses,
)

LINE = "=" * 82
SUB = "-" * 82


def _section(title: str) -> None:
    print(f"\n{SUB}\n{title}\n{SUB}")


def main() -> None:
    print(LINE)
    print("制造业 Domain Pack · 端到端演示")
    print(f"标准：{STANDARD_NAME}")
    print(f"编号：{STANDARD_NO}    {STANDARD_NOTICE}")
    print(LINE)

    # =====================================================================
    # ① 编译标准
    # =====================================================================
    _section("① 编译标准 → 可执行判据")
    compiled = compile_standard(CLAUSES)
    print(f"  {compiled.summary()}")

    dist = kind_distribution()
    print("\n  8 类 ClauseKind 覆盖情况（每类 ≥2 条才算合格）：")
    for k in ClauseKind:
        n = dist.get(k.value, 0)
        print(f"    {k.value:<12} {n:>2} 条  {'✓' if n >= 2 else '✗ 不足'}")
    print(f"    可编译条款合计 {sum(dist.values())} 条 / 全部 {len(CLAUSES)} 条")

    print(f"\n  ★ 不可编译条款 {len(compiled.uncompilable)} 条"
          f"（= 本包的『软确认』区，只能靠人一次性确认）：")
    for c in compiled.uncompilable:
        print(f"    ⚠ {c.source}｜{c.title}")
        print(f"        原文：「{c.text}」")
    print("    ↑ 它们不是失败，是『可达编辑集』下一步要扩张的方向（docs/21 元机制）。")

    # =====================================================================
    # ② 语料侧术语覆盖率审计（外部真值，不依赖本体）
    # =====================================================================
    _section("② 语料（多模态）与语料侧术语覆盖率审计")
    docs = C.all_docs()
    by_mod: dict[str, int] = {}
    for d in docs:
        by_mod[d.modality] = by_mod.get(d.modality, 0) + 1
    print(f"  语料 {len(docs)} 条：{by_mod}")
    for d in docs:
        print(f"    [{d.modality}] {d.doc_id}  {d.title}  术语 {len(d.terms)} 条")
    universe = C.corpus_term_universe()
    print(f"\n  语料侧术语总表：{len(universe)} 个术语（跨全部语料汇总频次）")
    print(f"    示例：{list(universe.items())[:8]}")

    print("\n  ★ 自律检查：声明的术语频次 == 从原文机械数出来的频次？")
    bad = [r for r in C.audit_declared_terms() if r["不一致"]]
    print(f"    检查 {len(docs)} 条语料，不一致 {len(bad)} 条 → "
          f"{'全部一致（术语表由机械计数生成，无法自欺）' if not bad else bad}")

    print("\n  ★ 术语覆盖率（**外部、不依赖本体、机械可算** —— 典型硬锚点）：")
    samples = A.COMPLIANT_ASSETS[:2] + A.VIOLATING_ASSETS[:2]
    for _, asset in samples:
        cov = C.term_coverage(A.asset_text(asset))
        print(f"    {asset['asset_id']}：覆盖 {cov['命中']}/{cov['术语总数']} "
              f"= {cov['覆盖率']:.2%}")
    cov0 = C.term_coverage(A.asset_text(A.COMPLIANT_ASSETS[0][1]))
    print(f"    未覆盖术语示例：{cov0['未覆盖术语'][:6]} …")
    print("    ↑ 这个数字不需要人标注，也不依赖本体设计得好不好 —— 免费的外部真值。")

    # =====================================================================
    # ③ 逐条校验
    # =====================================================================
    verifier = StandardVerifier(compiled)
    ctx = A.ctx()
    labeled = A.all_assets()

    _section(f"③ 逐条校验 {len(labeled)} 条资产（违反第 N 条 + 原文 + 证据）")
    print(f"  ctx 受控清单：设备台账 {len(ctx['equipment_registry'])} 台 | "
          f"已停用 {len(ctx['retired_equipment'])} 台 | "
          f"受控标准 {len(ctx['controlled_standards'])} 本 | "
          f"已废止 {len(ctx['abolished_standards'])} 本 | "
          f"部门 {len(ctx['controlled_depts'])} 个 | "
          f"未授权路径 {len(ctx['unauthorized_storage'])} 个")

    for i, asset in enumerate(labeled, start=1):
        findings = verifier.verify(asset, ctx)
        vs = [f for f in findings if not f.ok]
        rate = verifier.compliance(asset, ctx)
        tag = "合规" if asset["_label"] == "compliant" else "违规"
        print(f"\n  [{i:>2}/{len(labeled)}] {asset['asset_id']}  ({tag})")
        print(f"      用例：{asset['_why']}")
        print(f"      合规率 = {rate:.2%}"
              f"（{sum(1 for f in findings if f.ok)}/{len(findings)} 条判据通过）"
              f"   违例 {len(vs)} 条")
        for f in vs:
            print(f"      ✗ 违反 {f.clause_id}｜{f.message}")
            print(f"          原文：「{f.clause_text}」")
            print(f"          证据：{f.evidence}")

    # =====================================================================
    # ④ 合规率汇总（= 自进化适应度）
    # =====================================================================
    _section("④ 合规率汇总（这个数字就是自进化的适应度）")
    rows = [(a["asset_id"], a["_label"], a["_why"],
             verifier.compliance(a, ctx), len(verifier.violations(a, ctx))) for a in labeled]
    print(f"  {'资产':<20}{'标签':<10}{'合规率':>9}{'违例':>6}   用例")
    for aid, label, why, rate, nv in rows:
        print(f"  {aid:<20}{label:<10}{rate:>8.2%}{nv:>6}   {why[:36]}")

    comp = [r for r in rows if r[1] == "compliant"]
    viol = [r for r in rows if r[1] == "violating"]
    zero_viol = [r for r in comp if r[4] == 0]
    print(f"\n  合规资产 {len(comp)} 条：平均合规率 {sum(r[3] for r in comp) / len(comp):.2%}；"
          f"零违例 {len(zero_viol)}/{len(comp)} 条")
    print(f"  违规资产 {len(viol)} 条：平均合规率 {sum(r[3] for r in viol) / len(viol):.2%}；"
          f"平均违例 {sum(r[4] for r in viol) / len(viol):.1f} 条")
    print(f"  整体合规率 = {sum(r[3] for r in rows) / len(rows):.2%}"
          f"（{sum(1 for r in rows if r[4] == 0)}/{len(rows)} 条零违例）")

    # ---- 判据覆盖率矩阵：哪些条款真的被行使过 ----
    hit_by_clause: dict[str, int] = {}
    for a in labeled:
        for f in verifier.violations(a, ctx):
            hit_by_clause[f.clause_id] = hit_by_clause.get(f.clause_id, 0) + 1
    exercised = [c for c in CLAUSES if c.compilable and hit_by_clause.get(c.clause_id)]
    idle = [c for c in CLAUSES if c.compilable and not hit_by_clause.get(c.clause_id)]
    print(f"\n  ★ 判据行使情况：{len(exercised)}/{len(compiled.checkers)} 条可编译判据"
          f"真的有资产触发过它们")
    print(f"    未被行使的条款：{[c.clause_id for c in idle]}")
    print("    ↑ 这些条款不是废条款：它们被合规资产正常通过，只是**没有对应的违规样本**。"
          "没被行使的判据 = 未经验证的判据，这是『标签侧该补什么』的清单。")

    print("\n  ★ 反向自检：合规资产里有没有被机械判据**误报**的？")
    fps = A.false_positive_report(verifier)
    if not fps:
        print("    无误报：8 条合规资产全部零违例")
    for fp in fps:
        print(f"    {fp['资产']}：被 {'/'.join(fp['误报条款'])} 判违规 —— {fp['为什么是冤的']}")
    print("    ↑ 误报和盲区是同一枚硬币的两面：判据太松就漏（盲区），太紧就冤（误报）。"
          "两者都指向同一件事——机械判据缺『语境』。")

    # =====================================================================
    # ⑤ ★ 盲区实测
    # =====================================================================
    _section("⑤ ★ 盲区实测：哪些违规是现有判据测不到的（不靠想象，靠跑）")
    report = A.blind_spot_report(compiled.checkers)
    for r in report:
        verdict = ("★ 真盲区：种子判据全部放行" if r["真盲区"]
                   else f"可见：被 {'/'.join(r['捕获条款'])} 捕获")
        print(f"  · {r['资产']}  {verdict}")
        print(f"      {r['说明']}")

    real = [r for r in report if r["真盲区"]]
    print(f"\n  实测结果：{len(report)} 条违规资产中，{len(real)} 条是**真盲区**"
          f"（现有判据一条都没抓到）；其余 {len(report) - len(real)} 条可被捕获。")
    print("  ↑ 这条检查很关键：所谓『盲区』必须实测，不能凭设计者想象（docs/23 发现 2）。")

    print("\n  ★ 跨资产盲区（判据签名是 check(asset, ctx)，**它看不到别的资产**）：")
    for pr in A.cross_asset_blind_spots(verifier):
        verdict = ("★ 真盲区：零捕获" if pr["真盲区"]
                   else f"可捕获（{'/'.join(pr['捕获条款'])}）")
        print(f"    {pr['编号']} {pr['盲区']} —— {verdict}")
        print(f"        合成样本：{pr['样本']['asset_id']}（{pr['样本']['_why']}）")
        print(f"        语料依据：{pr['语料依据']}")
        print(f"        判定　　：{pr['判定']}，但单点判据结构上做不到")

    print("\n  ★ 设计者以为的盲区里，实测**已被捕获**的那些（猜错也要留档）：")
    for c in A.caught_looking_blind():
        print(f"    {c['样本']}")
        print(f"        曾经的猜测：{c['曾经的猜测']}")
        print(f"        实测　　　　：{c['实测']}")
        print(f"        结论　　　　：{c['结论']}")

    print("\n  ★ 真盲区的结构性说明（= 需要人一次性确认的『软确认』清单）：")
    for b in A.blind_spot_kinds():
        print(f"    {b['编号']} {b['盲区']}")
        print(f"        为什么测不到：{b['为什么测不到']}")
        print(f"        扩张方向　　：{b['扩张方向']}")

    print("\n  ★ 合成对照样本：证明『查表式约束』不是边角料（同一张表在语料里出现两次）")
    for name, ce in (("第一处", A.class_cycle_synthetic()), ("第二处", A.class_interval_synthetic())):
        probe = dict(ce["资产"])
        probe.update({"_label": "violating", "_why": "合成：" + ce["语料判定"]})
        caught = [c.clause.clause_id for c in compiled.checkers if not c.check(probe, ctx).ok]
        print(f"    [{name}] {probe['asset_id']}：{ce['语料理由']}")
        print(f"        语料依据：{ce['语料依据']}")
        print(f"        种子判据的反应：捕获条款 {caught or '无'} → "
              f"{'★ 测不到（真盲区）' if not caught else '可捕获'}")
    print("    ↑ 两处同因（上限取决于另一个字段）→ 该扩张的是**判据语言**，不是逐条打补丁。")

    # =====================================================================
    # ⑥ 一句话收口：硬锚点 vs 软确认
    # =====================================================================
    _section("⑥ 收口：本包里哪些是硬锚点友好的，哪些是软确认的")
    print(f"  硬锚点（可机械判定，不需要人）：{len(compiled.checkers)}/{len(CLAUSES)} 条 = "
          f"{compiled.compile_rate:.0%}")
    print(f"  硬锚点（语料侧）：{len(universe)} 个术语的覆盖率，机械可算、不依赖本体")
    print(f"  软确认（需人一次性确认）：{len(compiled.uncompilable)} 条不可编译条款 "
          f"+ {len(A.blind_spot_kinds())} 类盲区规则")
    print(f"  实测出的不对称：{len(real)} 条单点真盲区（漏） / {len(fps)} 条误报（冤） / "
          f"{len(A.caught_looking_blind())} 条『以为是盲区其实不是』（猜错）")
    print("  裁决不变量：合规率由标准编译出的判据给出，**不含任何模型自评**"
          "（docs/30 第七节：最终裁决者永远在 agent 够不着的地方）")
    print(LINE)


if __name__ == "__main__":
    main()
