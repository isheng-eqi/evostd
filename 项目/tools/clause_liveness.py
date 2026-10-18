"""判据活性检查：**哪些判据从未被任何违规样本触发过**。

为什么需要
----------
实测（政务包）：

    判据总数 23 | 被违规样本触发过的 5
    ★ 从未被违规样本检验过的判据：18 条

**23 条判据里只有 5 条经过实测。** 其余 18 条"存在"，但从未被证明过它们真的能判违规。
它们可能是：

  · 参数写错（阈值反了、取值域写反）
  · 字段名对不上（**静默弃权** —— 见 docs/42 第一百二十节）
  · 逻辑反了（把"不得晚于"实现成"不得早于"）

**而这三种情况都不会报错、不会出现在任何告警里。**
判据库看起来齐整（数量、覆盖率、自检全绿），实际有 78% 的判据没被检验过。

与"锚点自检"的关系
------------------
锚点自检回答的是：「**锚点**对不对」（抽出来的真值是否自洽）。
本检查回答的是：「**判据**活不活」（它到底会不会触发）。

两者互补，缺一不可：
  · 锚点错了 → 判据自检会抓到（锚点与判据不一致）
  · 判据从没触发过 → **锚点自检抓不到**（它根本没参与过判定）

用法
----
    python tools/clause_liveness.py              # 两个领域都查
    python tools/clause_liveness.py gov_catalog  # 只查一个

★ 注意：本检查只**报数**，不自动造样本。
   "某条判据没被触发过"不等于"它坏了"——可能只是**当前资产集里没有那种违规**。
   两种情况要分开看：
     · 资产集覆盖不到 → 该加资产（第 ⑤ 项）
     · 给了违规样本仍不触发 → **判据坏了**（该修判据）
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))


def check(domain: str = "gov_catalog") -> dict:
    import importlib
    # ★ 顺序很重要：**必须先设 EVOSTD_STATE，再 import evostd.server**。
    #   `evostd.server` 在**模块导入时**就根据 env 算好了 `DEFAULT_STATE_DIR`；
    #   先 import 再设 env，`build_service()` 会去读**默认目录** ——
    #   于是本脚本**静默地检查了另一个库**。
    #   （`shadow_map.py` 实测踩到这个坑并报了假问题，回头发现这里也一样。）
    os.environ.setdefault("EVOSTD_NOW", "2026-10-13")
    os.environ["EVOSTD_STATE"] = os.path.join(_ROOT, "src", "runs", f"liveness-{domain}")
    import evostd.server as S

    S.build_service(domain)
    svc = S._svc()

    # 只用**原始**资产（含 _label），因为它决定了哪些样本是"故意违规"的
    assets_mod = importlib.import_module(f"domains.{domain}.assets")
    raw = assets_mod.all_assets()
    clean = [{k: v for k, v in a.items() if not k.startswith("_")} for a in raw]
    ctx = svc._ctx_with_assets(clean)

    fired: dict[str, list] = {}
    raised: dict[str, list] = {}
    for a in clean:
        for ck in svc._lib_checkers():
            cid = ck.clause.clause_id
            try:
                f = ck.check(a, ctx)
            except Exception as e:                    # 判据抛异常也是"没在正常工作"
                raised.setdefault(cid, []).append(f"{a.get('asset_id') or a.get('catalog_id')}: {type(e).__name__}")
                continue
            if f is not None and not f.ok:
                fired.setdefault(cid, []).append(a.get("asset_id") or a.get("catalog_id"))

    kinds = {}
    never = []
    for ck in svc._lib_checkers():
        cid = ck.clause.clause_id
        k = getattr(ck.clause.kind, "value", "?")
        kinds[k] = kinds.get(k, 0) + 1
        if cid not in fired:
            p = getattr(ck.clause, "params", {}) or {}
            base_asset = dict(clean[0]) if clean else {}
            ok_anchor, why = _fires_on_own_anchor(ck.clause, base_asset, ctx)
            never.append({"clause_id": cid, "kind": k,
                          "field": p.get("field") or p.get("left") or p.get("when_field") or "",
                          "fires_on_own_anchor": ok_anchor,
                          "verdict": ("缺资产（判据本身能触发）" if ok_anchor
                                      else f"★ 判据可疑（{why}）")})

    n_all = len(list(svc._lib_checkers()))
    return {
        "domain": domain,
        "n_clauses": n_all,
        "n_fired": len(fired),
        "n_never_fired": len(never),
        "rate": round(len(fired) / n_all, 4) if n_all else 0.0,
        "n_missing_asset": sum(1 for x in never if x["fires_on_own_anchor"]),
        "n_suspect": sum(1 for x in never if not x["fires_on_own_anchor"]),
        "kinds": kinds,
        "never_fired": never,
        "raised": raised,
        "note": ("`never_fired` 不等于「判据坏了」 —— 也可能是**当前资产集里没有那种违规**。"
                 "两者要分开：资产覆盖不到 → 加资产；给了违规样本仍不触发 → 修判据。"),
    }



def _fires_on_own_anchor(clause, base_asset: dict, ctx: dict) -> tuple[bool, str]:
    """★ 判据在**它自己的硬锚点**上会不会触发？

    这是把"从未触发"分成两种情况的**现成手段**：
      · 会触发   → 判据本身没问题，只是**当前资产集里没有那种违规**（缺资产）
      · 不触发   → **判据可疑**（参数写错 / 字段名对不上 / 逻辑反了）

    复用的就是锚点自检那套机制（`derive_hard_anchors_v2` 会为一条判据
    造一个**它自己应该判违规**的最小夹具）。
    """
    try:
        from standard.anchors_v2 import derive_hard_anchors_v2
        from standard.grammar_v2 import make_checker
        anchors = derive_hard_anchors_v2([clause], base_asset, ctx)
        ck = make_checker(clause)
        for a in anchors:
            try:
                asset = a.representative() if hasattr(a, "representative") else None
            except Exception:
                asset = None
            if asset is None:
                asset = getattr(a, "asset", None)
            if asset is None:
                continue
            try:
                f = ck.check(asset, ctx)
            except Exception as e:
                return False, f"锚点执行异常 {type(e).__name__}"
            if f is not None and not f.ok:
                return True, ""
        return False, "在自己的锚点上也没触发"
    except Exception as e:
        return False, f"锚点推导失败 {type(e).__name__}"


def main() -> int:
    domains = [sys.argv[1]] if len(sys.argv) > 1 else ["manufacturing", "gov_catalog"]
    W = 76
    rc = 0
    for d in domains:
        r = check(d)
        print("=" * W)
        print(f"判据活性检查：{d}")
        print("=" * W)
        print(f"  判据总数 {r['n_clauses']} | 被违规样本触发过 {r['n_fired']} "
              f"| **从未触发 {r['n_never_fired']}**（{r['rate']*100:.1f}% 经过实测）")
        print(f"  类型分布：{r['kinds']}")
        if r["raised"]:
            print(f"  ★ 有判据在 check 时抛异常：{list(r['raised'])[:5]}")
        print(f"  ★ 其中：**缺资产** {r.get('n_missing_asset',0)} 条"
              f"（判据能在自己的锚点上触发，只是资产集里没那种违规）"
              f" | **判据可疑** {r.get('n_suspect',0)} 条")
        if r["never_fired"]:
            print(f"  从未触发的判据（前 12 条）：")
            for x in r["never_fired"][:12]:
                print(f"    {x['clause_id']:<8} [{x['kind']:<8}] field={x['field']:<22} {x.get('verdict','')}")
        print()
        print("  注意：`从未触发` ≠ `判据坏了`。也可能是当前资产集里没有那种违规。")
        print("        资产覆盖不到 → 加资产；给了违规样本仍不触发 → 修判据。")
        if r["n_never_fired"]:
            rc |= 2                                   # 有未检验判据 → 非零退出，便于 CI 卡住
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
