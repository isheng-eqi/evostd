"""判据语言 v2 实验：三类真盲区能不能被**语言扩张**消除？

这是二阶自进化在**制造包**上的落地（docs/22 只在演示样例上做过）。

实验设计
--------
对照组 A（v1）  ：`compile_standard(制造标准)` → 现有 34 条判据
实验组 B（v2）  ：A + 5 条新增条款（NOT_FUTURE / RANGE_BY_TABLE×2 / UNIQUE_ACROSS）

测三件事：
  1. **盲区数**：A 应报 4 条盲区；B 应报 0
  2. **不退化**：8 条合规资产在 B 下仍须零违例（R2 零误报）
  3. **不误伤**：A 下已被抓到的 8 条违规，B 下仍须被抓到（不能"修好一个坏一个"）

新增条款的**真值来源全部可追溯**：
  - 查表区间的表 ← 语料 DOC-MFG-001 / DOC-MFG-002 明文写的上限
  - 基准时间     ← 显式注入（不读时钟）
  - 唯一性字段   ← 条款第41条（真实性）的可机械化部分
"""
from __future__ import annotations

import json
from typing import Optional

from standard.compiler import Clause, ClauseKind, StandardVerifier, compile_standard
from standard.grammar_v2 import (ClauseKindV2, UniqueAcrossChecker, make_checker)


# ============================================================================
#  v2 新增条款（内容全部来自制造包语料，不是凭空造规则）
# ============================================================================
# 各领域"未来日期"要看哪个字段、唯一性要看哪个字段 —— 这是**领域知识**，不写死在语言里
DOMAIN_TIME_FIELD = {
    "manufacturing": "check_date",
    "gov_catalog": "publish_date",
}
DOMAIN_UNIQUE_FIELD = {
    "manufacturing": "file_hash",
    "gov_catalog": "file_hash",
}


def v2_clauses(standard_name: str, domain: str = "manufacturing",
               docs: Optional[list] = None) -> list[Clause]:
    """v2 新增条款 —— **领域无关**地生成。

    ★ 曾经把制造业的字段名（`maintenance_cycle_days` 等）写死在这里，
      这在换领域时会直接失效——正是可插拔性的实际缺口。
      现在：
        · 表条款由**该领域语料**机械抽出（`table_extractor`），字段名来自别名表
        · "未来日期看哪个字段""唯一性看哪个字段"来自 `DOMAIN_*` 映射（领域知识，人给一次）
        · 给不出映射/别名时**如实跳过**，不猜
    """
    S = standard_name
    out: list[Clause] = []
    tf = DOMAIN_TIME_FIELD.get(domain)
    if tf:
        out.append(Clause("第44条", "日期不得为未来值", ClauseKindV2.NOT_FUTURE,
                          f"{tf} 不得晚于登记基准时间。",
                          {"field": tf, "now_key": "now"}, standard=S))
    out.extend(clauses_from_tables(docs=docs, domain=domain))
    uf = DOMAIN_UNIQUE_FIELD.get(domain)
    if uf:
        out.append(Clause("第47条", f"{uf} 不得跨资产重复登记", ClauseKindV2.UNIQUE_ACROSS,
                          f"同一份原始文件不得登记在两个及以上的数据资产上。",
                          {"field": uf, "id_field": "asset_id" if domain == "manufacturing"
                           else "catalog_id", "ignore_empty": True}, standard=S))
    return out


