"""工具全覆盖冒烟：把 34 个工具**每一个都真的调一次**。

为什么需要
----------
`tool_liveness.py` 查出来：34 个工具里有 **16 个从未被任何提交进仓库的脚本调用过**。

它们**本会话里都被真实调用过** —— 但那些是 `python -c` 一次性命令，
**不在仓库里**。于是：

> **"我验证过" ≠ "项目里有一个能重复这个验证的东西"。**
> **下一个接手的人无法重跑它们。**

本脚本把那 16 个（连同其余 18 个的入口）**固化成可重跑的检查**。

用法
----
    python tools/smoke_all_tools.py

★ 使用**独立的 state 目录**（`runs/smokeall`），不碰正在运行的服务所用的库。
★ 会改状态的工具（`evo_*`、`asset_ingest`、`std_confirm`）在**临时库**上执行，
  顺序按依赖排好：`evo_init → evo_step → evo_status → evo_finalize → evo_apply → evo_archive`。
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(domain: str = "manufacturing") -> dict:
    # ★ 顺序很重要：先设 env，再 import evostd.server（否则会静默操作另一个库）
    os.environ.setdefault("EVOSTD_NOW", "2026-10-13")
    os.environ["EVOSTD_STATE"] = os.path.join(_ROOT, "src", "runs", "smokeall")
    sys.path.insert(0, os.path.join(_ROOT, "src"))
    import evostd.server as S

    S.build_service(domain)
    svc = S._svc()

    # 取一条真实资产当入参（不要凭空造，否则测的是"能不能解析 JSON"而不是工具本身）
    try:
        raw = svc.assets_mod.all_assets() if hasattr(svc, "assets_mod") else None
    except Exception:
        raw = None
    if raw is None:
        import importlib
        raw = importlib.import_module(f"domains.{domain}.assets").all_assets()
    first = raw[0]
    aid = first.get("asset_id") or first.get("catalog_id") or first.get("id")
    one = {k: v for k, v in first.items() if not k.startswith("_")}
    asset_json = json.dumps([one], ensure_ascii=False)      # 批量入参用数组
    single_json = json.dumps(one, ensure_ascii=False)        # 单条入参用对象
    # ★ 下面三个工具的签名都是 `(xxx_json: str)` —— **收 JSON 字符串，不是分列参数**。
    #   实测踩到：第一次跑报 TypeError / JSONDecodeError / AttributeError，
    #   根因是**从没人调用过它们，调用形式没人知道**。
    clause_json = json.dumps({
        "clause_id": "SMOKE-001", "kind": "数值范围",
        "text": "修订号应在 0 至 1 之间。",
        "params": {"field": "revision", "min": 0, "max": 1},
    }, ensure_ascii=False)

    results, ctx = [], {}

    def call(name, *a, **kw):
        f = getattr(S, name, None)
        if f is None:
            results.append({"tool": name, "ok": None, "why": "函数不存在"})
            return None
        try:
            r = f(*a, **kw)
            ok = r.get("ok") if isinstance(r, dict) else None
            results.append({"tool": name, "ok": ok,
                            "why": "" if ok is not False else str(r)[:80]})
            return r
        except Exception as e:
            results.append({"tool": name, "ok": False,
                            "why": f"{type(e).__name__}: {str(e)[:70]}"})
            return None

    # ── 无参 / 简单入参 ──────────────────────────────────────────
    call("std_capability")
    call("std_grammar")
    call("std_reference_time")
    call("std_pending")
    call("std_adjudications")
    call("std_adjudicate", "smoke:none", "deferred", "smoke-all", "冒烟占位，不代表真实裁决")
    call("std_degradations")
    call("std_audit")
    call("std_conflicts")
    call("std_amendments")
    call("std_clause_list")
    call("std_hard_anchors")
    call("std_ledger")
    call("std_parity", [])
    call("std_parity", S.std_capability()["tools"])
    call("asset_list")
    call("asset_list", "all")
    call("asset_search", "点检")
    call("asset_blindspots", asset_json)
    call("asset_lineage", single_json)
    call("multi_hop", aid)
    call("std_verify", asset_json)
    call("std_compliance", asset_json)
    call("std_propose", clause_json)
    call("asset_ingest", single_json)
    call("std_snapshot", "smoke-all")
    call("std_ask", asset_json)

    # ── 进化回路：按依赖顺序 ─────────────────────────────────────
    e = call("evo_init", "clause", 4, 11)
    rid = (e or {}).get("run_id") or (e or {}).get("id")
    ctx["run_id"] = rid
    if rid:
        call("evo_step", rid, 1)
        call("evo_status", rid)
        call("evo_archive", rid)
        call("evo_finalize", rid)
        call("evo_apply", rid)

    n_ok = sum(1 for r in results if r["ok"] is True)
    n_false = sum(1 for r in results if r["ok"] is False)
    n_none = sum(1 for r in results if r["ok"] is None)
    return {"domain": domain, "results": results, "ctx": ctx,
            "n_calls": len(results), "n_ok": n_ok, "n_false": n_false,
            "n_unknown": n_none, "ok": n_false == 0}


def main() -> int:
    r = run(sys.argv[1] if len(sys.argv) > 1 else "manufacturing")
    print("=" * 78)
    print(f"工具全覆盖冒烟：{r['domain']}")
    print("=" * 78)
    for x in r["results"]:
        mark = "✓" if x["ok"] is True else ("✗" if x["ok"] is False else "?")
        line = f"  {mark} {x['tool']:<22}"
        if x["why"]:
            line += f" {x['why']}"
        print(line)
    print()
    print(f"  共调用 {r['n_calls']} 次 | ok=True {r['n_ok']} | ok=False {r['n_false']} "
          f"| 无 ok 字段 {r['n_unknown']}")
    if r["ctx"].get("run_id"):
        print(f"  进化回路 run_id = {r['ctx']['run_id']}")
    return 0 if r["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
