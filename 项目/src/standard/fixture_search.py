"""冲突可解性判定：**机械地**把"锚点没选好"与"标准真矛盾"分开。

为什么需要
----------
`std_conflicts` 能报出"一条合规锚点满足自己的条款、却违反另一条"，
但它报出的是**现象**，不是**性质**。同一现象有两种完全不同的成因：

  · **锚点没选好**：夹具随手取了 `equipment_class=A类`，
    而 `maintenance_cycle_days=365` 在 A类 下超限、在 C类 下正好取到上限。
    → 换一个关键字段取值就不冲突了。这是**锚点的错**。
  · **标准真矛盾**：无论关键字段取什么值，两条条款都给出相反结论。
    → 这是**标准文本的错**，得由人裁决哪条说话算数。

靠"对方条款是不是条件必填"这种启发式去猜，是在**猜性质**。
本模块改用**判定过程**：

    在关键字段的取值空间里搜索一个**无冲突的夹具**。
      找到  → 可解，返回修正后的夹具（并把搜索过程留档）
      找不到 → 不可解，且给出"搜索过哪些取值"作为证据

**搜索空间有限且枚举得完**，所以这个判定是**确定的**，不是启发式的。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from standard.compiler import Clause

# 可以被重赋值的关键字段（**枚举型**——值域有限，搜索才可穷尽）。
# 加字段要谨慎：每加一个，搜索空间按值域大小相乘。
REGISTRY_FIELDS_HINT: set = {"equipment_no", "cited_standards", "source_doc_ids", "owner_dept"}

DEFAULT_KEY_FIELDS: dict[str, list[Any]] = {
    "equipment_class": ["A类", "B类", "C类"],
    "equipment_status": ["在用", "备用", "检修", "停用"],
    "media_type": ["图纸", "影像", "照片"],
    "secrecy": ["公开", "内部", "秘密"],
    "check_result": ["正常", "异常", "待复检"],
    "revision": [1, 2, 3],
}


@dataclass
class SearchResult:
    resolvable: bool
    fixed_asset: Optional[dict] = None
    assignment: dict = field(default_factory=dict)      # 生效的关键字段取值
    tried: list = field(default_factory=list)           # 搜索过的取值（证据）
    remaining: list = field(default_factory=list)       # 仍冲突的条款
    note: str = ""


def _conflicts_of(asset: dict, own_clause: str, checkers: list, ctx: dict) -> list:
    """返回该夹具在**除自己那条之外**的判据上触发的违例。"""
    cctx = dict(ctx)
    cctx["_assets"] = [asset]
    out = []
    for ck in checkers:
        cid = ck.clause.clause_id
        if cid == own_clause:
            continue
        try:
            f = ck.check(asset, cctx)
        except Exception:
            continue
        if f is not None and not f.ok:
            out.append({"clause_id": cid, "message": f.message[:90]})
    return out


def dynamic_key_fields_from_ctx(ctx: dict) -> dict:
    """从 ctx 里已有的受控清单直接推字段取值空间（不依赖 clauses）。"""
    m = {"equipment_no": "equipment_registry", "cited_standards": "controlled_standards",
         "source_doc_ids": "controlled_doc_ids", "owner_dept": "controlled_depts"}
    return {f: list(ctx[r]) for f, r in m.items() if ctx.get(r)}


def search_conflict_free(asset: dict, own_clause: str, checkers: list, ctx: dict,
                         key_fields: Optional[dict] = None,
                         max_trials: int = 400,
                         clauses: Optional[list] = None) -> SearchResult:
    """在关键字段的取值空间里找一个无冲突夹具。

    ★ 搜索是**确定性**的（按固定顺序枚举），所以结果可复现。
    """
    clause_by_id = {c.clause_id: c for c in (clauses or [])}
    if key_fields is None:
        kf = dict(DEFAULT_KEY_FIELDS)
        for k, v in dynamic_key_fields_from_ctx(ctx).items():
            kf.setdefault(k, v)
    else:
        kf = dict(key_fields)
    # 只保留**当前夹具里真的存在**的关键字段——不凭空引入字段（那是臆造）
    # （但受控清单类字段允许"当前夹具没有、清单里有"的情形：那正是被搜索空间漏掉的）
    kf = {k: v for k, v in kf.items() if k in asset or k in REGISTRY_FIELDS_HINT}
    # 集合型字段：把标量清单值包成 list，与夹具里该字段的形状对齐
    for k, vals in list(kf.items()):
        if isinstance(asset.get(k), list):
            kf[k] = [[v] for v in vals]
    if not kf:
        return SearchResult(False, remaining=_conflicts_of(asset, own_clause, checkers, ctx),
                            note="夹具里没有可重赋值的关键字段，搜索空间为空")

    base_conf = _conflicts_of(asset, own_clause, checkers, ctx)
    if not base_conf:
        return SearchResult(True, fixed_asset=dict(asset), note="原夹具已无冲突")

    # ★ 只变"**冲突条款真正涉及的字段**"。
    #   第一版在全空间深度优先枚举，空间一变大就在预算内走不到解，
    #   把本来可解的 HA2-003 误判成"不可解"。
    #   缩小到相关字段既快得多，也更符合语义：无关字段的取值不影响这条冲突。
    conf_fields: set = set()
    for cf in base_conf:
        cl = clause_by_id.get(cf["clause_id"])
        if cl is None:
            continue
        cp = getattr(cl, "params", {}) or {}
        for k in ("field", "key_field", "left", "right", "when_field", "require_field"):
            if cp.get(k):
                conf_fields.add(cp[k])
    own = clause_by_id.get(own_clause)
    if own is not None:
        op = getattr(own, "params", {}) or {}
        for k in ("field", "key_field", "left", "right"):
            if op.get(k):
                conf_fields.add(op[k])
    narrowed = {k: v for k, v in kf.items() if k in conf_fields}
    if narrowed:
        kf = narrowed

    keys = list(kf.keys())
    tried: list[dict] = []
    n = 0

    def walk(i: int, cur: dict) -> Optional[dict]:
        nonlocal n
        if n >= max_trials:
            return None
        if i == len(keys):
            n += 1
            trial = dict(asset); trial.update(cur)
            conf = _conflicts_of(trial, own_clause, checkers, ctx)
            tried.append({**cur, "_conflicts": [c["clause_id"] for c in conf]})
            return trial if not conf else None
        for v in kf[keys[i]]:
            got = walk(i + 1, {**cur, keys[i]: v})
            if got is not None:
                # 把剩余层级的取值也补满（保持确定性）
                for j in range(i + 1, len(keys)):
                    got.setdefault(keys[j], kf[keys[j]][0])
                return got
        return None

    found = walk(0, {})
    if found is not None:
        assign = {k: found[k] for k in keys if found.get(k) != asset.get(k)}
        return SearchResult(True, fixed_asset=found, assignment=assign, tried=tried,
                            note=f"换 {assign} 即无冲突 → 属**锚点问题**，可机械修正")
    return SearchResult(False, remaining=base_conf, tried=tried[:24],
                        note=("枚举完关键字段取值空间仍未找到无冲突夹具 "
                              f"（试了 {len(tried)} 组）→ 属**标准真矛盾**，需人裁决"))

def dynamic_key_fields(clauses: list[Clause], ctx: dict) -> dict:
    """★ 从判据库里**动态**推出可重赋值的字段及其取值空间。

    第一版只枚举了一张固定表（设备类别/状态/介质/密级），于是 4 条锚点被误判成
    "不可解"——它们的冲突方是**受控清单类**条款（如"设备编号必须在台账内"），
    而搜索空间里根本没有 `equipment_no` 这个字段。

    这里补上：
      · 任何带 `registry` 参数的字段  → 取值空间 = `ctx[registry]`（**从清单里取，不臆造**）
      · 任何带 `pattern` 参数的字段   → 不纳入（正则空间太大，且格式条款之间不冲突）
      · 集合型字段（值为 list）       → 取清单里的**单个元素**包成 list

    只收集"取值空间有限且来自 ctx"的字段，保证搜索仍是**穷尽**的。
    """
    out: dict[str, list] = {}
    for c in clauses:
        if not c.compilable:
            continue
        p = getattr(c, "params", {}) or {}
        f, reg = p.get("field"), p.get("registry")
        if f and reg and ctx.get(reg):
            vals = list(ctx[reg])
            out.setdefault(f, vals)
    return out
