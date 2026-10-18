"""快照 / 回滚往返测试：验证"改坏了能退回去"这条安全路径真的能走通。

为什么需要
----------
判据库有一套内容级快照与回滚（`std_snapshot` / `std_rollback`），
**但 `store.stats()` 里的 `snapshots` 长期是 0** ——
也就是说：**这是一套从来没被用过的安全机制。**

"存在但未验证"是本项目反复出现的盲区形状：
  · 判据"存在"不等于"能触发"        → 已由 `clause_liveness.py` 覆盖
  · 技能"绑上了"不等于"能跑"         → 已做静态校验
  · **快照"有接口"不等于"能退回"**    → 本脚本

一条从未被走通的回滚路径，**在真正需要它的那天才发现坏了**，代价最大。

用法
----
    python tools/snapshot_roundtrip.py              # 制造业
    python tools/snapshot_roundtrip.py gov_catalog  # 政务包

★ 本脚本使用**独立的 state 目录**（`runs/snaprt-<domain>`），
  不碰正在运行的服务所用的库。
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(domain: str = "manufacturing", victim: str = "第7条") -> dict:
    # ★ 顺序很重要：**必须先设 EVOSTD_STATE，再 import evostd.server**。
    #   `evostd.server` 在模块导入时就根据 env 算好 `DEFAULT_STATE_DIR`；
    #   先 import 再设 env 会**静默地操作另一个库**（实测踩过，见 docs/42 第 138 节）。
    os.environ.setdefault("EVOSTD_NOW", "2026-10-13")
    os.environ["EVOSTD_STATE"] = os.path.join(_ROOT, "src", "runs", f"snaprt-{domain}")
    sys.path.insert(0, os.path.join(_ROOT, "src"))
    import evostd.server as S

    S.build_service(domain)
    svc = S._svc()
    steps, problems = [], []

    def note(name, **kw):
        steps.append({"step": name, **kw})

    before = svc.store.stats()
    n0 = before["by_status"].get("adopted", 0)
    note("起始", adopted=n0, snapshots=before.get("snapshots", 0))

    # ① 打快照
    snap = S.std_snapshot(f"roundtrip-{domain}")
    sid = snap.get("snapshot_id") or snap.get("id")
    note("打快照", snapshot_id=sid, n_clauses=snap.get("n_clauses"))
    if not sid:
        problems.append("快照没有返回 snapshot_id")

    # ② 找一个真实存在的判据当"受害者"，退役它
    ids = [r.get("clause_id") for r in svc.store.list_clauses("adopted")]
    target = victim if victim in ids else (sorted(ids)[0] if ids else None)
    if target is None:
        problems.append("库里没有任何已采纳判据，无法测试")
        return {"domain": domain, "ok": False, "steps": steps, "problems": problems}

    ret = S.std_retire(target, reason="快照往返测试", by="snapshot_roundtrip", force=True)
    n_after_retire = svc.store.stats()["by_status"].get("adopted", 0)
    note("退役", clause=target, ok=ret.get("ok"), adopted=n_after_retire)
    if n_after_retire != n0 - 1:
        problems.append(f"退役后判据数应为 {n0-1}，实际 {n_after_retire}")

    # ③ 回滚
    rb = S.std_rollback(sid) if sid else {"ok": False}
    note("回滚", ok=rb.get("ok"), n_clauses=rb.get("n_clauses"))

    # ④ 验证真的退回去了
    after = svc.store.stats()
    n1 = after["by_status"].get("adopted", 0)
    back = target in {r.get("clause_id") for r in svc.store.list_clauses("adopted")}
    note("回滚后", adopted=n1, target_back=back, snapshots=after.get("snapshots", 0))

    if not rb.get("ok"):
        problems.append(f"回滚失败：{rb}")
    if n1 != n0:
        problems.append(f"回滚后判据数应恢复到 {n0}，实际 {n1}")
    if not back:
        problems.append(f"回滚后 {target} 没有回到已采纳集合里")

    return {
        "domain": domain, "ok": not problems, "steps": steps, "problems": problems,
        "note": ("验证的是「改坏了能退回去」这条路径本身。"
                 "从未被走通的回滚路径，会在最需要它的那天才发现坏了。"),
    }


def main() -> int:
    domains = [sys.argv[1]] if len(sys.argv) > 1 else ["manufacturing", "gov_catalog"]
    rc = 0
    for d in domains:
        r = run(d)
        print("=" * 78)
        print(f"快照 / 回滚往返测试：{d}")
        print("=" * 78)
        for s in r["steps"]:
            name = s.pop("step")
            print(f"  {name:<8} {json.dumps(s, ensure_ascii=False)}")
        if r["problems"]:
            print("  ★ 问题：")
            for p in r["problems"]:
                print(f"    - {p}")
            rc |= 2
        else:
            print(f"  ✓ 往返完全一致 —— 快照 → 退役 → 回滚 → 判据恢复")
        print()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