def clauses_from_tables(start_no: int = 45, docs: Optional[list] = None,
                        domain: str = "manufacturing") -> list[Clause]:
    """从语料机械抽表 → 自动生成条款。抽不出就返回空（不猜）。"""
    try:
        import importlib

        from standard.table_extractor import extract_limit_tables, clauses_from_tables as gen

        # ★ 实测缺陷（2026-10-21，平台端到端调用暴露）：
        #   原写法 `from domains.manufacturing import standards as _ms` 拿到的是
        #   **包里的 `standards()` 函数**，不是标准名；`_ms()` 返回的对象没有
        #   `STANDARD_NAME`，于是第45/46条的 `source` 变成 **`《》第45条`**——
        #   条款能回指，标准名却丢了。对一个以"可溯源"为卖点的系统，这是硬伤。
        #   现在直接取**领域包的包级常量**，并且按 domain 取对应包，
        #   不再是政务包也去读制造业的标准名。
        _PKG = {
            "manufacturing": "domains.manufacturing",
            "gov_catalog": "domains.gov_catalog",
        }
        pkg_name = _PKG.get(domain, "domains.manufacturing")
        if docs is None:
            docs = importlib.import_module(f"{pkg_name}.corpus").all_docs()
        name = getattr(importlib.import_module(pkg_name), "STANDARD_NAME", "")
        if not name:
            # 大声失败：宁可显示"标准名缺失"，也不要悄悄生成一个《》第N条
            name = "（标准名缺失）"
        return gen(extract_limit_tables(docs), name, start_no=start_no, domain=domain)
    except Exception as e:
        print(f"[warn] 从语料抽表失败：{e}")
        return []


def extracted_ctx(docs: Optional[list] = None) -> dict:
    """语料里机械抽出来的限额表（不传 docs 时用制造业包）。"""
    try:
        from standard.table_extractor import extract_limit_tables, ctx_from_tables
        if docs is None:
            from domains.manufacturing.corpus import all_docs as _ad
            docs = _ad()
        return ctx_from_tables(extract_limit_tables(docs))
    except Exception:
        return {}


def cross_check_tables() -> dict:
    """★ 交叉校验：机械抽出来的表必须与人工抄的表**逐格一致**。

    不一致就说明其中一个是错的——这时候必须停下，而不是悄悄用其中一个。
    这是"人工抄写"这段过渡期里唯一可靠的防线。
    """
    mech = extracted_ctx()
    diffs = []
    for key, rows in HAND_TABLES.items():
        m = mech.get(key)
        if m is None:
            diffs.append({"table": key, "issue": "机械抽取未产出该表", "mech": None, "hand": rows})
            continue
        if m != rows:
            diffs.append({"table": key, "issue": "逐格不一致", "mech": m, "hand": rows})
    return {"ok": not diffs, "n_diff": len(diffs), "diffs": diffs,
            "n_tables_mech": len(mech), "n_tables_hand": len(HAND_TABLES)}


# ============================================================================
#  ctx 扩张：v2 判据需要的外部真值
# ============================================================================
# ★ 人工抄写的表——**只作为交叉校验的对照**，实际用的是从语料机械抽出来的（extracted_ctx）。
#   名字带 _hand 后缀是为了让"这张表是人抄的"这件事在代码里显式可见。
HAND_TABLES = {
    # ← DOC-MFG-001《设备台账登记规程（片段）》明文
    "maintenance_cycle_days_limit": {"A类": {"max": 90}, "B类": {"max": 180}, "C类": {"max": 365}},
    # ← DOC-MFG-002《设备点检作业规程（片段）》明文
    "check_interval_hours_limit": {"A类": {"max": 24}, "B类": {"max": 72}, "C类": {"max": 168}},
}

V2_CTX = {
    # 基准时间：**显式注入**（★ 不是 datetime.now()，判据仍是纯函数）
    "now": "2026-10-13",
}
V2_CTX.update(HAND_TABLES)          # 兜底；实际跑时会被机械抽取的覆盖


