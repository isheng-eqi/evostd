"""硬锚点机械抽取器：从条款文本里"零人工"榨出可判定的真值。

为什么这是 P0（来自 docs/31 的实测）：
    13 条机械抽出的锚点，把误纳率从 0.07 压到 0.00，并把一个灾难性的索取策略
    从 F1 0.63 救到 0.99 —— 收益超过我设计的两个"聪明机制"。
    原因很简单：**条款文本自己就定义了真值，不需要任何人点头。**

设计原则
--------
1. **一个条款能抽出什么，由它的语义类型决定**，不是猜的：
   RANGE 边界值 / ENUM 词表 / PATTERN 正反例 / REQUIRED 缺字段 /
   CONDITIONAL 条件成立时的缺字段 / CONSISTENCY 序关系反例 /
   REFERENCE 清单内外 / FORBIDDEN 黑名单命中
2. **抽出来的锚点天然分两类**：
   - 合规锚点 → 用来**证伪**（判据若在合规锚点上触发，立即否决）
   - 违规锚点 → 用来**证实**
   而实测表明：只有证实会漏（F1 崩到 0.50），只有证伪也会漏——**两类都要有**。
3. **抽不出来的部分明确留白**：那些 `compilable=False` 的条款进入"软确认"队列，
   那才是需要人点头的地方。硬锚点覆盖率越高，人越省。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from standard.compiler import Clause, ClauseKind, _CHECKER_BY_KIND


@dataclass
class HardAnchor:
    """一条机械抽取的锚点。source_clause 指回条款，可追溯。"""
    anchor_id: str
    asset: dict
    label: str            # compliant | violating
    source_clause: str
    how: str              # 机械抽取的规则说明（给人看）
    kind: str = "hard"    # hard | soft

    def to_dict(self) -> dict:
        return {"anchor_id": self.anchor_id, "label": self.label,
                "source_clause": self.source_clause, "how": self.how,
                "kind": self.kind, "asset": self.asset}


# ---------------------------------------------------------------------------
#  机械抽取
# ---------------------------------------------------------------------------
_PATTERN_SAMPLES = {
    r"[A-Za-z0-9\-]{8,64}": ("AST-2026-0001", "短"),
    r"[0-9a-f]{64}": ("a" * 64, "ZZZ"),
    r"\d{4}-\d{2}-\d{2}": ("2026-03-01", "2026/03/01"),
    r"[A-Z]{2,4}/\d{4,6}": ("GB/T 12345", "gb-123"),
    r"[0-9]{4}-[0-9]{4}": ("2026-0001", "26-1"),
}


def _gen_from_pattern(pat: str, max_len: int = 200) -> Optional[str]:
    """★ 从正则**本身**生成一个匹配样例（而不是查表）。

    为什么必须这样做：查表对不认识的模式会退化成默认样例，
    而默认样例往往**不匹配** —— 自检第一次跑就抓到了 3 条这样的错误锚点。
    宁可生成不出来（返回 None → 跳过该条款），也不要造一条错的锚点。

    只覆盖常见形态；生成后**一律用 re.fullmatch 复核**。
    """
    def parse(s: str, i: int) -> tuple[str, int]:
        out = []
        while i < len(s):
            ch = s[i]
            if ch == "|":                       # 交替：只取第一个分支
                break
            if ch == ")":
                break
            if ch == "(":                       # 分组/非捕获组：递归
                i += 1
                if s.startswith("?:", i):
                    i += 2
                piece, i = parse(s, i)
                if i < len(s) and s[i] == ")":
                    i += 1
            elif ch == "[":                     # 字符类：取第一个可用字符
                j = s.index("]", i)
                cls = s[i + 1:j]
                if cls.startswith("^"):
                    piece = "1"
                elif "0-9" in cls:
                    piece = "9"
                elif "a-f" in cls:
                    piece = "a"
                elif "A-Z" in cls:
                    piece = "A"
                elif "a-z" in cls:
                    piece = "a"
                else:
                    cand = [c for c in cls if c.isalnum()]
                    piece = cand[0] if cand else "a"
                i = j + 1
            elif ch == "\\":                    # 转义类
                nxt = s[i + 1] if i + 1 < len(s) else ""
                piece = {"d": "9", "w": "a", "s": "-"}.get(nxt, nxt)
                i += 2
            elif ch in "*+?":                   # 量词（此处不应出现，安全跳过）
                i += 1
                continue
            else:
                piece = ch
                i += 1
            # 量词
            rep = 1
            if i < len(s) and s[i] == "{":
                j = s.index("}", i)
                body = s[i + 1:j]
                rep = int(body.split(",")[0])
                i = j + 1
            elif i < len(s) and s[i] == "+":
                i += 1
            elif i < len(s) and s[i] == "*":
                i += 1
                rep = 0
            elif i < len(s) and s[i] == "?":
                i += 1
            out.append(piece * rep)
        return "".join(out), i

    try:
        s, _ = parse(pat, 0)
    except Exception:
        return None
    if not s or len(s) > max_len:
        return None
    import re as _re
    return s if _re.fullmatch(pat, s) else None


def _pattern_pair(pat: str, default_field: str) -> tuple[Optional[str], Optional[str]]:
    """返回 (合规样例, 违规样例)；任一为 None 表示生成不出来，调用方应跳过。"""
    if pat in _PATTERN_SAMPLES:
        return _PATTERN_SAMPLES[pat]
    ok = _gen_from_pattern(pat)
    if ok is None:
        return None, None
    import re as _re
    for bad in ("", "!!invalid!!", "短", "ZZZ", "2026/03/01", "x"):
        if bad != ok and not _re.fullmatch(pat, bad):
            return ok, bad
    return ok, None


def derive_hard_anchors(clauses: list[Clause], base_asset: dict,
                        ctx: dict | None = None) -> list[HardAnchor]:
    """从条款里机械抽取锚点。base_asset 必须是一条**完全合规**的资产模板。"""
    ctx = ctx or {}
    out: list[HardAnchor] = []
    n = [0]

    def emit(asset: dict, label: str, clause: Clause, how: str) -> None:
        n[0] += 1
        out.append(HardAnchor(anchor_id=f"HA-{n[0]:03d}", asset=asset, label=label,
                              source_clause=clause.clause_id, how=how))

    for c in clauses:
        if not c.compilable:
            continue
        p = c.params
        f = p.get("field")

        if c.kind == ClauseKind.ENUM:
            for v in p["allowed"]:
                a = copy.deepcopy(base_asset); a[f] = v
                emit(a, "compliant", c, f"{f} 取受控词表内取值「{v}」")
            a = copy.deepcopy(base_asset); a[f] = "∉受控词表"
            emit(a, "violating", c, f"{f} 取受控词表外取值")

        elif c.kind == ClauseKind.RANGE:
            lo, hi = p.get("min"), p.get("max")
            if lo is not None:
                a = copy.deepcopy(base_asset); a[f] = lo
                emit(a, "compliant", c, f"{f} 取下界 {lo}")
                a = copy.deepcopy(base_asset); a[f] = lo - 1
                emit(a, "violating", c, f"{f} 低于下界 {lo}")
            if hi is not None:
                a = copy.deepcopy(base_asset); a[f] = hi
                emit(a, "compliant", c, f"{f} 取上界 {hi}")
                a = copy.deepcopy(base_asset); a[f] = hi + 1
                emit(a, "violating", c, f"{f} 超过上界 {hi}")

        elif c.kind == ClauseKind.PATTERN:
            ok, bad = _pattern_pair(p["pattern"], f)
            if ok is None or bad is None:
                continue        # 生成不出可信样例 → 跳过，绝不造错锚点
            a = copy.deepcopy(base_asset); a[f] = ok
            emit(a, "compliant", c, f"{f} 取合法样例（由正则生成并复核）")
            a = copy.deepcopy(base_asset); a[f] = bad
            emit(a, "violating", c, f"{f} 取非法样例")

        elif c.kind == ClauseKind.REQUIRED:
            a = copy.deepcopy(base_asset); a.pop(f, None)
            emit(a, "violating", c, f"{f} 缺失（必填）")
            # 空串也算违规（这是很常见的实现差异点，值得单列一条）
            a = copy.deepcopy(base_asset); a[f] = "   "
            emit(a, "violating", c, f"{f} 为空白串（必填）")

        elif c.kind == ClauseKind.CONDITIONAL:
            wf, wv, rf = p["when_field"], p["when_value"], p["require_field"]
            a = copy.deepcopy(base_asset); a[wf] = wv; a.pop(rf, None)
            emit(a, "violating", c, f"{wf}={wv} 时 {rf} 缺失")
            a = copy.deepcopy(base_asset); a[wf] = wv; a[rf] = "已填写"
            emit(a, "compliant", c, f"{wf}={wv} 且 {rf} 已填")

        elif c.kind == ClauseKind.CONSISTENCY:
            l, r, op = p["left"], p["right"], p["op"]
            # 基础模板里若没有这两个字段，无法机械构造对照 → 跳过（不臆造字段）
            if l not in base_asset or r not in base_asset:
                continue
            a = copy.deepcopy(base_asset)
            if op == "<=":
                a[l], a[r] = "2026-12-31", "2026-01-01"      # 反例
            elif op == ">=":
                a[l], a[r] = "2026-01-01", "2026-12-31"
            else:
                a[l], a[r] = 1, 2
            emit(a, "violating", c, f"{l} {op} {r} 的反例")
            a = copy.deepcopy(base_asset); a[l] = a[r]
            emit(a, "compliant", c, f"{l} 与 {r} 相等（满足 {op}）")

        elif c.kind == ClauseKind.REFERENCE:
            reg = p.get("registry")
            members = ctx.get(reg, [])
            if members:
                a = copy.deepcopy(base_asset)
                a[f] = [members[0]] if isinstance(base_asset.get(f), list) else members[0]
                emit(a, "compliant", c, f"{f} 取受控清单 {reg} 内条目")
            a = copy.deepcopy(base_asset)
            # 取一个必然不在清单里的值
            a[f] = ["∉清单"] if isinstance(base_asset.get(f), list) else "∉清单"
            emit(a, "violating", c, f"{f} 取受控清单外条目")

        elif c.kind == ClauseKind.FORBIDDEN:
            reg = p.get("registry")
            banned = ctx.get(reg, [])
            if banned:
                a = copy.deepcopy(base_asset)
                a[f] = [banned[0]] if isinstance(base_asset.get(f), list) else banned[0]
                emit(a, "violating", c, f"{f} 命中禁止清单 {reg} 的条目")
            a = copy.deepcopy(base_asset)
            a[f] = ["∉禁止清单"] if isinstance(base_asset.get(f), list) else "∉禁止清单"
            emit(a, "compliant", c, f"{f} 不在禁止清单 {reg} 内")

    return out


def verify_anchors(anchors: list[HardAnchor], clauses: list[Clause],
                   ctx: dict | None = None) -> dict:
    """★ 抽取器自检：每条硬锚点对**它来源的那条判据**的结论，必须与标签一致。

    这是"锚点可信"的第一道关——如果抽取器自己抽错了，
    后面所有裁决都建立在错的基础上（docs/23 发现 2 的教训：盲区必须实测）。
    """
    ctx = ctx or {}
    by_id = {c.clause_id: c for c in clauses}
    mismatches = []

    def _g(ha, key, default=None):
        """锚点既可能是 HardAnchor 对象，也可能是 store 里的 dict —— 两种都读。"""
        if isinstance(ha, dict):
            return ha.get(key, default)
        return getattr(ha, key, default)

    all_assets = [_g(ha, "asset", {}) for ha in anchors]
    for ha in anchors:
        c = by_id.get(_g(ha, "source_clause"))
        if c is None:
            continue
        # ★ 用 make_checker 而不是直接查 _CHECKER_BY_KIND：
        #   v2 新增的三类条款不在 v1 的表里，直接查会 KeyError ——
        #   实测表现为 std_audit 整个崩掉（锚点自检是审计的一环）。
        from standard.grammar_v2 import make_checker
        chk = make_checker(c)
        att = _g(ha, "attributed_to")
        assets = _g(ha, "assets") or all_assets
        anchor_asset = _g(ha, "asset", {})
        # ★ 标识字段从条款参数读，不能写死 "asset_id" ——
        #   制造业用 asset_id，政务用 catalog_id。写死会让集合级锚点在换领域后
        #   找不到被归因的那一条，自检报"不一致"（实测政务包 1 处）。
        _idf = (getattr(c, "params", {}) or {}).get("id_field", "asset_id")
        if att:
            for x in assets:
                if x.get(_idf) == att or x.get("asset_id") == att:
                    anchor_asset = x
                    break
        cctx = dict(ctx)
        cctx["_assets"] = assets
        f = chk.check(anchor_asset, cctx)
        ok = True if f is None else f.ok
        expect_ok = (_g(ha, "label") == "compliant")
        if ok != expect_ok:
            mismatches.append({"anchor": _g(ha, "anchor_id"), "clause": c.clause_id,
                               "label": _g(ha, "label"), "checker_says_ok": ok,
                               "how": _g(ha, "how", "")})
    return {"n_anchors": len(anchors), "n_mismatch": len(mismatches),
            "mismatches": mismatches[:10],
            "verdict": "ok" if not mismatches else "★ 抽取器与判据不一致，必须先修抽取器"}


def _ag(ha, key, default=None):
    """锚点既可能是 HardAnchor 对象，也可能是 store 里的 dict —— 两种都读。"""
    if isinstance(ha, dict):
        return ha.get(key, default)
    return getattr(ha, key, default)


def coverage(clauses: list[Clause], anchors) -> dict:
    """硬锚点覆盖率：多少条款能被机械抽取，覆盖了哪些类型。"""
    compilable = [c for c in clauses if c.compilable]
    covered = {_ag(ha, 'source_clause') for ha in anchors}
    kinds = {}
    for c in compilable:
        kinds.setdefault(c.kind.value, [0, 0])
        kinds[c.kind.value][1] += 1
        if c.clause_id in covered:
            kinds[c.kind.value][0] += 1
    n_comp = len(compilable)
    n_cov = len(covered & {c.clause_id for c in compilable})
    return {
        "clauses_total": len(clauses),
        "clauses_compilable": n_comp,
        "clauses_uncompilable": len(clauses) - n_comp,
        "clauses_with_hard_anchors": n_cov,
        "hard_anchor_coverage": round(n_cov / n_comp, 4) if n_comp else 0.0,
        "n_hard_anchors": len(anchors),
        "per_kind": {k: f"{v[0]}/{v[1]}" for k, v in sorted(kinds.items())},
        "uncompilable_clauses": [c.clause_id for c in clauses if not c.compilable],
    }
