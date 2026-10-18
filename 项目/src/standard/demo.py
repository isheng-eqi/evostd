"""标准编译器演示：条款 → 可执行判据 → 违例必须报出"违反第 N 条"。

跑法（在 项目/src 目录下）：
    python -m standard.demo
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from standard.compiler import StandardVerifier, compile_standard  # noqa: E402
from standard.samples import (  # noqa: E402
    BAD_ASSETS, CLAUSES, CONTROLLED_STANDARDS, GOOD_ASSET,
)


def main() -> None:
    print("=" * 78)
    print("标准编译器 · 把权威文本编译成可执行的对错判据")
    print("=" * 78)

    compiled = compile_standard(CLAUSES)
    print(f"\n【编译结果】{compiled.summary()}")
    for c in compiled.uncompilable:
        print(f"  ⚠ 不可编译：{c.source} —— {c.text}")
    print("  ↑ 这两条不是失败，是**下一步扩张可达编辑集的入口**"
          "（见 docs/21「元机制」）")

    verifier = StandardVerifier(compiled)
    ctx = {"controlled_standards": CONTROLLED_STANDARDS}

    # ---- 合规资产 ----
    print("\n" + "-" * 78)
    print("【用例 1】合规资产")
    print("-" * 78)
    fs = verifier.verify(GOOD_ASSET, ctx)
    print(f"  合规率 = {verifier.compliance(GOOD_ASSET, ctx):.2%}  "
          f"（{sum(1 for f in fs if f.ok)}/{len(fs)} 条判据通过）")
    print(f"  违例数 = {len(verifier.violations(GOOD_ASSET, ctx))}")

    # ---- 违规资产 ----
    print("\n" + "-" * 78)
    print("【用例 2-6】违规资产 —— 看它能不能报出「违反第 N 条」")
    print("-" * 78)
    for i, (label, asset) in enumerate(BAD_ASSETS, start=2):
        vs = verifier.violations(asset, ctx)
        print(f"\n  用例 {i}：{label}")
        print(f"    合规率 = {verifier.compliance(asset, ctx):.2%}   违例 {len(vs)} 条")
        for f in vs:
            print(f"      {f}")
            print(f"        证据：{f.evidence}")

    # ---- 汇总：适应度可直接用于自进化 ----
    print("\n" + "-" * 78)
    print("【作为自进化适应度】")
    print("-" * 78)
    print(f"  {'用例':<34}{'合规率':>10}{'违例条款':>10}")
    rows = [("合规资产", GOOD_ASSET)] + [(lbl, a) for lbl, a in BAD_ASSETS]
    for label, asset in rows:
        vs = verifier.violations(asset, ctx)
        print(f"  {label[:32]:<34}{verifier.compliance(asset, ctx):>9.2%}{len(vs):>10}")

    print("\n  → 这个合规率就是自进化的适应度：**外部、确定性、可回指条款**。")
    print("  → 它不是模型给的分数，而是标准给的裁决。")
    print("=" * 78)


if __name__ == "__main__":
    main()
