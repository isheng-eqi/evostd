"""标准编译器：把权威文本的条款，编译成可执行的对错判据。

这是整个系统的"目标函数工厂"——自进化没有它就没有靶子。

设计要点
--------
1. **每条判据都必须带 `source`（标准名 + 条款号）**，任何违例都要能回指到原文。
   这是"标准对齐要有牙齿"与"决策链路可溯源"的同一个落点。
2. **编译器必须承认自己编译不了什么**。`compile_standard()` 同时返回
   `uncompilable`——那些"只能靠人判断"的条款。它们不是失败，而是
   **下一步扩张可达编辑集的入口**（见 docs/21 的元机制）。
3. 判据是**纯函数**：同输入同输出，不依赖时钟 / 随机 / 网络。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ============================================================================
#  条款模型
# ============================================================================
class ClauseKind(str, Enum):
    RANGE = "数值范围"            # 字段取值必须落在区间内
    ENUM = "受控词表"             # 字段取值必须来自受控词表
    PATTERN = "格式约束"          # 字段必须匹配正则（如哈希、编号）
    REQUIRED = "必填约束"         # 字段必须存在且非空
    CONDITIONAL = "条件必填"      # 当条件成立时，某字段必须存在
    CONSISTENCY = "跨字段一致性"   # 字段之间的序关系（如 采集时间 ≤ 入库时间）
    REFERENCE = "跨文档引用"      # 字段取值必须**存在于**受控清单中（白名单）
    # ★ 下面这一类是"判据语言扩张"的产物：白名单表达不了"不得使用已废止标准"这类规则。
    #   它的加入不是设计时想好的，而是**被一次失败逼出来的**（见 docs/23 失败记录）。
    FORBIDDEN = "禁止取值"        # 字段取值**不得属于**某受控集合（黑名单）

    # ★ 判据语言 v2（二阶自进化）：这三类不是设计出来的，是**被实测的盲区逼出来的**。
    #   B1 判据需要知道"现在" / B2·B4 上限取决于另一字段 / B3 判据看不到别的资产。
    NOT_FUTURE = "基准时间约束"        # 取值不得晚于**显式注入**的基准时间
    RANGE_BY_TABLE = "查表区间"        # 区间取决于另一字段，查 ctx 里的受控表
    UNIQUE_ACROSS = "集合级唯一性"     # 判据对象从「点」升级到「集合」
    # ★ 第 4 类由**机械发现的编译错误**逼出来（见 docs/42 第十九节）：
    #   标准原文写"设备状态为在用…停用日期应为空值，不得填写"，
    #   编译时被塞进了 CONSISTENCY（两字段等价），于是变成
    #   `equipment_status == retire_date` —— 一句语义完全不同的判据。
    #   它是 CONDITIONAL 的**反向**：CONDITIONAL 管"必须填"，它管"必须不填"。
    FORBID_WHEN = "条件禁止"           # 当条件成立时，某字段必须为空


@dataclass
class Clause:
    """一条标准条款。text 是原文，params 是编译所需的参数。"""
    clause_id: str                 # 如 "第10条"
    title: str
    kind: ClauseKind
    text: str                      # ★ 原文，用于溯源展示
    params: dict
    standard: str = ""             # 所属标准名
    compilable: bool = True        # False = 只能人工判断

    @property
    def source(self) -> str:
        return f"《{self.standard}》{self.clause_id}"


@dataclass
class Finding:
    """一条判据的结论。ok=False 时即为违例。"""
    clause_id: str
    source: str
    ok: bool
    message: str = ""
    evidence: dict = field(default_factory=dict)
    clause_text: str = ""

    def __str__(self) -> str:
        if self.ok:
            return f"✓ {self.source}"
        return f"✗ 违反 {self.source}：{self.message}｜原文：「{self.clause_text}」"


# ============================================================================
#  判据（编译产物）
# ============================================================================
class Checker:
    """一条可执行判据。子类实现 _run()。"""

    def __init__(self, clause: Clause):
        self.clause = clause
        # ★★ 构造时做**判据定义校验**。
        #
        #   动机（契约测试 Y2）：`RangeChecker._run` 里
        #   `lo, hi = params.get("min"), params.get("max")` —— **缺键被当成"无界"**。
        #   于是 `max` 打成 `mx` 这种拼写错，会让上界**静默失效**：
        #   条款看起来在工作、实际什么都不拦，而且没有任何提示。
        #
        #   这里在构造期就拒收，让它变成 `uncompilable`（可见），
        #   而不是"半残但显示正常"。
        problems = self._validate()
        if problems:
            raise ValueError("判据定义无效：" + "；".join(problems))

    def _validate(self) -> list[str]:
        """子类覆盖：返回定义问题列表（空 = 合法）。默认不校验。"""
        return []

    def check(self, asset: dict, ctx: dict) -> Optional[Finding]:
        ok, msg, ev = self._run(asset, ctx)
        return Finding(clause_id=self.clause.clause_id, source=self.clause.source,
                       ok=ok, message=msg, evidence=ev, clause_text=self.clause.text)

    def _run(self, asset: dict, ctx: dict) -> tuple[bool, str, dict]:
        raise NotImplementedError


class RangeChecker(Checker):
    _ALLOWED = {"field", "min", "max"}

    def _validate(self) -> list[str]:
        p = self.clause.params
        out = []
        extra = set(p) - self._ALLOWED
        if extra:
            out.append(f"出现未知参数 {sorted(extra)}（可能是拼写错，如 max 写成 mx）")
        lo, hi = p.get("min"), p.get("max")
        if lo is None and hi is None:
            out.append("min 与 max 都没有 = 任何值都通过 = 无用判据")
        if lo is not None and hi is not None and lo > hi:
            out.append(f"min({lo}) > max({hi})，该范围是空集")
        return out

    def _run(self, asset, ctx):
        f = self.clause.params["field"]
        if f not in asset or asset[f] is None:
            return True, "", {}                      # 缺字段由 REQUIRED 条款负责
        v = asset[f]
        lo, hi = self.clause.params.get("min"), self.clause.params.get("max")
        if not isinstance(v, (int, float)):
            return False, f"{f} 应为数值，实际 {type(v).__name__}", {"field": f, "value": v}
        if lo is not None and v < lo:
            return False, f"{f}={v} 低于下限 {lo}", {"field": f, "value": v, "min": lo}
        if hi is not None and v > hi:
            return False, f"{f}={v} 超过上限 {hi}", {"field": f, "value": v, "max": hi}
        return True, "", {}


class EnumChecker(Checker):
    def _run(self, asset, ctx):
        f = self.clause.params["field"]
        if f not in asset or asset[f] is None:
            return True, "", {}
        allowed = self.clause.params["allowed"]
        v = asset[f]
        if v not in allowed:
            return False, f"{f}=「{v}」不在受控词表 {allowed} 内", {"field": f, "value": v}
        return True, "", {}


class PatternChecker(Checker):
    def _run(self, asset, ctx):
        f = self.clause.params["field"]
        if f not in asset or asset[f] is None:
            return True, "", {}
        v = str(asset[f])
        pat = self.clause.params["pattern"]
        if not re.fullmatch(pat, v):
            return False, f"{f}=「{v[:40]}」不匹配格式 {pat}", {"field": f, "value": v[:80]}
        return True, "", {}


class RequiredChecker(Checker):
    def _run(self, asset, ctx):
        f = self.clause.params["field"]
        v = asset.get(f)
        if v is None or (isinstance(v, str) and not v.strip()) or v == []:
            return False, f"必填字段 {f} 缺失或为空", {"field": f}
        return True, "", {}


class ConditionalChecker(Checker):
    def _run(self, asset, ctx):
        p = self.clause.params
        wf, wv, rf = p["when_field"], p["when_value"], p["require_field"]
        if asset.get(wf) != wv:
            return True, "", {}                      # 条件不成立，条款不适用
        v = asset.get(rf)
        if v is None or (isinstance(v, str) and not v.strip()):
            return False, (f"当 {wf}=「{wv}」时，{rf} 必须填写"), {"field": rf, "when": f"{wf}={wv}"}
        return True, "", {}


class ConsistencyChecker(Checker):
    def _run(self, asset, ctx):
        p = self.clause.params
        a, b, op = p["left"], p["right"], p["op"]      # 要求 a op b
        if a not in asset or b not in asset:
            return True, "", {}
        x, y = asset[a], asset[b]
        if x is None or y is None:
            return True, "", {}
        bad = ((op == "<=" and not x <= y) or (op == ">=" and not x >= y)
               or (op == "<" and not x < y) or (op == ">" and not x > y)
               or (op == "==" and not x == y))
        if bad:
            return False, f"不一致：要求 {a} {op} {b}，实际 {a}=「{x}」 {b}=「{y}」", \
                {"left": a, "right": b, "actual": [str(x), str(y)]}
        return True, "", {}


class ReferenceChecker(Checker):
    """白名单：字段取值必须**存在**于受控清单中。"""

    def _run(self, asset, ctx):
        f = self.clause.params["field"]
        reg_key = self.clause.params["registry"]
        if f not in asset or asset[f] is None:
            return True, "", {}
        registry = set(ctx.get(reg_key, []))
        vals = asset[f] if isinstance(asset[f], list) else [asset[f]]
        missing = [v for v in vals if v not in registry]
        if missing:
            return False, f"{f} 引用了受控清单外的条目：{missing}", {"field": f, "missing": missing}
        return True, "", {}


class ForbiddenChecker(Checker):
    """黑名单：字段取值**不得属于**某受控集合。

    ★ 这一类判据不是设计时想到的，而是被实验逼出来的：
      用白名单表达"不得使用已废止标准"是不可能的——白名单要求取值在集合内，
      而这里要求取值不在集合内。**判据语言本身不够用，回路就卡死在第一代。**
    """

    def _run(self, asset, ctx):
        f = self.clause.params["field"]
        reg_key = self.clause.params["registry"]
        if f not in asset or asset[f] is None:
            return True, "", {}
        forbidden = set(ctx.get(reg_key, []))
        vals = asset[f] if isinstance(asset[f], list) else [asset[f]]
        hit = [v for v in vals if v in forbidden]
        if hit:
            return False, f"{f} 命中了禁止取值：{hit}", {"field": f, "hit": hit}
        return True, "", {}


_CHECKER_BY_KIND = {
    ClauseKind.RANGE: RangeChecker,
    ClauseKind.ENUM: EnumChecker,
    ClauseKind.PATTERN: PatternChecker,
    ClauseKind.REQUIRED: RequiredChecker,
    ClauseKind.CONDITIONAL: ConditionalChecker,
    ClauseKind.CONSISTENCY: ConsistencyChecker,
    ClauseKind.REFERENCE: ReferenceChecker,
    ClauseKind.FORBIDDEN: ForbiddenChecker,
}


# ============================================================================
#  编译器
# ============================================================================
@dataclass
class CompileResult:
    standard: str
    checkers: list[Checker]
    uncompilable: list[Clause]
    clause_count: int

    @property
    def compile_rate(self) -> float:
        return len(self.checkers) / self.clause_count if self.clause_count else 0.0

    def summary(self) -> str:
        kinds: dict[str, int] = {}
        for c in self.checkers:
            kinds[c.clause.kind.value] = kinds.get(c.clause.kind.value, 0) + 1
        return (f"《{self.standard}》：{self.clause_count} 条 → "
                f"编译成功 {len(self.checkers)} 条（{self.compile_rate:.0%}），"
                f"不可编译 {len(self.uncompilable)} 条；判据类型分布 {kinds}")


def _resolve_checker(kind):
    """先查 v1 判据表，查不到再懒加载 v2（避免 compiler ← grammar_v2 的循环 import）。"""
    if kind in _CHECKER_BY_KIND:
        return _CHECKER_BY_KIND[kind]
    from standard.grammar_v2 import CHECKER_BY_KIND_V2, ClauseKindV2
    if kind in CHECKER_BY_KIND_V2:
        return CHECKER_BY_KIND_V2[kind]
    from standard.grammar_v2 import CHECKER_BY_KIND_V2_EXTRA
    if kind in CHECKER_BY_KIND_V2_EXTRA:
        return CHECKER_BY_KIND_V2_EXTRA[kind]
    if getattr(kind, "value", kind) == ClauseKindV2.UNIQUE_ACROSS:
        from standard.grammar_v2 import UniqueAcrossChecker
        return UniqueAcrossChecker
    raise KeyError(f"判据语言不认识这种条款类型：{kind}")


def compile_standard(clauses: list[Clause]) -> CompileResult:
    """把条款列表编译成判据列表。

    ★ 编译不了的条款**不会被丢掉**，而是进入 `uncompilable`——
    它们是"可达编辑集"下一步要扩张的方向（见 docs/21 元机制）。
    """
    checkers: list[Checker] = []
    uncompilable: list[Clause] = []
    standard = clauses[0].standard if clauses else ""
    for c in clauses:
        # ★ 用 _resolve_checker 而不是直接查 _CHECKER_BY_KIND：
        #   否则 v2 新增的三类条款会被判成"不可编译"，**静默地不参与裁决**。
        #   实测踩到过：服务日志显示"v2 已装载：新增 4 条"，但 checkers 仍是 34 条，
        #   四条盲区资产照旧零违例——装载成功了，却没生效。
        try:
            cls = _resolve_checker(c.kind)
        except KeyError:
            cls = None
        if not c.compilable or cls is None:
            uncompilable.append(c)
            continue
        # ★ 构造期校验失败（如 RANGE 的 max 拼错成 mx）也要进 `uncompilable`，
        #   而不是让整个编译**崩掉**。原先是裸调用 `cls(c)`：
        #   一条定义坏掉的条款会连带打死其它所有判据。
        try:
            checkers.append(cls(c))
        except Exception as _e:
            uncompilable.append(c)
            # 大声失败：静默跳过会让"条款在工作"和"条款没生效"看起来一样。
            import sys as _sys
            print(f"[compile_standard] 条款 {c.clause_id} 定义无效，已归入不可编译：{_e}",
                  file=_sys.stderr)
    return CompileResult(standard=standard, checkers=checkers,
                         uncompilable=uncompilable, clause_count=len(clauses))


class StandardVerifier:
    """编译产物的使用面：给一个资产，返回全部结论。★ 纯函数，确定性。"""

    name = "standard-compiler-verifier"
    deterministic = True

    def __init__(self, compiled: CompileResult):
        self.compiled = compiled

    def verify(self, asset: dict, ctx: Optional[dict] = None) -> list[Finding]:
        ctx = ctx or {}
        return [f for f in (c.check(asset, ctx) for c in self.compiled.checkers) if f is not None]

    def violations(self, asset: dict, ctx: Optional[dict] = None) -> list[Finding]:
        return [f for f in self.verify(asset, ctx) if f is not None and not f.ok]

    def compliance(self, asset: dict, ctx: Optional[dict] = None) -> float:
        """合规率 —— 可直接作为自进化的适应度。"""
        fs = self.verify(asset, ctx)
        if not fs:
            return 1.0
        return round(sum(1 for f in fs if f.ok) / len(fs), 6)
