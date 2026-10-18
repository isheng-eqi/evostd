"""极端与边界测试：专打"看起来合法但实际不可能"的输入。

角度（与 multi_angle_test 不重复）：
  X1 超长值          10000 字符的字段值
  X2 特殊字符        emoji / 控制字符 / 换行注入
  X3 日历不可能日期  2027-02-29（非闰年）、2026-13-45
  X4 时间边界        闰日 2028-02-29、跨年 12-31→01-01
  X5 空资产          {}
  X6 入口混淆        给 service 传 str / 给模块级传 list
  X7 多跳越界        multi_hop(max_hops=10) 是否会失控
  X8 并发            10 线程同时 std_verify，结果是否一致

用法：python tools/extreme_test.py
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
    os.environ["EVOSTD_STATE"] = os.path.join(_SRC, "runs", "extreme")
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
    from domains.manufacturing.assets import all_assets as A

    mfg = S.build_service("manufacturing")
    raw = A()
    clean = [{k: v for k, v in x.items() if not k.startswith("_")} for x in raw]
    base = dict(clean[0])

    # ── X1 超长值 ───────────────────────────────────────────────
    try:
        a = dict(base); a["maintainer"] = "张" * 10000
        r = mfg.std_verify([a])["results"][0]
        rec("X1", "超长值", True, f"10000 字符的 maintainer → 不崩（违例 {r['n_violations']} 条）")
    except Exception as ex:
        rec("X1", "超长值", False, f"抛异常 {type(ex).__name__}: {str(ex)[:50]}")

    # ── X2 特殊字符 ─────────────────────────────────────────────
    try:
        a = dict(base); a["maintainer"] = "😀\n\t\x00赵四"
        r = mfg.std_verify([a])["results"][0]
        rec("X2", "特殊字符", True, f"emoji+换行+NUL → 不崩（违例 {r['n_violations']} 条）")
    except Exception as ex:
        rec("X2", "特殊字符", False, f"抛异常 {type(ex).__name__}: {str(ex)[:50]}")

    # ── X3 ★ 日历不可能的日期 ───────────────────────────────────
    # 这两条如果是"格式对就算通过"，那是真缺陷：2027-02-29 根本不存在。
    bad_dates = ["2027-02-29", "2026-13-45", "2026-04-31"]
    caught = []
    for d in bad_dates:
        a = dict(base); a["check_date"] = d
        r = mfg.std_verify([a])["results"][0]
        if r["n_violations"] > 0:
            caught.append((d, [v["clause_id"] for v in r["violations"]]))
    rec("X3", "日历日期", len(caught) == len(bad_dates),
        f"不存在的日期 {bad_dates} → 被抓到 {len(caught)}/{len(bad_dates)} {caught}")

    # ── X4 时间边界（合法） ─────────────────────────────────────
    # ★ 必须用**过去**的合法日期：2028-02-29 虽然日历合法，但它晚于参考时间
    #   2026-10-13，被"不得为未来"抓到是**正确**的，不是误报。
    #   （我第一版写错了，把正确行为当成了缺陷。）
    ok_dates = ["2024-02-29", "2025-03-01", "2026-01-01"]
    false_pos = []
    for d in ok_dates:
        a = dict(base); a["check_date"] = d
        r = mfg.std_verify([a])["results"][0]
        hits = [v["clause_id"] for v in r["violations"]]
        if hits:
            false_pos.append((d, hits))
    rec("X4", "时间边界", not false_pos, f"合法日期 {ok_dates} → 误报 {len(false_pos)} 条 {false_pos}")

    # ── X5 空资产 ───────────────────────────────────────────────
    try:
        r = mfg.std_verify([{}])["results"][0]
        rec("X5", "空资产", r["n_violations"] > 0, f"{{}} → 违例 {r['n_violations']} 条（应被必填判据抓）")
    except Exception as ex:
        rec("X5", "空资产", True, f"{{}} → 抛 {type(ex).__name__}（不算静默）")

    # ── X6 入口混淆 ─────────────────────────────────────────────
    # service 方法收 list；模块级收 JSON 串。用错应该**明确报错**，不能静默。
    try:
        mfg.std_verify(json.dumps([base]))
        rec("X6", "入口混淆", False, "给 service 传 JSON 串**没有报错**（会被逐字符当资产，静默错）")
    except Exception as ex:
        rec("X6", "入口混淆", True, f"给 service 传 JSON 串 → 抛 {type(ex).__name__}（不算静默）")
    try:
        e = S.std_verify([base])
        rec("X7a", "入口混淆", e.get("ok") is False,
            f"给模块级传 list → ok={e.get('ok')} error={str(e.get('error'))[:40]}")
    except Exception as ex:
        rec("X7a", "入口混淆", True, f"给模块级传 list → 抛 {type(ex).__name__}")

    # ── X7 多跳越界 ─────────────────────────────────────────────
    try:
        aid = clean[0].get("asset_id")
        r = S.multi_hop(aid, 10)
        n = r.get("n_hops")
        rec("X7", "多跳越界", r.get("ok") is True and isinstance(n, int) and n <= 11,
            f"multi_hop(max_hops=10) → ok={r.get('ok')} n_hops={n}（有界即通过）")
    except Exception as ex:
        rec("X7", "多跳越界", False, f"抛异常 {type(ex).__name__}: {str(ex)[:50]}")

    # ── X8 并发 ─────────────────────────────────────────────────
    try:
        import threading
        outs = []
        lock = threading.Lock()

        def worker():
            rr = mfg.std_verify(clean)["compliant_rate"]
            with lock:
                outs.append(rr)

        ts = [threading.Thread(target=worker) for _ in range(10)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        rec("X8", "并发", len(set(outs)) == 1,
            f"10 线程并发同输入 → 结果集合 {set(outs)}（应只有一个值）")
    except Exception as ex:
        rec("X8", "并发", False, f"并发抛异常 {type(ex).__name__}: {str(ex)[:50]}")

    # ── 汇总 ────────────────────────────────────────────────────
    W = 100
    print("=" * W)
    print("极端与边界测试")
    print("=" * W)
    print(f"  {'项':<5}{'角度':<10}{'结果':<7}说明")
    print(f"  {'-'*5}{'-'*10}{'-'*7}{'-'*74}")
    npass = nfail = 0
    for code, angle, ok, detail in _res:
        mark = "PASS" if ok else "FAIL"
        npass += 1 if ok else 0
        nfail += 0 if ok else 1
        print(f"  {code:<5}{angle:<10}{mark:<7}{detail[:74]}")
    print()
    print(f"  合计 {len(_res)} 项：PASS {npass} / FAIL {nfail}")
    return 0 if nfail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
