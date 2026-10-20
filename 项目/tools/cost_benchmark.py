"""效率基准：把"半自动构建"里**机械部分**的耗时实测出来，与人工估算分开列。

为什么必须分开
--------------
赛题要的是「半自动化本体构建效率优势明显」。这个"优势"如果只写"我们很快"，
就是自说自话；如果只写"人工要 20 小时"，又拿不出实测。
所以这里只做两件事：

  1. 机械部分：**冷启动子进程**实测（含解释器与导入），谁都复现得了；
  2. 人工部分：**明确标为估算**并写出假设（每条判据 20 分钟、每条锚点 5 分钟），
     谁都能用自己的数字替换。

刻意不做的事
------------
不拿"人时 ÷ 毫秒"算一个几千万倍的加速比。那是拿两种不同量纲的东西相除，
看着震撼，实际上是自欺。这里只给"人时级 → 秒级"的量级判断。

输出：submission/成本基准.json + 终端表格。

用法：
    cd 项目/src && set EVOSTD_NOW=2026-10-21
    python ../tools/cost_benchmark.py
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
DOMAIN = "manufacturing"


def cold_run(code: str, repeat: int = 3) -> dict:
    """冷启动子进程跑一段代码，返回墙钟中位数（含解释器启动与导入）。"""
    ts = []
    codes = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        p = subprocess.run([sys.executable, "-c", code], cwd=SRC, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           env={**os.environ, "EVOSTD_NOW": os.environ.get("EVOSTD_NOW", "2026-10-21")})
        ts.append((time.perf_counter() - t0) * 1000.0)
        codes.append(p.returncode)
    return {"n": repeat, "median_ms": round(statistics.median(ts), 1),
            "min_ms": round(min(ts), 1), "max_ms": round(max(ts), 1),
            "exit_codes": codes}


COMPILE_CODE = (
    "import domains.manufacturing as p;"
    "from standard.grammar_v2_experiment import v2_clauses;"
    "cs=list(p.standards()); v2=v2_clauses(p.STANDARD_NAME, docs=p.docs());"
    "print(len(cs)+len(v2))"
)

ANCHOR_CODE = (
    "import evostd.server as S; S.build_service('manufacturing');"
    "print(S.std_hard_anchors().get('n'))"
)

VERIFY_CODE = (
    "import json, evostd.server as S; S.build_service('manufacturing');"
    "from domains.manufacturing.assets import all_assets;"
    "A=[{k:v for k,v in a.items() if not k.startswith('_')} for a in all_assets()];"
    "r=S.std_verify(json.dumps(A, ensure_ascii=False)); print(r['n_results'] if 'n_results' in r else len(r['results']))"
)


def main() -> int:
    os.environ.setdefault("EVOSTD_NOW", "2026-10-21")
    bench: dict = {"domain": DOMAIN, "environment": {
        "python": sys.version.split()[0], "platform": sys.platform,
        "note": "冷启动子进程墙钟中位数，含解释器启动与模块导入；同机复现即可",
    }, "measured": {}}

    bench["measured"]["compile_clauses"] = cold_run(COMPILE_CODE)
    bench["measured"]["build_service_and_anchors"] = cold_run(ANCHOR_CODE)
    bench["measured"]["verify_all_assets"] = cold_run(VERIFY_CODE)

    t0 = time.perf_counter()
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "kg_export.py"),
                        "--domain", DOMAIN, "--out", os.path.join(ROOT, "submission", "kg")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=ROOT,
                       env={**os.environ, "EVOSTD_NOW": os.environ.get("EVOSTD_NOW", "2026-10-21")})
    bench["measured"]["full_pipeline_kg_export"] = {
        "wall_ms": round((time.perf_counter() - t0) * 1000.0, 1), "exit_code": p.returncode}

    # 规模（从产物读，避免手填）
    stats_path = os.path.join(ROOT, "submission", "kg", "kg-stats.json")
    n_clauses = n_anchors = n_assets = None
    if os.path.exists(stats_path):
        with open(stats_path, encoding="utf-8") as f:
            kg = json.load(f)
        n_clauses = kg.get("n_by_type", {}).get("条款")
        n_assets = kg.get("n_by_type", {}).get("资产")
    anchors_path = os.path.join(ROOT, "src", "runs", "evostd-state", DOMAIN, "anchors.json")
    if os.path.exists(anchors_path):
        with open(anchors_path, encoding="utf-8") as f:
            try:
                a = json.load(f)
                # 落盘格式是 list；兼容 dict{"anchors":[...]} 的旧格式
                if isinstance(a, list):
                    n_anchors = len(a)
                elif isinstance(a, dict):
                    n_anchors = len(a.get("anchors") or [])
            except Exception:
                pass

    MIN_PER_CLAUSE = 20
    MIN_PER_ANCHOR = 5
    manual_hours = None
    if n_clauses and n_anchors:
        manual_hours = round(n_clauses * MIN_PER_CLAUSE / 60 + n_anchors * MIN_PER_ANCHOR / 60, 2)

    pipe_s = bench["measured"]["full_pipeline_kg_export"]["wall_ms"] / 1000.0
    bench["scale"] = {"n_clauses": n_clauses, "n_anchors": n_anchors, "n_assets": n_assets}
    bench["manual_estimate"] = {
        "assumptions": {
            "minutes_per_clause": MIN_PER_CLAUSE,
            "minutes_per_anchor": MIN_PER_ANCHOR,
            "basis": "保守估计：含写规则、找样例、自测；实际随标准复杂度浮动",
        },
        "estimated_hours": manual_hours,
    }
    bench["conclusion"] = {
        "mechanical": f"整条管线（编译+锚点+建图）冷启动 {pipe_s:.2f} 秒",
        "manual": f"同等工作量按假设约 {manual_hours} 人时",
        "order_of_magnitude": "人时级 → 秒级（不计算倍数，量纲不同）",
        "honest_note": ("机械部分是实测；人工部分是估算。真正的效率优势不在'快多少倍'，"
                        "而在于：机械部分可重复、可 diff、换标准只需换文本。"),
    }

    out = os.path.join(ROOT, "submission", "成本基准.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(bench, f, ensure_ascii=False, indent=1)
    print(json.dumps(bench, ensure_ascii=False, indent=1))
    print(f"[cost] 已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
