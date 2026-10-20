"""一键复现：测试 → 图谱 → 对账 → 文档 → 提交包。

给评委和队友用：一条命令跑完，任何一步失败就停下并标明是哪一步。
不隐藏输出：每步都把关键行打出来，失败时打印最后 15 行。

用法：
    cd 项目
    python tools/run_all.py            # 全流程
    python tools/run_all.py --fast     # 跳过 7 套测试（只重建产物）
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
ENV = {**os.environ, "EVOSTD_NOW": os.environ.get("EVOSTD_NOW", "2026-10-21"),
       "PYTHONIOENCODING": "utf-8"}

TESTS = [
    ("multi_angle_test", "多角度 20 项"),
    ("extreme_test", "极端边界 9 项"),
    ("contract_test", "判据契约 14 项"),
    ("accountability_test", "溯源对账 11 项"),
    ("gate_test", "准入闸门 8 项"),
    ("grammar_contract_test", "语言契约 4 项"),
    ("domain_matrix_test", "领域矩阵 14 项"),
]

STEPS: list[tuple[str, bool]] = []


def run(cmd: list[str], cwd: str, label: str, tail: int = 6) -> bool:
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=cwd, env=ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    dt = time.perf_counter() - t0
    ok = p.returncode == 0
    STEPS.append((label, ok))
    head = "✓" if ok else "✗"
    print(f"  {head} {label}  ({dt:.1f}s)")
    if not ok:
        lines = ((p.stdout or "") + "\n" + (p.stderr or "")).strip().splitlines()
        for line in lines[-15:]:
            print("      " + line)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="跳过测试")
    args = ap.parse_args()

    print("== 1. 测试（7 套 / 80 项）==")
    if args.fast:
        print("  （--fast：跳过）")
    else:
        for mod, desc in TESTS:
            if not run([sys.executable, os.path.join("..", "tools", f"{mod}.py")], SRC, desc):
                print("\n★ 测试失败，后续步骤中止。")
                return 1

    print("== 2. 知识图谱（两个行业）==")
    if not run([sys.executable, os.path.join("tools", "kg_export.py"),
                "--domain", "manufacturing", "--out", "submission/kg"], ROOT, "制造业图谱"):
        return 1
    if not run([sys.executable, os.path.join("tools", "kg_export.py"),
                "--domain", "gov_catalog", "--out", "submission/kg-gov"], ROOT, "政务图谱"):
        return 1

    print("== 3. 数字对账（0 处不一致才继续）==")
    p = subprocess.run([sys.executable, os.path.join("tools", "verify_numbers.py"), "--strict"],
                       cwd=ROOT, env=ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    STEPS.append(("数字对账", p.returncode == 0))
    for line in (p.stdout or "").strip().splitlines()[-3:]:
        print("    " + line)
    if p.returncode != 0:
        print("★ 对账失败：材料数字与代码不一致，拒绝继续。")
        return 1

    print("== 4. 设计文档 → docx ==")
    if not run([sys.executable, os.path.join("tools", "md2docx.py"),
                os.path.join("submission", "开发设计文档-六节版.md"),
                os.path.join("submission", "开发设计文档-六节版.docx")], ROOT, "docx 生成"):
        return 1

    print("== 5. 初赛包 ==")
    if not run([sys.executable, os.path.join("tools", "make_package.py")], ROOT, "提交包"):
        return 1

    print("\n== 汇总 ==")
    bad = [s for s, ok in STEPS if not ok]
    print(f"  {len(STEPS) - len(bad)}/{len(STEPS)} 步通过")
    if bad:
        print("  失败：" + "、".join(bad))
        return 1
    print("  全部通过。初赛包在项目上级目录：ICT自进化-初赛包-<日期>.zip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
