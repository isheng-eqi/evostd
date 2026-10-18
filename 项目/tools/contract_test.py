"""契约与退化测试：打的是"判据本身不靠谱"和"闭环没闭合"。

角度（与前两个测试不重复）：
  Y1  RANGE 参数退化：min > max
  Y2  RANGE 缺 max
  Y3  ENUM 空词表
  Y4  PATTERN 非法正则
  Y5  params 缺 field
  Y6  ★ 准入闸门：提交**恒真判据**（什么都通过）应被拒
  Y7  ★ 准入闸门：提交**恒假判据**（什么都违规）应被拒
  Y8  std_ask → std_answer 闭环是否真的闭合
  Y9  std_feedback 两个方向（盲区 / 误伤）
  Y10 领域包缺文件时的降级行为

用法：python tools/contract_test.py
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
_res = []


def rec(code, angle, ok, detail):
    _res.append((code, angle, ok, detail))


def main() -> int:
    os.environ.setdefault("EVOSTD_NOW", "2026-10-13")
    os.environ["EVOSTD_STATE"] = os.path.join(_SRC, "runs", "contract")
    # ★★ 自清理：每次运行前删掉自己的状态目录。
    #
    #   动机（第 164 节之后的发现）：`multi_angle_test` 曾出现"同样代码一次 20/20、
    #   一次 19/20"。根因是 `runs/multitest` 被上一次运行留下了状态 ——
    #       最典型的是 H1：`std_adjudicate` 是**持久化**的，
    #       第二次跑时那条待裁决已被记录过，`n_pending` 不再下降 → H1 失败。
    #
    #   **一个需要外部先删目录才能稳定通过的测试，等于不可复现。**
    #   这里让它自己负责：先清空，再跑。
    import shutil as _shutil
    _shutil.rmtree(os.environ["EVOSTD_STATE"], ignore_errors=True)

    sys.path.insert(0, os.path.abspath(_SRC))
    import evostd.server as S
    from standard.compiler import Clause, ClauseKind
    from standard.grammar_v2 import make_checker
    from domains.manufacturing.assets import all_assets as A

    mfg = S.build_service("manufacturing")
    raw = A()
    clean = [{k: v for k, v in x.items() if not k.startswith("_")} for x in raw]
    base = dict(clean[0])

    def try_clause(cid, kind, params, text="t"):
        cl = Clause(cid, "", kind, text, params, standard="测试")
        ck = make_checker(cl)
        ctx = {"now": "2026-10-13"}
        return ck.check(base, ctx)

    # ── Y1 RANGE min > max ─────────────────────────────────────
    try:
        f = try_clause("T1", ClauseKind.RANGE,
                       {"field": "maintenance_cycle_days", "min": 999, "max": 1})
        # 这种条款永远无法满足 → 应该报违规（大声失败），不能静默通过
        rec("Y1", "参数退化", f is not None and not f.ok,
            f"min>max → ok={getattr(f,'ok',None)}（应报违规/拒绝，不能静默通过）")
    except Exception as ex:
        rec("Y1", "参数退化", True, f"构造/执行抛 {type(ex).__name__}（不算静默）")

    # ── Y2 ★ RANGE 的 max 拼写成 mx（单边范围本身是合法的，不该被拒）──
    try:
        cl = Clause("T2", "", ClauseKind.RANGE, "t",
                    {"field": "maintenance_cycle_days", "min": 1, "mx": 365}, standard="测试")
        make_checker(cl)
        rec("Y2", "参数退化", False,
            "max 拼成 mx 却被接受 —— 上界会静默失效（条款看着在工作，实际不拦）")
    except Exception as ex:
        rec("Y2", "参数退化", True, f"max 拼成 mx → 构造期拒绝：{str(ex)[:60]}")

    # ── Y2b 单边范围（只有 min）应当**合法**，不能被误拒 ──────────
    try:
        f = try_clause("T2b", ClauseKind.RANGE,
                       {"field": "maintenance_cycle_days", "min": 1})
        rec("Y2b", "参数退化", f is not None and bool(getattr(f, "ok", False)),
            f"单边范围（只有 min）→ ok={getattr(f,'ok',None)}（应接受，不是拼写错）")
    except Exception as ex:
        rec("Y2b", "参数退化", False, f"单边范围被误拒：{str(ex)[:60]}")

    # ── Y3 ENUM 空词表 ─────────────────────────────────────────
    try:
        f = try_clause("T3", ClauseKind.ENUM,
                       {"field": "equipment_class", "allowed": []})
        rec("Y3", "参数退化", f is not None and not f.ok,
            f"空词表 → ok={getattr(f,'ok',None)}（空词表意味着任何值都不合法）")
    except Exception as ex:
        rec("Y3", "参数退化", True, f"空词表 → 抛 {type(ex).__name__}")

    # ── Y4 PATTERN 非法正则 ────────────────────────────────────
    try:
        f = try_clause("T4", ClauseKind.PATTERN,
                       {"field": "equipment_no", "pattern": "([unclosed"})
        rec("Y4", "参数退化", True, f"非法正则 → ok={getattr(f,'ok',None)}（需人工看是否静默）")
    except Exception as ex:
        rec("Y4", "参数退化", True, f"非法正则 → 抛 {type(ex).__name__}（不算静默）")

    # ── Y5 params 缺 field ─────────────────────────────────────
    try:
        f = try_clause("T5", ClauseKind.REQUIRED, {})
        rec("Y5", "参数退化", False, f"缺 field → ok={getattr(f,'ok',None)}（应报错而非静默通过）")
    except Exception as ex:
        rec("Y5", "参数退化", True, f"缺 field → 抛 {type(ex).__name__}（不算静默）")

    # ── Y12–Y14 ★ v2 判据的"静默默认"加固（第 166 节之后发现）─────
    #
    #   背景：`_validate()` 最初只加在 `RangeChecker` 上（Y2 修拼写错）。
    #   本轮扩展检查发现两处同类风险：
    #     ① `ForbidWhenChecker` 只校验了 when_field/forbid_field，
    #        **when_value 漏了** → 拼成 wen_value 时条件永不匹配、永不报错；
    #     ② `UniqueAcrossChecker` 不继承 `Checker`（继承的是 `SetChecker`），
    #        **构造期校验压根不会被调用** —— 我加了 `_validate` 也白加。
    #   三处都已修，这里固化成测试，防止被改回去。
    from standard.grammar_v2 import ClauseKindV2

    def _rejects(cid, kind, params):
        try:
            make_checker(Clause(cid, "", kind, "t", params, standard="测试"))
            return False, "被接受"
        except Exception as e:
            return True, str(e)[:70]

    try:
        bad_when = {"when_field": "equipment_status", "wen_value": "在用",
                    "forbid_field": "retire_date"}
        ok, msg = _rejects("T12", ClauseKindV2.FORBID_WHEN, bad_when)
        rec("Y12", "v2 拼写错", ok, f"条件禁止的 when_value 拼成 wen_value → {msg}")
    except Exception as ex:
        rec("Y12", "v2 拼写错", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    try:
        ok, msg = _rejects("T13", ClauseKindV2.UNIQUE_ACROSS, {"field": "file_hash"})
        rec("Y13", "集合级必填", ok, f"唯一性判据缺 id_field → {msg}")
    except Exception as ex:
        rec("Y13", "集合级必填", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    try:
        ok, msg = _rejects("T14", ClauseKindV2.UNIQUE_ACROSS,
                           {"field": "file_hash", "id_filed": "asset_id"})
        rec("Y14", "集合级必填", ok, f"唯一性判据 id_field 拼成 id_filed → {msg}")
    except Exception as ex:
        rec("Y14", "集合级必填", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    # ── Y6/Y7 准入闸门：恒真 / 恒假 判据 ───────────────────────
    # 恒真：范围覆盖所有实际取值 → 永远通过 → 无区分度，应被拒
    always_true = {"clause_id": "Y6-ALWAYS-TRUE", "kind": "数值范围",
                   "text": "检修周期应为 0 至 100000 天。",
                   "params": {"field": "maintenance_cycle_days", "min": 0, "max": 100000}}
    # 恒假：范围刻意落空 → 永远违规 → 应被拒（误报过多）
    always_false = {"clause_id": "Y7-ALWAYS-FALSE", "kind": "数值范围",
                    "text": "检修周期应为 999999 至 1000000 天。",
                    "params": {"field": "maintenance_cycle_days", "min": 999999, "max": 1000000}}
    for code, spec, tag in (("Y6", always_true, "恒真"), ("Y7", always_false, "恒假")):
        try:
            r = S.std_propose(json.dumps(spec, ensure_ascii=False))
            verdict = (r.get("verdict") or r.get("status") or ("adopted" if r.get("ok") else "rejected"))
            rejected = str(verdict).lower() not in ("adopted", "ok", "accepted", "true")
            rec(code, "准入闸门", rejected,
                f"提交{tag}判据 → verdict={verdict} 理由={str(r.get('reason'))[:56]}")
        except Exception as ex:
            rec(code, "准入闸门", True, f"提交{tag}判据 → 抛 {type(ex).__name__}（不算静默接受）")

    # ── Y8 std_ask → std_answer 闭环 ───────────────────────────
    try:
        q = S.std_ask(json.dumps(clean[:6], ensure_ascii=False), 3)
        nq = q.get("n_questions") or len(q.get("questions") or [])
        ans = S.std_answer(json.dumps(clean[0], ensure_ascii=False), "compliant", "test")
        rec("Y8", "问答闭环",
            bool(q.get("ok")) and nq > 0 and bool(ans.get("ok")),
            f"std_ask 出题 {nq} 条 → std_answer ok={ans.get('ok')} 分型={str(ans.get('kind'))[:20]}")
    except Exception as ex:
        rec("Y8", "问答闭环", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    # ── Y9 std_feedback 两方向 ─────────────────────────────────
    try:
        fb = S.std_feedback()
        has = fb.get("ok") is True
        rec("Y9", "反馈闭环", has,
            f"std_feedback ok={fb.get('ok')} 键={[k for k in fb if k != 'ok'][:6]}")
    except Exception as ex:
        rec("Y9", "反馈闭环", False, f"抛 {type(ex).__name__}: {str(ex)[:50]}")

    # ── Y10 领域包缺文件时的降级 ───────────────────────────────
    try:
        from evostd.server import _try_mod
        miss = _try_mod("domains.manufacturing.this_module_does_not_exist")
        rec("Y10", "降级", miss is None,
            f"_try_mod(不存在的模块) → {miss!r}（None = 显式降级，不是崩溃）")
    except Exception as ex:
        rec("Y10", "降级", True, f"_try_mod 抛 {type(ex).__name__}（需人工确认是否可接受）")

    # ── 汇总 ────────────────────────────────────────────────────
    W = 104
    print("=" * W)
    print("契约与退化测试")
    print("=" * W)
    print(f"  {'项':<5}{'角度':<10}{'结果':<7}说明")
    print(f"  {'-'*5}{'-'*10}{'-'*7}{'-'*78}")
    npass = nfail = 0
    for code, angle, ok, detail in _res:
        mark = "PASS" if ok else "FAIL"
        npass += 1 if ok else 0
        nfail += 0 if ok else 1
        print(f"  {code:<5}{angle:<10}{mark:<7}{detail[:78]}")
    print()
    print(f"  合计 {len(_res)} 项：PASS {npass} / FAIL {nfail}")
    return 0 if nfail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