# ============================================================================
#  v2 验证器：点级 + 集合级两条路径
# ============================================================================
class StandardVerifierV2:
    """v1 的检查器 + v2 的检查器；集合级判据走 check_all。"""

    def __init__(self, clauses: list[Clause]):
        self.clauses = clauses
        self.point = []
        self.set_level = []
        for c in clauses:
            ck = make_checker(c)
            (self.set_level if getattr(ck, "is_set_level", False) else self.point).append(ck)

    def findings(self, asset: dict, ctx: dict) -> list:
        out = [f for f in (ck.check(asset, ctx) for ck in self.point) if f is not None]
        # 集合级判据：★ 按**归因对象**挂载，不是按"全组成员"挂载。
        #   第一版按 evidence["assets"]（全组）挂，结果把归因给"后登记方"的结论
        #   同时挂到了原件上 → 合规资产被误报，直接违反 R2（零误报）。
        #   集合级判据的归因字段（attributed_to）缺失时，才回落为全组挂载。
        me = asset.get("asset_id")
        for ck in self.set_level:
            for f in ck.check_all(ctx.get("_assets", []) or [], ctx):
                ev = f.evidence or {}
                target = ev.get("attributed_to")
                if target is not None:
                    if me == target:
                        out.append(f)
                elif me in (ev.get("assets") or []):
                    out.append(f)
        return out

    def violations(self, asset: dict, ctx: dict) -> list:
        return [f for f in self.findings(asset, ctx) if not f.ok]

    def verify(self, assets: list[dict], ctx: dict) -> list:
        ctx = dict(ctx)
        ctx["_assets"] = assets
        res = []
        for a in assets:
            fs = self.findings(a, ctx)
            res.append({"asset_id": a.get("asset_id"),
                        "findings": fs,
                        "violations": [f for f in fs if not f.ok],
                        "compliance": (sum(1 for f in fs if f.ok) / len(fs)) if fs else 1.0})
        return res


# ============================================================================
#  实验
# ============================================================================
def blind_spots(assets, verifier, ctx, label_of):
    """盲区 = 人工标为违规，但判据一条都没抓到。"""
    ctx = dict(ctx)
    ctx["_assets"] = assets
    out = []
    for a in assets:
        if label_of(a) != "violating":
            continue
        if not verifier.violations(a, ctx):
            out.append(a.get("asset_id"))
    return out


def false_alarms(assets, verifier, ctx, label_of):
    """误报 = 人工标为合规，但判据抓到了。返回 (asset_id, 违例明细)。"""
    ctx = dict(ctx)
    ctx["_assets"] = assets
    out = []
    for a in assets:
        if label_of(a) != "compliant":
            continue
        vs = verifier.violations(a, ctx)
        if vs:
            out.append((a.get("asset_id"),
                        [(f.clause_id, f.message[:70]) for f in vs]))
    return out


def f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) else 1.0
    r = tp / (tp + fn) if (tp + fn) else 1.0
    return (2 * p * r / (p + r) if (p + r) else 0.0), p, r


