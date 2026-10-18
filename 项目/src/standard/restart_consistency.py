"""重启一致性回归：**冷启动与带状态重启必须给出完全相同的状态**。

为什么需要这个
--------------
实测踩到（第 24 轮之后）：

    冷启动      判据 38 | 硬锚点 103 | 冲突 1
    带状态重启   判据 38 | 硬锚点 87  | 冲突 2     ← 差异静默存在

根因两处，都在 `StandardService._seed`：

  1. **变量遮蔽**：`extra = sorted(have_ids - want_ids)` 把 `_seed(clauses, extra=...)`
     的**参数 `extra`** 覆盖成了字符串列表 → 紧随其后的准入块 `if extra:` 拿到的是
     id 字符串而不是条款对象 → **整个准入与扩张锚点推导被静默跳过**。
  2. **`origin` 丢失**：`all_clauses()` 返回的是重建的 Clause 对象，**不带 `origin`**，
     于是"库与领域包一致"的判断永远不成立。

**危害**：判定本身仍然正确（38 条判据都在用），但机械真值集不完整、
冲突计数虚高——**而自检仍报 0 不一致、所有报告数字看起来正常**。

**为什么此前 20 多轮没发现**：每次验证前都习惯性清空状态目录，
**只验了"冷启动正确"，从没验"带状态重启正确"**。

> 这个文件的意义就是：**让"只验冷启动"这种验证盲区不再可能**。

复跑：python -m standard.restart_consistency
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "runs", "restart-consistency")


def _probe(tag: str, state_dir: str, domain: str = "manufacturing") -> dict:
    """在一个**全新进程语义**下装配服务并取状态（清掉已加载模块，模拟重启）。"""
    for m in [k for k in list(sys.modules)
              if k.startswith(("evostd", "domains", "standard"))]:
        del sys.modules[m]
    os.environ["EVOSTD_STATE"] = state_dir
    os.environ.setdefault("EVOSTD_NOW", "2026-10-13")
    S = importlib.import_module("evostd.server")
    importlib.reload(S)
    S.build_service(domain)
    au = S.std_audit()
    return {
        "tag": tag,
        "n_clauses": au["library"]["n_adopted"],
        "n_anchors": au["anchor_selfcheck"]["n_anchors"],
        "n_mismatch": au["anchor_selfcheck"]["n_mismatch"],
        "n_conflicts": S.std_conflicts()["n_conflicts"],
        "n_degradations": au["degradations"]["n_incidents"],
    }




# ============================================================================
#  持久化：**学习成果不能在重启时丢**
# ============================================================================
def _persistence_check(domain: str = "manufacturing") -> tuple[bool, list[str]]:
    """在冷启动时制造三类"学习成果"，重启后逐条核对还在不在。

    为什么单列一节：`restart_consistency` 只比了"装配出来的状态"对不对，
    而**人的确认、人的退役决定、系统提案入库的判据**是三种**不该被重启抹掉**的东西。
    它们此前从未在重启后被验过。
    """
    import json
    msgs: list[str] = []
    ok = True

    def _fresh():
        for m in [k for k in list(sys.modules)
                  if k.startswith(("evostd", "domains", "standard"))]:
            del sys.modules[m]
        os.environ["EVOSTD_STATE"] = STATE
        S = importlib.import_module("evostd.server")
        importlib.reload(S)
        S.build_service(domain)
        return S

    shutil.rmtree(STATE, ignore_errors=True)
    S = _fresh()
    svc = S._svc()
    import importlib as _il
    mod = _il.import_module("domains.gov_catalog.assets" if "gov" in domain
                            else "domains.manufacturing.assets")
    raw = mod.all_assets()
    def _aid(x):
        return (x.get("asset_id") or x.get("catalog_id") or x.get("asset_no") or x.get("id"))
    by = {_aid(a): {k: v for k, v in a.items() if not k.startswith("_")} for a in raw}
    _first = sorted(by)[0]

    # ① 人工软确认（最贵的真值来源）
    S.std_answer(json.dumps(by[_first], ensure_ascii=False), "violating", by="专家")
    n_soft = len(svc.store.anchors_by_kind("soft"))
    # ② 人工退役决定
    _tid = ("第47条" if "第47条" in svc.store.clauses
            else next((c for c in svc.store.clauses if c.startswith("第")), None))
    S.std_retire(_tid, reason="持久化测试", by="专家", force=True)
    st_before = svc.store.clauses[_tid].status
    n_before = len(svc.store.list_clauses("adopted"))

    S = _fresh()
    svc2 = S._svc()
    n_soft2 = len(svc2.store.anchors_by_kind("soft"))
    st_after = (svc2.store.clauses[_tid].status
                if _tid in svc2.store.clauses else "（没了）")
    n_after = len(svc2.store.list_clauses("adopted"))

    for name, a, b in (("人工软确认锚点", n_soft, n_soft2),
                       ("人工退役决定", st_before, st_after),
                       ("在库判据数", n_before, n_after)):
        same = (a == b)
        ok = ok and same
        msgs.append(f"  {name:<14} 重启前 {a} → 重启后 {b}  {'✓' if same else '★ 丢了'}")
    return ok, msgs


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    # ★ 默认跑**两个领域**：重启缺陷在共享内核（`_seed`）里，
    #   只验制造业包 = 只验了一半。政务包此前从未进过这个回归。
    ap.add_argument("--domain", default="",
                    help="只验某个领域；默认 manufacturing + gov_catalog 都验")
    a = ap.parse_args()
    domains = [a.domain] if a.domain else ["manufacturing", "gov_catalog"]

    rc = 0
    for dom in domains:
        print()
        print("#" * 76)
        print(f"# 领域：{dom}")
        print("#" * 76)
        rc |= _one_domain(dom)
    return rc


def _one_domain(dom: str) -> int:
    shutil.rmtree(STATE, ignore_errors=True)
    # ① 冷启动  ② 带状态重启  ③ 再重启一次
    runs = [_probe("冷启动", STATE, dom),
            _probe("带状态重启", STATE, dom),
            _probe("再重启一次", STATE, dom)]

    W = 76
    print("=" * W)
    print("重启一致性回归：冷启动 vs 带状态重启")
    print("=" * W)
    print(f"  {'场景':<12} | {'判据':>4} | {'硬锚点':>6} | {'自检':>4} | {'冲突':>4} | 降级")
    print("  " + "-" * (W - 4))
    for r in runs:
        print(f"  {r['tag']:<12} | {r['n_clauses']:>4} | {r['n_anchors']:>6} | "
              f"{r['n_mismatch']:>4} | {r['n_conflicts']:>4} | {r['n_degradations']}")

    keys = ("n_clauses", "n_anchors", "n_mismatch", "n_conflicts", "n_degradations")
    base = {k: runs[0][k] for k in keys}
    bad = [r for r in runs[1:] if {k: r[k] for k in keys} != base]

    print()
    if bad:
        print("  ★ 不一致！带状态重启的状态与冷启动不同：")
        for r in bad:
            diff = {k: (base[k], r[k]) for k in keys if r[k] != base[k]}
            print(f"     {r['tag']}: " + ", ".join(f"{k} {a}→{b}" for k, (a, b) in diff.items()))
        print()
        print("  这类差异**不会报错、自检仍报 0 不一致**，只会让机械真值集悄悄变弱。")
        print("  查 `StandardService._seed`：是不是又有一条路径被提前 return 跳过了，")
        print("  或者某个局部变量遮蔽了参数名。")
        print("=" * W)
        return 1

    print("  ✓ 三次完全一致 —— 冷启动与带状态重启走的是同一条路径。")

    print()
    print("  持久化：学习成果不能在重启时丢")
    print("  " + "-" * (W - 4))
    p_ok, msgs = _persistence_check(dom)
    for m in msgs:
        print(m)
    if not p_ok:
        print()
        print("  ★ 有学习成果在重启时丢了。查 `_seed` 里 drop/reset 的范围，")
        print("    以及 `reset_anchors_from_standard` 是否误清了 kind != 'hard' 的锚点。")
        print("=" * W)
        return 1

    print("=" * W)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
