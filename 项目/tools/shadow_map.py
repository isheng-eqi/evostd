"""影子映射：把"不可编译"条款拆成「机械可判的影子」+「确实只能靠人的部分」。

为什么需要
----------
制造业标准里有 3 条被标注为 `uncompilable`：

    第41条  设备状态应真实反映现场实际状态，不得虚报。
    第42条  检修工单内容应完整反映实际作业过程与更换件情况。
    第43条  图纸与影像资料应真实对应当前在役设备，不得用历史资料充数。

**系统标 `uncompilable` 是正确的** —— 这三条都要求"记录与物理现实一致"，
而系统没有"物理现实"这个数据源。它没有硬凑一条弱判据去冒充。

但**每一条都有一部分是可判的**。实测发现这些影子**大部分已经由现有判据覆盖**
（只是没归因到原条款）：

    第23条 条件必填   停用 → 必须有 retire_date
    第26条 条件必填   异常 → 必须有 disposal              ← 第42条的影子
    第30条 条件禁止   在用 → 禁止有 retire_date           ← 第41条的影子
    第34条 跨文档引用  source_doc_ids 必须在受控清单        ← 第43条的影子
    第44条 基准时间约束 check_date 不得是未来日期           ← 第43条的时效部分

所以**这里不新增判据**，只做两件事：

1. **归因**：写清"哪条影子由哪条判据承担"，让"不可编译"不再是黑箱。
2. **划界**：写清"影子的哪一部分仍然只能靠人"，防止把弱判据冒充成强条款。

★ 为什么第 2 点必须写
--------------------
影子条款**比原条款弱**。如果对外宣称"第41条已可判"，那是
**把弱判据冒充成强条款** —— 与本项目抓过的
"判据 id 写着 `-wide`、行为却是 exact" 是同一个错误形状。

用法
----
    python tools/shadow_map.py            # 校验映射：引用的判据是否都存在且已采纳
    python tools/shadow_map.py -v         # 连影子本身一起打印
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

# ── 映射：不可编译条款 → 承担其机械部分的判据 ──────────────────────────
# `covers` 写的是**这条判据实际判的是什么**（不是它在标准里的原文），
# `human_only` 写的是**影子判不到、仍然只能靠人的那部分**。
SHADOWS: dict[str, dict] = {
    "manufacturing": {
        "第41条": {
            "text": "设备数据资产记录的设备状态应真实反映设备在现场的实际状态，不得虚报。",
            "covers": [
                {"clause": "第30条", "what": "状态为「在用」时不得存在 retire_date（状态与退役记录矛盾）"},
                {"clause": "第23条", "what": "状态为「停用」时必须存在 retire_date（状态变更无凭据）"},
                {"clause": "第10条", "what": "equipment_status 取值必须在受控词表内"},
            ],
            "human_only": "「现场的实际状态」本身 —— 系统没有这个数据源。"
                          "影子只能查**记录之间**是否自相矛盾，查不到记录是否与现场一致。",
        },
        "第42条": {
            "text": "检修工单登记的内容应完整反映本次检修的实际作业过程与更换件情况。",
            "covers": [
                {"clause": "第26条", "what": "check_result 为「异常」时必须填 disposal（异常无处置说明）"},
                {"clause": "第21条", "what": "collect_time 必填"},
                {"clause": "第22条", "what": "maintainer 必填"},
                {"clause": "第16条", "what": "work_order_no 格式必须是 WO-\\d{6}"},
            ],
            "human_only": "「实际作业过程」与「实际更换件」是否被如实登记。"
                          "影子只能查**必填字段是否为空**，查不到填进去的内容是否属实。",
        },
        "第43条": {
            "text": "图纸与影像资料应真实对应当前在役设备，不得使用与现场不符的历史资料充数。",
            "covers": [
                {"clause": "第34条", "what": "source_doc_ids 引用的文档必须在受控清单内（引用可溯）"},
                {"clause": "第44条", "what": "check_date 不得是未来日期（时效下界）"},
                {"clause": "第13条", "what": "media_type 取值必须在受控词表内"},
                {"clause": "第25条", "what": "media_type 为「影像」时必须填 annotation_note"},
            ],
            "human_only": "「资料是否对应当前在役设备」—— 需要把资料内容与设备实体比对。"
                          "影子只能查**引用是否可溯、日期是否合法**，查不到资料内容对不对。",
        },
    },
}


def check(domain: str = "manufacturing") -> dict:
    # ★ 顺序很重要：**必须先设 EVOSTD_STATE，再 import evostd.server**。
    #   `evostd.server` 在**模块导入时**就根据 env 算好了 `DEFAULT_STATE_DIR`；
    #   先 import 再设 env，`build_service()` 会去读**默认目录**——
    #   于是脚本**静默地检查了另一个库**，报出"第44条不存在"这种假问题。
    #   （实测踩到：CLI 跑报错、在设好 env 的进程里跑却正常，差的就是这个顺序。）
    os.environ.setdefault("EVOSTD_NOW", "2026-10-13")
    os.environ["EVOSTD_STATE"] = os.path.join(_ROOT, "src", "runs", f"shadow-{domain}")
    import evostd.server as S

    S.build_service(domain)
    svc = S._svc()

    adopted = {r.get("clause_id") for r in svc.store.list_clauses("adopted")}
    uncompilable = {r.get("clause_id") for r in svc.store.list_clauses("uncompilable")}
    mapping = SHADOWS.get(domain, {})

    rows, problems = [], []
    for cid, spec in mapping.items():
        if cid not in uncompilable:
            problems.append(f"{cid} 不再处于 uncompilable（映射已过时）")
        for cov in spec["covers"]:
            ref = cov["clause"]
            exists = ref in adopted
            if not exists:
                problems.append(f"{cid} 的影子引用了不存在的判据 {ref}")
            rows.append({"uncompilable": cid, "shadow_clause": ref,
                         "what": cov["what"], "exists": exists})

    # 反向：有没有不可编译条款**没有**写影子映射
    unmapped = sorted(uncompilable - set(mapping))
    if unmapped:
        problems.append(f"这些不可编译条款还没有影子映射：{unmapped}")

    return {
        "domain": domain,
        "n_uncompilable": len(uncompilable),
        "n_mapped": len(mapping),
        "n_shadow_refs": len(rows),
        "rows": rows,
        "unmapped_uncompilable": unmapped,
        "problems": problems,
        "ok": not problems,
        "note": ("影子条款**比原条款弱**。影子映射只说明「哪些机械部分已被覆盖」，"
                 "**不等于原条款已可判** —— 每条的 human_only 仍是未覆盖的部分。"),
    }


def main() -> int:
    domains = [sys.argv[1]] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") \
        else ["manufacturing"]
    verbose = "-v" in sys.argv
    rc = 0
    for d in domains:
        r = check(d)
        print("=" * 78)
        print(f"影子映射校验：{d}")
        print("=" * 78)
        print(f"  不可编译条款 {r['n_uncompilable']} 条 | 已写映射 {r['n_mapped']} 条 "
              f"| 影子引用 {r['n_shadow_refs']} 处")
        if verbose:
            for row in r["rows"]:
                print(f"    {row['uncompilable']} → {row['shadow_clause']:<8} "
                      f"{'✓' if row['exists'] else '✗'} {row['what']}")
        if r["problems"]:
            print("  ★ 问题：")
            for p in r["problems"]:
                print(f"    - {p}")
            rc |= 2
        else:
            print("  ✓ 映射引用的判据全部存在且已采纳")
        print()
        print("  注意：影子**比原条款弱**。本校验只说明「哪些机械部分已被覆盖」，")
        print("        **不等于原条款已可判** —— 每条的 human_only 仍是未覆盖的部分。")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
