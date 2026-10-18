"""锚点抽取器 v2 —— 让"能机械验证的范围"跟上"能判的范围"。

为什么必须做这一步
------------------
判据语言 v2 把三类盲区消除了（F1 0.80 → 1.00），但实测发现：
**新增的 4 条判据，一条都产不出硬锚点。**

按项目自己的准入门槛 R1（**必须见过 ≥1 条合规锚点**），这 4 条**根本没有入库资格**——
它们消除盲区靠的是"我说它对"，而不是"外部真值证明它对"。

> **语言扩张扩大了"能判"的范围，但没有同步扩大"能机械验证"的范围。**
> 这是一个真实的、被实测暴露出来的不对称。本模块就是把另一半补上。

三类新判据的锚点**都是机械可导出的**（真值全部来自条款参数 + ctx）：
  NOT_FUTURE      真值 = ctx 注入的基准时间          → 基准当天合规 / 次日违规
  RANGE_BY_TABLE  真值 = ctx 受控表的每一行          → 每行取上限合规 / 上限+1 违规
  UNIQUE_ACROSS   真值 = 集合内唯一性                → 两个不同值合规 / 同值违规
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Optional

from standard.compiler import Clause, ClauseKind
from standard.grammar_v2 import ClauseKindV2, UniqueAcrossChecker, make_checker



def _legal_key_value(key, key_field: str, clauses: list) -> Any:
    """把受控表里的原始键（如"公开级"）映射成**该字段合法的取值**（如"公开"）。

    ★ 换领域暴露的问题：制造业表的键"A类"恰好就是 `equipment_class` 的合法取值，
      于是"直接拿键当字段值"这种写法一直没出错。换到政务，
      表的键是"公开级/内部级/秘密级"，而字段的合法取值是"公开/内部/秘密"——
      夹具直接塞键就会违反该字段的 ENUM 条款，制造出"标准自相矛盾"的假象（实测 3 处）。

    做法：在该字段的 ENUM 词表里找**归一化后相等**的那个合法值；找不到就原样返回。
    """
    from standard.compiler import ClauseKind
    from standard.grammar_v2 import _norm_key
    for c in clauses:
        if not c.compilable or c.kind != ClauseKind.ENUM:
            continue
        if (c.params or {}).get("field") != key_field:
            continue
        for allowed in (c.params or {}).get("allowed", []):
            if _norm_key(allowed) == _norm_key(key):
                return allowed
    return key


def _pattern_conforming(field: str, clues: list, suffix: str = "0001") -> Optional[str]:
    """为某个字段生成一个**符合其 PATTERN 条款**的值。

    换领域暴露的问题：集合级锚点曾写死 `AST-MFG-9999-0001`，
    换到政务（`GOV-\d{6}`）就违反了本领域的标识格式条款。
    这里改成按该领域自己的 PATTERN 机械生成。找不到 PATTERN 就返回 None（不猜）。
    """
    from standard.compiler import ClauseKind
    from evostd.anchors import _gen_from_pattern
    for c in clues:
        if not c.compilable or c.kind != ClauseKind.PATTERN:
            continue
        if (c.params or {}).get("field") != field:
            continue
        v = _gen_from_pattern((c.params or {}).get("pattern", ""))
        return v
    return None


@dataclass
class HardAnchorV2:
    """一条硬锚点。集合级锚点用 `assets`（多条），点级锚点用 `assets[0]`。

    `attributed_to`：集合级锚点里**被归因的那一条**的 asset_id。
    这是集合级判据特有的字段——见 grammar_v2.UniqueAcrossChecker 的归因说明。
    """
    anchor_id: str
    assets: list[dict]
    label: str                      # compliant | violating
    source_clause: str
    how: str                        # 这条真值是怎么机械算出来的
    attributed_to: Optional[str] = None
    level: str = "point"            # point | set

    @property
    def asset(self) -> dict:
        return self.assets[0] if self.assets else {}


def _next_day(iso: str) -> str:
    """机械地取"次日"（不引入外部依赖）。"""
    import datetime as _dt
    d = _dt.date.fromisoformat(iso[:10])
    return (d + _dt.timedelta(days=1)).isoformat()


def derive_hard_anchors_v2(clauses: list[Clause], base_asset: dict,
                           ctx: dict | None = None) -> list[HardAnchorV2]:
    """为 v1 + v2 的全部条款机械产出锚点。v1 条款委托给原抽取器。"""
    from evostd.anchors import derive_hard_anchors

    ctx = ctx or {}
    out: list[HardAnchorV2] = []
    n = [0]

    def nid() -> str:
        n[0] += 1
        return f"HA2-{n[0]:03d}"

    for c in clauses:
        if not c.compilable:
            continue
        k = c.kind.value if hasattr(c.kind, "value") else c.kind
        p = c.params
        f = p.get("field")

        # ---- ★ 跨字段一致性：基础模板缺字段时，**注入最小测试夹具**而不是跳过 ----
        #
        #  原 v1 抽取器遇到 `l not in base_asset` 就 continue，理由是"不臆造字段"。
        #  那个原则对，但**在这里用错了地方**：一致性判据要验的是**两个字段之间的关系**，
        #  不是字段本身的内容。为关系构造一对满足/违反的取值，是**从条款参数机械导出**的
        #  测试夹具，不是"编造一条设备台账"。
        #
        #  区分标准：**编造领域事实**（说某台设备周期是 90 天）不许；
        #           **构造关系夹具**（让 a <= b 成立/不成立）允许，且必须在 how 里写明。
        if k == ClauseKind.CONSISTENCY.value:
            l, r, op = p.get("left"), p.get("right"), p.get("op")
            if l and r and (l not in base_asset or r not in base_asset):
                inj = [x for x in (l, r) if x not in base_asset]
                a = copy.deepcopy(base_asset)
                a[l], a[r] = (("2026-12-31", "2026-01-01") if op == "<="
                              else ("2026-01-01", "2026-12-31"))
                out.append(HardAnchorV2(nid(), [a], "violating", c.clause_id,
                                        f"{l} {op} {r} 的反例（注入测试夹具 {inj}）"))
                a = copy.deepcopy(base_asset); a[l] = a[r] = "2026-06-01"
                out.append(HardAnchorV2(nid(), [a], "compliant", c.clause_id,
                                        f"{l} 与 {r} 相等，满足 {op}（注入测试夹具 {inj}）"))
                continue
            for a in derive_hard_anchors([c], base_asset, ctx):
                out.append(HardAnchorV2(anchor_id=nid(), assets=[a.asset],
                                        label=a.label, source_clause=a.source_clause,
                                        how=a.how, level="point"))
            continue

        # ---- v1 条款：委托原抽取器 ----
        if k not in ClauseKindV2.VALUES:
            for a in derive_hard_anchors([c], base_asset, ctx):
                out.append(HardAnchorV2(anchor_id=nid(), assets=[a.asset],
                                        label=a.label, source_clause=a.source_clause,
                                        how=a.how, level="point"))
            continue

        # ---- v2 ① 基准时间：真值 = ctx 注入的基准 ----
        if k == ClauseKindV2.NOT_FUTURE.value:
            now = ctx.get(p.get("now_key", "now"))
            if not now:
                continue                       # 没有基准就导不出锚点（诚实弃权）
            a = copy.deepcopy(base_asset); a[f] = str(now)
            out.append(HardAnchorV2(nid(), [a], "compliant", c.clause_id,
                                    f"{f} 取基准时间当天「{now}」（来源 ctx.{p.get('now_key','now')}）"))
            a = copy.deepcopy(base_asset); a[f] = _next_day(str(now))
            out.append(HardAnchorV2(nid(), [a], "violating", c.clause_id,
                                    f"{f} 取基准时间次日（机械 +1 天）"))

        # ---- v2 ② 查表区间：真值 = ctx 受控表的每一行 ----
        elif k == ClauseKindV2.RANGE_BY_TABLE.value:
            table = ctx.get(p.get("table_key"))
            if not isinstance(table, dict):
                continue
            kf = p["key_field"]
            for key, row in table.items():
                hi, lo = row.get("max"), row.get("min")
                if hi is not None:
                    kv_ok = _legal_key_value(key, kf, clauses)
                    a = copy.deepcopy(base_asset); a[kf] = kv_ok; a[f] = hi
                    out.append(HardAnchorV2(nid(), [a], "compliant", c.clause_id,
                                            f"{kf}=「{key}」时 {f} 取该行上限 {hi}"
                                            f"（来源 ctx.{p['table_key']}）"))
                    a = copy.deepcopy(base_asset); a[kf] = _legal_key_value(key, kf, clauses)
                    a[f] = hi + 1
                    out.append(HardAnchorV2(nid(), [a], "violating", c.clause_id,
                                            f"{kf}=「{key}」时 {f} 超该行上限 {hi}"))
                if lo is not None:
                    a = copy.deepcopy(base_asset); a[kf] = _legal_key_value(key, kf, clauses)
                    a[f] = lo - 1
                    out.append(HardAnchorV2(nid(), [a], "violating", c.clause_id,
                                            f"{kf}=「{key}」时 {f} 低于该行下限 {lo}"))

        # ---- v2 ④ 条件禁止：真值 = 条件成立时该字段必须为空 ----
        elif k == ClauseKindV2.FORBID_WHEN.value:
            wf = p.get("when_field"); wv = p.get("when_value"); ff = p.get("forbid_field")
            if wf and ff:
                a = copy.deepcopy(base_asset); a[wf] = wv; a[ff] = None
                out.append(HardAnchorV2(nid(), [a], "compliant", c.clause_id,
                                        f"{wf}=「{wv}」时 {ff} 为空"))
                a = copy.deepcopy(base_asset); a[wf] = wv
                a[ff] = "2026-01-01" if ("date" in ff or "time" in ff) else "已填写"
                out.append(HardAnchorV2(nid(), [a], "violating", c.clause_id,
                                        f"{wf}=「{wv}」时 {ff} 非空"))
                continue
            continue

        # ---- v2 ③ 集合级唯一性：真值 = 集合内唯一 ----
        elif k == ClauseKindV2.UNIQUE_ACROSS.value:
            idf = p.get("id_field", "asset_id")
            # 合规锚点：两条资产取**不同**的值
            # ★ 合成值必须**同时满足库里其它格式条款**，否则集合级锚点会去撞
            #   `AST-MFG-\d{4}-\d{4}`（资产标识格式）和 `[0-9a-f]{64}`（哈希格式），
            #   制造出"标准自相矛盾"的假象（实测 HA2-102 就是这么来的）。
            #   这里显式构造合格式值：不臆造领域事实，只是让夹具长成合法的形状。
            # ★ 不再写死制造业格式：按**本领域**的标识 PATTERN 机械生成
            _aid = _pattern_conforming(str(idf), clauses) or "ID-0001"
            _aid2 = (_aid[:-1] + "2") if _aid and _aid[-1].isdigit() else "ID-0002"
            _h1 = "f" * 64; _h2 = "e" * 64
            a1 = copy.deepcopy(base_asset); a1[idf] = _aid; a1[f] = _h1
            a2 = copy.deepcopy(base_asset); a2[idf] = _aid2; a2[f] = _h2
            out.append(HardAnchorV2(nid(), [a1, a2], "compliant", c.clause_id,
                                    f"两条资产的 {f} 取值不同（集合内唯一）", level="set"))
            # 违规锚点：两条资产取**相同**的值，归因给后登记的那条
            _dup = "d" * 64
            b1 = copy.deepcopy(base_asset); b1[idf] = _aid; b1[f] = _dup
            b1["revision"] = 1
            b2 = copy.deepcopy(base_asset); b2[idf] = _aid2; b2[f] = _dup
            b2["revision"] = 2
            out.append(HardAnchorV2(nid(), [b1, b2], "violating", c.clause_id,
                                    f"两条资产的 {f} 取值相同，归因给后登记者（revision 较大）",
                                    attributed_to=_aid2, level="set"))

    # ★ 出口闭包：把条件必填条款要求的依赖字段补齐，
    #   消掉"锚点不完整"造成的**假冲突**（实测 4 条）。
    #   ★ 闭包必须**回验**：补齐的字段可能反过来破坏锚点对**它自己那条**条款的结论。
    #   实测踩到：闭包消掉了 4 条"锚点不完整"的假冲突，却让锚点自检从 0 不一致变成 4 不一致。
    #   —— 那是自检机制在抓我们自己修复的副作用，不是自检误报。
    #   所以：**只在"补齐后仍能自洽"时才保留补齐**，否则退回原夹具。
    #   宁可留一条已知的假冲突（会被分类为类B并注明），也不要一条自己都对不上的锚点。
    by_clause = {c.clause_id: c for c in clauses}
    for a in out:
        c = by_clause.get(a.source_clause)
        if c is None:
            continue
        try:
            ck = make_checker(c)
        except Exception:
            continue

        def _still_consistent(assets) -> bool:
            try:
                if getattr(ck, "is_set_level", False):
                    found = ck.check_all(assets, {**ctx, "_assets": assets})
                    if a.attributed_to:
                        return a.attributed_to in {f.evidence.get("attributed_to") for f in found}
                    return bool(found)
                f = ck.check(assets[0], {**ctx, "_assets": assets})
                return (a.label == "violating") == bool(f is not None and not f.ok)
            except Exception:
                return False

        for i, asset in enumerate(a.assets):
            fixed, filled = close_fixture(asset, clauses, ctx)
            if not filled:
                continue
            trial = list(a.assets); trial[i] = fixed
            if _still_consistent(trial):
                a.assets[i] = fixed
                a.how = a.how + " ｜闭包补齐：" + "、".join(filled)

    #   ★ 冲突消解：闭包之后仍可能与**别的**条款冲突。
    #     用判定过程搜一个无冲突夹具——搜得到就换（属锚点问题），
    #     搜不到就**保持原样并在 how 里标明**（属标准真矛盾，须由人裁决）。
    #     这样锚点库里的"冲突"就只剩真矛盾，`std_conflicts` 的数字才有意义。
    from standard.fixture_search import search_conflict_free
    try:
        cks = [make_checker(c) for c in clauses if c.compilable]
    except Exception:
        cks = []
    resolved = unresolved = 0
    if cks:
        for a in out:
            if a.label != "compliant":
                continue
            r = search_conflict_free(a.asset, a.source_clause, cks, ctx, clauses=clauses)
            if r.resolvable and r.assignment:
                a.assets[0] = r.fixed_asset
                a.how = a.how + " ｜冲突消解：" + str(r.assignment)
                resolved += 1
            elif not r.resolvable:
                unresolved += 1
                a.how = a.how + " ｜★真矛盾（搜索无解，需人裁决）：" +                         ",".join(x["clause_id"] for x in r.remaining)
    if resolved or unresolved:
        print(f"[anchors_v2] 冲突消解：修正 {resolved} 条锚点，"
              f"另有 {unresolved} 条属标准真矛盾（保留并标注）", file=__import__("sys").stderr)
    return out


def verify_anchors_v2(anchors: list[HardAnchorV2],
                      clauses: list[Clause] | None = None,
                      base_ctx: dict | None = None) -> dict:
    """用锚点回验判据：合规锚点不得被判违规，违规锚点必须被判违规。

    这是 R1/R2/R3 的机械检验，也是"锚点自身可信"的机器证明。

    ★ `base_ctx` 必须传真实的受控清单。
      第一版这里写成 `ctx = {"_assets": a.assets}`，把真 ctx 覆盖掉了，
      于是 REFERENCE/FORBIDDEN 类判据找不到受控清单 → 14 条锚点全被误判为不一致。
      **教训：验证器的环境必须与被验证判据的运行环境一致，否则"不一致"是验证器自己的错。**
    """
    by_clause: dict[str, Clause] = {}
    if clauses:
        by_clause = {c.clause_id: c for c in clauses}
    ok, mismatch, skipped = 0, [], 0
    for a in anchors:
        if not by_clause:
            break
        c = by_clause.get(a.source_clause)
        if c is None:
            skipped += 1
            continue
        ck = make_checker(c)
        ctx = dict(base_ctx or {})          # ★ 真 ctx 打底
        ctx["_assets"] = a.assets            # 再挂集合
        if getattr(ck, "is_set_level", False):
            found = ck.check_all(a.assets, ctx)
            flagged = {f.evidence.get("attributed_to") for f in found}
            violated = (a.attributed_to in flagged) if a.attributed_to else bool(found)
        else:
            f = ck.check(a.asset, ctx)
            violated = bool(f and not f.ok)
        want = (a.label == "violating")
        if violated == want:
            ok += 1
        else:
            mismatch.append({"anchor": a.anchor_id, "clause": a.source_clause,
                             "label": a.label, "how": a.how,
                             "got_violated": violated, "want_violated": want})
    return {"ok": ok, "mismatch": mismatch, "n_mismatch": len(mismatch), "skipped": skipped,
            "total": len(anchors)}


def coverage_v2(clauses: list[Clause], anchors: list[HardAnchorV2]) -> dict:
    """按条款类型统计覆盖率；并标出**类型级**的有无锚点。"""
    have = {a.source_clause for a in anchors}
    per_kind: dict[str, list[int]] = {}
    for c in clauses:
        if not c.compilable:
            continue
        k = c.kind.value if hasattr(c.kind, "value") else c.kind
        slot = per_kind.setdefault(k, [0, 0])
        slot[1] += 1
        if c.clause_id in have:
            slot[0] += 1
    ok_n = sum(1 for c in clauses if c.compilable and c.clause_id in have)
    tot = sum(1 for c in clauses if c.compilable)
    return {"n_compilable": tot, "n_with_anchor": ok_n,
            "rate": round(ok_n / tot, 4) if tot else 0.0,
            "per_kind": {k: f"{v[0]}/{v[1]}" for k, v in per_kind.items()},
            "n_anchors": len(anchors)}

def close_fixture(fixture: dict, clauses: list[Clause], ctx: dict,
                  max_rounds: int = 4) -> tuple[dict, list[str]]:
    """★ 锚点夹具闭包：把**条件必填**条款要求的依赖字段补齐。

    为什么需要：锚点是为**验证某一条**条款而构造的最小夹具，只改一个字段。
    但标准里还有条件必填条款（如"设备状态为停用时，应同时登记停用日期"）。
    夹具只把状态改成"停用"、没填停用日期，就会在**另一条**条款上违规——
    于是冲突检测把它报成"标准自相矛盾"，而它其实是"锚点不完整"。

    实测：51 条合规锚点里有 4 条属于这一类。

    做法：反复跑库里的条件必填判据，凡是"条件成立但依赖字段缺失"的，
    机械补上一个**中性占位值**，直到不动点（有轮数上限，避免死循环）。
    补了什么记在返回的第二项里，写进锚点的 `how`，可审计。
    """
    from standard.compiler import ClauseKind
    out = copy.deepcopy(fixture)
    filled: list[str] = []
    conds = [c for c in clauses if c.compilable and c.kind == ClauseKind.CONDITIONAL]
    forbids = [c for c in clauses if c.compilable
               and c.kind == ClauseKind.FORBID_WHEN]
    for _ in range(max_rounds):
        changed = False
        for c in conds:
            # ★ 参数名是 when_field / when_value / require_field（实测对齐 compiler 的 ConditionalChecker）
            fld = c.params.get("when_field")
            val = c.params.get("when_value")
            req = c.params.get("require_field")
            if not (fld and req):
                continue
            if str(out.get(fld)) != str(val):
                continue                       # 条件不成立，无需补
            if out.get(req) not in (None, "", []):
                continue                       # 依赖字段已满足
            # 机械补一个中性值：日期型补日期，其它补"已填写"
            out[req] = "2026-01-01" if ("date" in req or "time" in req) else "已填写"
            filled.append(f"{req}（因 {fld}={val}）")
            changed = True
        # ★ 条件禁止（第4类）：条件成立时，被禁止的字段**必须清空**。
        #   它和条件必填是一对反向约束，闭包必须同时跑，否则夹具会左右互撞。
        for c in forbids:
            wf = c.params.get("when_field"); wv = c.params.get("when_value")
            ff = c.params.get("forbid_field")
            if not (wf and ff):
                continue
            if str(out.get(wf)) != str(wv):
                continue
            if out.get(ff) in (None, "", []):
                continue
            out[ff] = None
            filled.append(f"清空 {ff}（因 {wf}={wv} 时该字段必须为空）")
            changed = True
        if not changed:
            break
    return out, filled
