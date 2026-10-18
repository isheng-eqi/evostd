"""判据语言 v2 —— 二阶自进化的落地：让语言本身长大。

背景（这不是设计出来的，是被实测逼出来的）
------------------------------------------
制造包实测出 **4 条真盲区**：现有 34 条判据一条都抓不到，但每一条都是真违规。
它们的成因**只有 3 种**，而且全部是"**判据语言表达力不够**"，不是"某条规则忘了写"：

  B1  点检日期落在 2030 年（未来）    → 判据需要知道"现在"，而 v1 判据被刻意做成
                                        不依赖时钟的纯函数
  B2  A类检修周期 365 天（语料≤90）    → 上限**取决于另一个字段的取值**，v1 的 RANGE
                                        只有编译期常量 min/max
  B4  A类点检间隔 168 小时（语料≤24）  → 与 B2 同因不同表 → 证明该扩张的是**语言**
  B3  同一文件哈希登记在两个资产号      → `check(asset, ctx)` **看不到别的资产**，
                                        这是**签名层面**的结构性限制

v2 的三类新判据，正好一一对应：

  NOT_FUTURE       基准时间**显式注入**（来自 ctx，不是时钟）→ 确定性不破
  RANGE_BY_TABLE   区间**查表得到**（表的来源写进参数，可溯源）
  UNIQUE_ACROSS    判据对象从**点**升级到**集合**（新增 SetChecker 基类）

设计纪律（延续 v1）
-------------------
1. 每条判据仍带 `source`（标准名 + 条款号），违例可回指原文。
2. 判据仍是**纯函数**：新判据的额外输入全部来自 `ctx`，不读时钟、不读网络。
3. **新判据必须过锚点三规则**（R1 见过合规样本 / R2 零误报 / R3 可归因命中），
   否则不许入库——这条由 `StandardService.std_propose` 把关，不在这里放宽。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from standard.compiler import Checker, Clause, ClauseKind, Finding


# ============================================================================
#  新增的条款类型（= 语言的关键字）
# ============================================================================
class ClauseKindV2:
    """v2 新增的条款类型 —— **直接引用 ClauseKind 的枚举成员**（不是字符串）。

    ★ 曾经写成裸字符串，结果 `clause_to_dict` 里的 `c.kind.value` 直接崩：
      `AttributeError: 'str' object has no attribute 'value'`。
      教训：语言扩张必须体现在**类型系统**里，否则序列化/反序列化会在某个角落炸掉。
    """
    FORBID_WHEN = ClauseKind.FORBID_WHEN          # 条件禁止（第4类，由编译错误逼出）
    NOT_FUTURE = ClauseKind.NOT_FUTURE            # 取值不得晚于显式注入的基准时间
    RANGE_BY_TABLE = ClauseKind.RANGE_BY_TABLE    # 区间取决于另一字段，查 ctx 受控表
    UNIQUE_ACROSS = ClauseKind.UNIQUE_ACROSS      # 集合内必须唯一

    ALL = (NOT_FUTURE, RANGE_BY_TABLE, UNIQUE_ACROSS, FORBID_WHEN)
    VALUES = tuple(k.value for k in ALL)          # 字符串形态，供按值比较的调用方使用


# ============================================================================
#  点级新判据
# ============================================================================
def _cmp(actual: str, bound: str) -> int:
    """ISO 日期/时间字符串比较；无法比较返回 0（不误报）。"""
    try:
        return (actual > bound) - (actual < bound)
    except Exception:
        return 0


class NotFutureChecker(Checker):
    """B1：取值不得晚于基准时间。

    ★ 基准时间来自 `ctx["now"]`，**不是** `datetime.now()`。
      这是关键设计：判据仍然确定（同输入同输出），只是它现在能"知道现在"了——
      因为"现在"变成了**外部注入的真值**，而不是判据内部的副作用。
      代价是要有人/流程负责注入正确的基准；收益是这类盲区被永久消除。
    """

    def _run(self, asset, ctx):
        import datetime as _dt
        import re as _re

        f = self.clause.params["field"]

        # ★★ 先做**日历有效性**校验，与"是否未来"完全独立。
        #
        #   实测缺陷（极端测试 X3）：格式判据只查 `\d{4}-\d{2}-\d{2}` 这个**形状**，
        #   不查日历。于是 `2026-02-29`（2026 不是闰年）、`2026-04-31`（4 月只有 30 天）
        #   这类**根本不存在的日期**：
        #     · 早于基准时间 → 不会被"不得为未来"抓到
        #     · 也没有任何格式判据抓它（日期字段压根没有 pattern 条款）
        #   → **系统判定"合规"，而这条数据明显是错的。**
        #
        #   含 `calendar_fields` 的额外字段也一并校验（本条条款可覆盖多个日期字段）。
        _seen = set()
        for cf in [f] + list(self.clause.params.get("calendar_fields") or []):
            if cf in _seen:
                continue
            _seen.add(cf)
            val = asset.get(cf)
            if val is None or val == "":
                continue
            sval = str(val).strip()
            if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", sval):
                continue                      # 不是日期形状，交给格式/必填判据
            try:
                _dt.datetime.strptime(sval, "%Y-%m-%d")
            except ValueError as exc:
                return (False,
                        f"{cf}=「{sval}」不是合法的日历日期（{exc}）",
                        {"field": cf, "value": sval, "reason": "日历无效"})

        if f not in asset or asset[f] is None:
            return True, "", {}
        now = ctx.get(self.clause.params.get("now_key", "now"))
        if not now:
            # 没有注入基准 → 判据**弃权**，而不是猜一个时间。
            # 弃权会体现在 std_audit 的"未行使"清单里，不会静默通过。
            return True, "", {"abstained": True, "reason": "ctx 未注入基准时间"}
        v = str(asset[f])
        if _cmp(v, str(now)) > 0:
            return (False,
                    f"{f}=「{v}」晚于基准时间「{now}」（不得为未来值）",
                    {"field": f, "value": v, "bound": str(now),
                     "bound_source": "ctx." + self.clause.params.get("now_key", "now")})
        return True, "", {}



# ── 键名归一化：语料的档位写法可能带分类后缀（"公开级" vs 资产字段的"公开"）──────
_KEY_SUFFIXES = ("级", "类", "档", "等")


def _norm_key(x) -> str:
    s = str(x).strip()
    for suf in _KEY_SUFFIXES:
        if len(s) > len(suf) and s.endswith(suf):
            return s[: -len(suf)]
    return s


def _lookup_row(table: dict, kv):
    """先精确匹配；不中再按归一化键匹配。返回 (row, matched_key, how)。"""
    if kv in table:
        return table[kv], kv, "exact"
    nk = _norm_key(kv)
    if isinstance(table, dict):
        for k, v in table.items():
            if _norm_key(k) == nk:
                return v, k, f"normalized({kv}->{k})"
    return None, None, "miss"


class RangeByTableChecker(Checker):
    """B2/B4：上限/下限取决于另一个字段的取值。

    参数：
      field       被约束字段
      key_field   决定区间的字段（如 equipment_class）
      table_key   ctx 里的受控表名（如 maintenance_cycle_limit）
    表的形状：{"A类": {"max": 90}, "B类": {"max": 180}, ...}
    """

    def _run(self, asset, ctx):
        f = self.clause.params["field"]
        if f not in asset or asset[f] is None:
            return True, "", {}
        kf = self.clause.params["key_field"]
        kv = asset.get(kf)
        if kv is None:
            return True, "", {}                       # key 缺失由别的条款负责
        table = ctx.get(self.clause.params["table_key"])
        if not isinstance(table, dict):
            return True, "", {"abstained": True,
                              "reason": f"ctx 缺受控表 {self.clause.params['table_key']}"}
        # ★ 键名归一化：语料里写"公开级"、资产字段里写"公开"，
        #   直接查表会 miss → 判据弃权 → 盲区照旧。这是**换领域才暴露出来的**问题。
        #   先精确匹配，不中再按归一化键匹配，并记录匹配方式（可溯源）。
        row, matched, how = _lookup_row(table, kv)
        if row is None:
            return True, "", {"abstained": True,
                              "reason": f"受控表无 {kf}=「{kv}」这一行（精确与归一化均未命中）"}
        v = asset[f]
        lo, hi = row.get("min"), row.get("max")
        if not isinstance(v, (int, float)):
            return False, f"{f} 应为数值，实际 {type(v).__name__}", {"field": f, "value": v}
        if lo is not None and v < lo:
            return (False, f"{kf}=「{kv}」时 {f}={v} 低于下限 {lo}",
                    {"field": f, "value": v, "key": {kf: kv}, "min": lo,
                     "table": self.clause.params["table_key"], "key_match": how})
        if hi is not None and v > hi:
            return (False, f"{kf}=「{kv}」时 {f}={v} 超过上限 {hi}",
                    {"field": f, "value": v, "key": {kf: kv}, "max": hi,
                     "table": self.clause.params["table_key"], "key_match": how})
        return True, "", {}


# ============================================================================
#  集合级判据（判据对象从「点」升级到「集合」）
# ============================================================================
class SetChecker:
    """集合级判据基类。

    v1 的签名是 `check(asset, ctx)`——**结构上不可能**表达"两条资产之间"的事实。
    B3（同一文件哈希登记在两个资产号）就是被这个签名挡住的。

    v2 不修改 v1 的签名（那会破坏全部既有判据），而是**新增一条并行路径**：
    集合级判据实现 `check_all(assets, ctx) -> list[Finding]`。
    `StandardVerifierV2` 同时走两条路径。

    ★ 这不是"绕过限制"，而是**承认限制并扩张语言**：
      判据现在可以声明"我依赖整个集合"，这个声明本身就是语言的一部分。
    """

    is_set_level = True

    def __init__(self, clause: Clause):
        self.clause = clause
        # ★★ 集合级判据**不继承 `Checker`**（那是 v1 的单点判据基类），
        #   所以 `Compiler.Checking.__init__` 里的定义校验**不会自动生效**。
        #   实测踩到：给 UniqueAcrossChecker 加了 `_validate()`，
        #   但 `id_field` 缺失/拼错**照样被接受** —— 因为没人调用它。
        #
        #   → 这里补上同样的构造期校验，让"忘了传 id_field"变成
        #     **构造期报错**，而不是换领域后集合级判据静默失效。
        problems = self._validate()
        if problems:
            raise ValueError("判据定义无效：" + "；".join(problems))

    def _validate(self) -> list[str]:
        """子类覆盖：返回定义问题列表（空 = 合法）。默认不校验。"""
        return []

    def check_all(self, assets: list[dict], ctx: dict) -> list[Finding]:
        raise NotImplementedError

    def check(self, asset: dict, ctx: dict):
        """★ 让集合级判据也能通过 v1 的 `check(asset, ctx)` 接口工作。

        为什么需要：`StandardVerifier`（v1）对每条条款调 `c.check(asset, ctx)`。
        不提供这个方法，集合级判据会被 AttributeError 打挂——要么全崩、要么被静默跳过。

        实现：从 `ctx["_assets"]` 取集合跑 `check_all`，
        再判断**当前这条资产是不是被归因的那一条**（不是则返回 None = 无违例）。
        """
        assets = ctx.get("_assets") or []
        if not assets:
            # 没给集合 → 弃权。但**必须返回一个 ok=True 的结论**而不是 None：
            # v1 的 StandardVerifier 期望每条判据都返回 Finding，
            # 返回 None 会以 `'NoneType' object has no attribute 'ok'` 打挂整条链路。
            return self._finding(True, "", {"abstained": True, "reason": "ctx 未提供资产集合"})
        # ★★ 批内不足 2 条时，**集合级唯一性是"判不了"，不是"通过"**。
        #
        #   实测缺陷（多角度测试 A5）：单条校验 `AST-MFG-2026-1016`
        #   （它与 `AST-MFG-2026-0001` 登记了同一份 file_hash）时，
        #   批内只有它自己，对照物不在批里 → 旧代码走到下面的
        #   "查过了、这条资产没卷入 → 通过"，于是：
        #     · `compliant: True`
        #     · 弃权清单里**没有**第47条
        #   → **逐条校验时，跨资产重复检测永不生效，且没有任何提示。**
        #
        #   这与本项目反复强调的那条纪律正面冲突：
        #   **"没有重复" 和 "无法判断有没有重复" 必须是两种可见的结论。**
        if len(assets) < 2:
            return self._finding(True, "", {
                "abstained": True,
                "reason": (f"批内只有 {len(assets)} 条资产 —— 集合级判定需要至少 2 条对照物；"
                           "当前无法判定，不是通过"),
            })
        # ★ 标识字段必须**从条款参数读**，不能写死 "asset_id" ——
        #   制造业用 asset_id，政务用 catalog_id。写死会让换领域后集合级判据静默失效
        #   （实测：政务包的"跨资产重复"盲区因此完全没被抓到）。
        idf = self.clause.params.get("id_field", "asset_id")
        me = asset.get(idf)
        for f in self.check_all(assets, ctx):
            ev = f.evidence or {}
            target = ev.get("attributed_to")
            if (target is not None and me == target) or \
               (target is None and me in (ev.get("assets") or [])):
                return f
        return self._finding(True, "", {})    # 查过了、这条资产没卷入 → 通过

    def _finding(self, ok: bool, msg: str, ev: dict) -> Finding:
        return Finding(clause_id=self.clause.clause_id, source=self.clause.source,
                       ok=ok, message=msg, evidence=ev, clause_text=self.clause.text)


class UniqueAcrossChecker(SetChecker):
    """B3：某字段在整个资产集合内必须唯一。

    ★ 集合级判据特有的设计维度：**归因**。
      单点判据天然知道"违例属于谁"；集合级判据必须额外回答"该怪谁"。

      实测教训：第一版对重复组里的**每一条**都判违规，结果把**原件**（合规资产
      AST-MFG-2026-0001，revision=1）也判成了违规 → 引入 1 条误报，直接违反
      R2（零误报）准入门槛。

      正确语义：「重复登记」这件事的**责任方是后登记的那一个**，不是原件。
      所以默认 `attribution="later"`：按 `order_by` 排序，只判**除首条之外**的。

    参数：
      field        要检查唯一性的字段（如 file_hash）
      id_field     资产标识字段（默认 asset_id）
      ignore_empty 空值是否跳过（默认 True）
      attribution  "later"（默认，只判后登记的）| "all"（组内全判）
      order_by     判定先后的字段序列，默认 ["revision", "ingest_time"]；
                   缺失时回落到**列表出现顺序**（先出现者视为原件）
    """

    # ★★ `id_field` 原来有静默默认 `"asset_id"`，而本文件第 203 行早就写下过教训：
    #   "写死 asset_id 会让换领域后集合级判据静默失效"（政务用 catalog_id）。
    #   现在两套标准**都显式传了** id_field，所以当前没有活 bug；
    #   但默认值把这个坑留着 —— 以后某条条款忘了传，就会静默回到 asset_id。
    #   这里把它变成**必填**：忘传 = 构造期报错，而不是换领域后悄悄失效。
    _ALLOWED = {"field", "id_field", "ignore_empty", "attribution", "order_by"}

    def _validate(self):
        p = self.clause.params
        out = []
        extra = set(p) - self._ALLOWED
        if extra:
            out.append(f"出现未知参数 {sorted(extra)}（可能是拼写错）")
        for k in ("field", "id_field"):
            if not p.get(k):
                out.append(f"缺少必填参数 {k}（集合级判据不允许猜标识字段）")
        return out

    def check_all(self, assets, ctx):
        f = self.clause.params["field"]
        idf = self.clause.params.get("id_field", "asset_id")
        ignore_empty = self.clause.params.get("ignore_empty", True)
        attribution = self.clause.params.get("attribution", "later")
        order_by = self.clause.params.get("order_by", ["revision", "ingest_time"])

        order: dict[str, int] = {a.get(idf): i for i, a in enumerate(assets)}
        groups: dict[Any, list[dict]] = {}
        for a in assets:
            v = a.get(f)
            if v is None or (ignore_empty and v == ""):
                continue
            groups.setdefault(v, []).append(a)

        out = []
        for v, members in groups.items():
            if len(members) <= 1:
                continue

            def sort_key(a):
                keys = []
                for k in order_by:
                    x = a.get(k)
                    keys.append((0, x) if x is not None else (1, ""))
                keys.append((0, order.get(a.get(idf), 0)))   # 兜底：列表顺序
                return keys

            members_sorted = sorted(members, key=sort_key)
            origin = members_sorted[0]
            dupes = members_sorted if attribution == "all" else members_sorted[1:]
            ids_all = [m.get(idf) for m in members_sorted]
            for m in dupes:
                out.append(self._finding(
                    False,
                    (f"{f}=「{str(v)[:16]}…」与 {origin.get(idf)} 重复登记"
                     f"（同组 {len(members)} 条：{ids_all}）"),
                    {"field": f, "value": v, "assets": ids_all, "n": len(members),
                     "origin": origin.get(idf), "attributed_to": m.get(idf),
                     "scope": "跨资产（集合级）", "attribution": attribution}))
        return out



class ForbidWhenChecker(Checker):
    """条件禁止：当 `when_field == when_value` 时，`forbid_field` 必须为空。

    ★ CONDITIONAL 的**反向**。两者合起来才能表达 "iff"：
        CONDITIONAL  状态=停用 → 必须有停用日期
        FORBID_WHEN  状态=在用 → 必须没有停用日期
    """

    # ★ 原先只校验了 `when_field` 与 `forbid_field`：
    #       if not (wf and ff): → 弃权
    #   但 **`when_value` 没在校验里** —— 拼成 `wen_value` 时 `wv=None`，
    #   于是 `str(asset.get(wf)) != str(None)` 恒成立 → 条件永不匹配 →
    #   **判据永远不拦，而且不会弃权、不会报错。**
    #   这里把三个参数都变成必填，把"静默永不触发"变成构造期的显式失败。
    _ALLOWED = {"when_field", "when_value", "forbid_field"}

    def _validate(self):
        p = self.clause.params
        out = []
        extra = set(p) - self._ALLOWED
        if extra:
            out.append(f"出现未知参数 {sorted(extra)}（可能是拼写错，如 when_value 写成 wen_value）")
        for k in ("when_field", "when_value", "forbid_field"):
            if k not in p or p.get(k) is None:
                out.append(f"缺少必填参数 {k}")
        return out

    def _run(self, asset, ctx):
        wf, wv = self.clause.params.get("when_field"), self.clause.params.get("when_value")
        ff = self.clause.params.get("forbid_field")
        if not (wf and ff):
            return True, "", {"abstained": True, "reason": "条款参数不完整"}
        if str(asset.get(wf)) != str(wv):
            return True, "", {}                      # 条件不成立
        v = asset.get(ff)
        if v in (None, "", []):
            return True, "", {}
        return (False,
                f"当 {wf}=「{wv}」时，{ff} 应为空值，实际为「{str(v)[:24]}」",
                {"field": ff, "value": v, "when": f"{wf}={wv}"})


CHECKER_BY_KIND_V2_EXTRA = {ClauseKind.FORBID_WHEN: ForbidWhenChecker}


# ============================================================================
#  v2 判据表
# ============================================================================
CHECKER_BY_KIND_V2 = {
    ClauseKindV2.NOT_FUTURE: NotFutureChecker,
    ClauseKindV2.RANGE_BY_TABLE: RangeByTableChecker,
}


def make_checker(clause: Clause):
    """按 kind 造判据：先查 v1，再查 v2。kind 统一归一化成枚举成员。"""
    from standard.compiler import _CHECKER_BY_KIND
    k = clause.kind
    if not isinstance(k, ClauseKind):
        try:
            k = ClauseKind(k)                       # 字符串 → 枚举成员
        except ValueError:
            k = ClauseKind(k.value if hasattr(k, "value") else k)
    if k in _CHECKER_BY_KIND:
        return _CHECKER_BY_KIND[k](clause)
    if k in CHECKER_BY_KIND_V2:
        return CHECKER_BY_KIND_V2[k](clause)
    if k in CHECKER_BY_KIND_V2_EXTRA:
        return CHECKER_BY_KIND_V2_EXTRA[k](clause)
    if k == ClauseKindV2.UNIQUE_ACROSS:
        return UniqueAcrossChecker(clause)
    raise KeyError(f"判据语言不认识这种条款类型：{k}")