def run() -> dict:
    from domains.manufacturing import standards
    from domains.manufacturing.assets import all_assets, ctx as actx

    std = standards()
    name = getattr(std, "STANDARD_NAME", "（演示样例）")
    raw = all_assets()
    A = [{k: v for k, v in a.items() if not k.startswith("_")} for a in raw]
    lab = {a.get("asset_id"): a.get("_label") for a in raw}
    why = {a.get("asset_id"): a.get("_why", "") for a in raw}
    label_of = lambda a: lab.get(a.get("asset_id"))

    # ---- 编译 v1 ----
    compiled = compile_standard(std)
    v1 = StandardVerifier(compiled)
    v1_clauses = [ck.clause for ck in compiled.checkers]      # CompileResult 无 .clauses
    base_ctx = dict(actx()) if isinstance(actx(), dict) else {}

    # ---- 编译 v2 = v1 + 新条款 ----
    xcheck = cross_check_tables()                       # ★ 先校验表，再跑实验
    new = v2_clauses(name)
    v2 = StandardVerifierV2(v1_clauses + new)
    ctx2 = dict(base_ctx)
    ctx2.update(V2_CTX)
    ctx2.update(extracted_ctx())                        # ★ 机械抽取的表覆盖人工抄的

    b1 = blind_spots(A, v1, base_ctx, label_of)
    b2 = blind_spots(A, v2, ctx2, label_of)
    fa1 = false_alarms(A, v1, base_ctx, label_of)
    fa2 = false_alarms(A, v2, ctx2, label_of)

    n_bad = sum(1 for a in A if label_of(a) == "violating")
    n_good = sum(1 for a in A if label_of(a) == "compliant")
    caught1 = n_bad - len(b1)
    caught2 = n_bad - len(b2)
    F1_1, P1, R1_ = f1(caught1, len(fa1), len(b1))
    F1_2, P2, R2_ = f1(caught2, len(fa2), len(b2))

    # ---- 回归：v1 抓到的，v2 必须仍抓到 ----
    regressions = []
    for a in A:
        if label_of(a) != "violating":
            continue
        if a.get("asset_id") not in b1 and a.get("asset_id") in b2:
            regressions.append(a.get("asset_id"))

    return {
        "standard": name,
        "n_assets": len(A), "n_violating": n_bad, "n_compliant": n_good,
        "table_cross_check": xcheck,
        "v1": {"n_clauses": len(v1_clauses), "blindspots": b1,
               "false_alarms": fa1, "F1": round(F1_1, 4),
               "P": round(P1, 4), "R": round(R1_, 4),
               "detail": {i: why.get(i, "") for i in b1}},
        "v2": {"n_clauses": len(v1_clauses) + len(new), "blindspots": b2,
               "false_alarms": fa2, "F1": round(F1_2, 4),
               "P": round(P2, 4), "R": round(R2_, 4),
               "new_clauses": [c.clause_id for c in new]},
        "regressions": regressions,
    }


def main() -> None:
    r = run()
    W = 78
    print("=" * W)
    print("判据语言 v2 实验：三类真盲区能否被「语言扩张」消除")
    print(f"标准：{r['standard']}")
    print(f"资产：{r['n_assets']} 条（违规 {r['n_violating']} / 合规 {r['n_compliant']}）")
    xc = r["table_cross_check"]
    print(f"限额表交叉校验：机械抽取 {xc['n_tables_mech']} 张 vs 人工抄写 "
          f"{xc['n_tables_hand']} 张 → {'一致 ✓' if xc['ok'] else '★ 不一致 ' + str(xc['diffs'])}")
    print("=" * W)

    for tag, key, note in (("v1（现有语言）", "v1", "对照组"),
                           ("v2（扩张语言）", "v2", "实验组")):
        d = r[key]
        print(f"\n【{tag}】{note}   判据 {d['n_clauses']} 条")
        print(f"  盲区 {len(d['blindspots'])} 条  误报 {len(d['false_alarms'])} 条")
        print(f"  精确率 {d['P']:.4f}  召回率 {d['R']:.4f}  辨别力 F1 {d['F1']:.4f}")
        for i in d["blindspots"]:
            print(f"    ★ 盲区 {i}")

    print("\n" + "-" * W)
    print("① 盲区变化")
    print(f"   v1 {len(r['v1']['blindspots'])} 条 → v2 {len(r['v2']['blindspots'])} 条")
    print("② 误报（R2 零误报是准入门槛）")
    print(f"   v1 {len(r['v1']['false_alarms'])} 条 → v2 {len(r['v2']['false_alarms'])} 条")
    print("③ 回归检查（v1 抓到的，v2 必须仍抓到）")
    print(f"   退化 {len(r['regressions'])} 条" + (f"：{r['regressions']}" if r["regressions"] else " ✓"))
    print("④ 辨别力")
    print(f"   F1 {r['v1']['F1']:.4f} → {r['v2']['F1']:.4f}")
    print("=" * W)

    if not r["v2"]["blindspots"] and not r["v2"]["false_alarms"] and not r["regressions"]:
        print("★ 结论：三类盲区被【语言扩张】消除，且零误报、零退化。")
        print("  这证明该扩张的是判据语言，而不是逐条打补丁。")
    else:
        print("△ 结论：尚有残留，见上。")
    print("=" * W)


if __name__ == "__main__":
    main()
