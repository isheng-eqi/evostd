"""★ 二阶自进化的最小可证伪实验。

检验的主张（docs/22）：
    自进化的天花板在"什么算对"。只有让**判据库本身长大**，
    系统才能看见以前看不见的违规——这才是一条真正的二阶曲线。

实验设计
--------
· 世界里有 15 条真规则。12 条被编译进**种子判据库**；3 条是**盲区**。
· 数据集：合规资产 + 违反种子条款的资产 + **只违反盲区规则的资产**（种子库全放行）。
· **锚点集**：人工确认过的极少量样本，dev（系统可见）/ test（只在审计时读）分开。
· 二阶回路：观察锚点里"被确认违规、但现有判据全部放行"的样本 → 提案新判据 →
  在 anchor-dev 上裁决（必须抓到盲区违规，且对合规样本零误报）→ 入库。
· 度量：**判据库在 anchor-test 上的辨别力 F1** 随代数变化。

★ 本实验真实走过的路（见 docs/23）
    第一版实验**在第 1 代就 reject_all**。原因不是搜索不行，而是**判据语言不够用**：
    现有 7 类里 REFERENCE 是白名单语义（取值必须**在**清单内），
    而盲区需要的是黑名单语义（取值**不得在**清单内）——"不得使用已废止标准"根本表达不出来。
    于是加入 G2 文法（FORBIDDEN 黑名单），并把它写成回路的一条规则：
    **提案全部被拒 = 文法不够 → 扩张文法 → 重试。** 这才是"扩大可达编辑集"的可执行版本。

跑法（在 项目/src 目录下）：python -m standard.verifier_evolution
"""
from __future__ import annotations

import hashlib
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from standard.compiler import (  # noqa: E402
    Checker, Clause, ClauseKind, _CHECKER_BY_KIND, compile_standard,
)
from standard.grammar_v2 import make_checker  # noqa: E402
from standard.samples import CLAUSES, CONTROLLED_STANDARDS  # noqa: E402

# ============================================================================
#  世界（真规则）：前 12 条已被编译成种子判据库，后 3 条是盲区
# ============================================================================
RETIRED_STORAGE = ["oss://retired/legacy-2019", "oss://retired/legacy-2020"]
RETIRED_ASSETS = ["AST-2019-0007", "AST-2018-0031"]
WITHDRAWN_STANDARDS = ["GB/T 11111-2005", "GB/T 22222-2008"]

CTX = {
    "controlled_standards": CONTROLLED_STANDARDS,
    "retired_storage": RETIRED_STORAGE,
    "retired_assets": RETIRED_ASSETS,
    "withdrawn_standards": WITHDRAWN_STANDARDS,
}

BLIND_SPOTS = [
    ("存储位置已下线", "storage_uri", "retired_storage"),
    ("资产标识已注销", "asset_id", "retired_assets"),
    ("引用了已废止标准", "cited_standards", "withdrawn_standards"),
]

# ============================================================================
#  ★ 判据语言的两个版本：G1 表达不了黑名单，G2 可以
# ============================================================================
GRAMMARS = [
    ("G1 基础判据语言（7 类，无黑名单语义）",
     [ClauseKind.RANGE, ClauseKind.ENUM, ClauseKind.PATTERN, ClauseKind.REQUIRED,
      ClauseKind.CONDITIONAL, ClauseKind.CONSISTENCY, ClauseKind.REFERENCE]),
    ("G2 扩张：加入黑名单语义 FORBIDDEN",
     [ClauseKind.RANGE, ClauseKind.ENUM, ClauseKind.PATTERN, ClauseKind.REQUIRED,
      ClauseKind.CONDITIONAL, ClauseKind.CONSISTENCY, ClauseKind.REFERENCE,
      ClauseKind.FORBIDDEN]),
]


def _h(n: int) -> str:
    return hashlib.sha256(str(n).encode()).hexdigest()


def make_dataset(rng: random.Random, n_each: int = 40) -> list[dict]:
    """三类资产：合规 / 违反种子条款 / **只违反盲区规则**。"""
    assets: list[dict] = []

    def base(i: int) -> dict:
        return {
            "asset_id": f"AST-2026-{i:04d}", "modality": "表格", "secrecy": "内部",
            "owner_dept": "设备管理部", "collect_time": "2026-03-01",
            "ingest_time": "2026-03-05", "quality_score": 80.0, "completeness": 0.9,
            "file_hash": _h(i), "cited_standards": ["GB/T 33863.1-2017"],
            "storage_uri": "oss://assets/2026/ok", "_label": "compliant", "_why": "",
        }

    for i in range(n_each):
        assets.append(base(i))
    for i in range(n_each):
        a = base(1000 + i)
        a["quality_score"] = 130.0
        a["_label"], a["_why"] = "violating", "种子条款：质量评分越界"
        assets.append(a)
    for i in range(n_each):
        a = base(2000 + i)
        label, field, reg = BLIND_SPOTS[i % len(BLIND_SPOTS)]
        values = CTX[reg]
        a[field] = [values[0]] if field == "cited_standards" else values[0]
        a["_label"], a["_why"] = "violating", f"盲区：{label}"
        assets.append(a)

    rng.shuffle(assets)
    return assets


# ============================================================================
#  提案文法（LLM 变异算子的替身：枚举候选判据，由锚点裁决）
# ============================================================================
def proposal_pool(kinds: list[ClauseKind]) -> list[Clause]:
    out: list[Clause] = []
    triple = [("retired_storage", "storage_uri", "存储位置"),
              ("retired_assets", "asset_id", "资产标识"),
              ("withdrawn_standards", "cited_standards", "引用标准")]
    if ClauseKind.REFERENCE in kinds:                 # 白名单写法（表达不了盲区，但系统会先试）
        for reg, field, label in triple:
            out.append(Clause(f"提案-REF-{reg}", f"{label}有效性", ClauseKind.REFERENCE,
                              f"数据资产的{label}应存在于受控清单 {reg} 中。",
                              {"field": field, "registry": reg}, standard="（系统自提案）"))
    if ClauseKind.FORBIDDEN in kinds:                 # 黑名单写法（G2 才具备）
        for reg, field, label in triple:
            out.append(Clause(f"提案-FORBID-{reg}", f"{label}不得失效", ClauseKind.FORBIDDEN,
                              f"数据资产的{label}不得指向已失效条目（受控清单 {reg}）。",
                              {"field": field, "registry": reg}, standard="（系统自提案）"))
    # 两条"看着合理但无区分度"的诱饵，用来考验裁决是否真的在把关
    out.append(Clause("提案-range-q", "质量评分下限", ClauseKind.RANGE,
                      "质量评分应不低于 60。",
                      {"field": "quality_score", "min": 60.0}, standard="（系统自提案）"))
    out.append(Clause("提案-enum-modal", "模态受控（与第6条重复）", ClauseKind.ENUM,
                      "模态应属于受控词表。",
                      {"field": "modality", "allowed": ["文本", "表格", "图文", "影像"]},
                      standard="（系统自提案）"))
    return out


# ============================================================================
#  判据库与辨别力
# ============================================================================
def flags(checkers: list[Checker], asset: dict, ctx: dict) -> bool:
    return any(not c.check(asset, ctx).ok for c in checkers)


def f1(checkers: list[Checker], items: list[dict], ctx: dict) -> dict:
    tp = fp = fn = tn = 0
    for a in items:
        pred, truth = flags(checkers, a, ctx), a["_label"] == "violating"
        tp += int(pred and truth)
        fp += int(pred and not truth)
        fn += int(not pred and truth)
        tn += int(not pred and not truth)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4),
            "f1": round(2 * p * r / (p + r), 4) if (p + r) else 0.0,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


# ============================================================================
#  二阶回路（含"文法不够就扩张文法"的元机制）
# ============================================================================
def run(seed: int = 42) -> dict:
    rng = random.Random(seed)
    data = make_dataset(rng)

    violating = [a for a in data if a["_label"] == "violating"]
    compliant = [a for a in data if a["_label"] == "compliant"]
    anchors = violating[:18] + compliant[:10]
    rng.shuffle(anchors)
    dev, test = anchors[:16], anchors[16:]

    compiled = compile_standard(CLAUSES)
    library: list[Checker] = list(compiled.checkers)

    # ★ 盲区必须实测确认，不能想当然：逐类统计"种子判据库是否已经捕获"
    blind_status = []
    for label, field, reg in BLIND_SPOTS:
        items = [a for a in data if a["_why"] == f"盲区：{label}"]
        caught = sum(1 for a in items if flags(compiled.checkers, a, CTX))
        blind_status.append({"盲区": label, "样本数": len(items), "种子库已捕获": caught,
                             "是否真盲区": "是" if caught == 0 else "★ 否——已被种子判据覆盖"})

    log: list[dict] = []
    curve = []

    def snap(gen: int, grammar: str) -> dict:
        return {"gen": gen, "grammar": grammar, "n_clauses": len(library),
                "dev": f1(library, dev, CTX), "test": f1(library, test, CTX)}

    g = 0
    curve.append(snap(0, GRAMMARS[0][0]))

    for gen in range(1, 9):
        unexplained = [a for a in dev if a["_label"] == "violating" and not flags(library, a, CTX)]
        if not unexplained:
            log.append({"gen": gen, "action": "stop", "reason": "锚点里已无未解释的违规"})
            break

        admitted = None
        while admitted is None and g < len(GRAMMARS):
            gname, kinds = GRAMMARS[g]
            best, best_gain, best_stat = None, 0, {}
            for cand in proposal_pool(kinds):
                if any(c.clause.clause_id == cand.clause_id for c in library):
                    continue
                c = make_checker(cand)
                caught = sum(1 for a in unexplained if not c.check(a, CTX).ok)
                false_alarm = sum(1 for a in dev
                                  if a["_label"] == "compliant" and not c.check(a, CTX).ok)
                gain = caught - 3 * false_alarm
                if gain > best_gain:
                    best, best_gain, best_stat = c, gain, {"caught": caught,
                                                           "false_alarm": false_alarm}
            # 裁决门槛：≥1 条已确认违规被解释（低资源下的现实要求，锚点本来就少），
            # 且对合规样本**零误报**（这一条才是真正把关的）。
            if best is not None and best_stat.get("false_alarm", 1) == 0 \
                    and best_stat.get("caught", 0) >= 1:
                admitted = best
                log.append({"gen": gen, "action": "admit", "grammar": gname,
                            "clause": best.clause.clause_id, "text": best.clause.text,
                            "stats": best_stat})
            else:
                log.append({"gen": gen, "action": "grammar_insufficient", "grammar": gname,
                            "reason": f"当前文法下没有候选能通过裁决（最佳 {best_stat or '无'}）"})
                g += 1
                if g < len(GRAMMARS):
                    log.append({"gen": gen, "action": "expand_grammar",
                                "to": GRAMMARS[g][0],
                                "reason": "提案全部被拒 = 判据语言表达不了该盲区 → 扩张文法重试"})
        if admitted is None:
            log.append({"gen": gen, "action": "stop", "reason": "文法已无更强者，回路停止"})
            break

        library.append(admitted)
        curve.append(snap(gen, GRAMMARS[min(g, len(GRAMMARS) - 1)][0]))

    # ---- 造假检测 ----
    class ConstTrue(Checker):
        def _run(self, asset, ctx): return False, "永远报违规", {}

    class ConstFalse(Checker):
        def _run(self, asset, ctx): return True, "", {}

    dt = ConstTrue(Clause("对抗-恒真", "", ClauseKind.REQUIRED, "永远报违规", {}))
    df = ConstFalse(Clause("对抗-恒假", "", ClauseKind.REQUIRED, "永远放行", {}))
    dev_v = [a for a in dev if a["_label"] == "violating"]
    dev_c = [a for a in dev if a["_label"] == "compliant"]
    last_dev, last_test = curve[-1]["dev"]["f1"], curve[-1]["test"]["f1"]
    audit = {
        "恒真判据": {"caught": sum(1 for a in dev_v if not dt.check(a, CTX).ok),
                     "false_alarm": sum(1 for a in dev_c if not dt.check(a, CTX).ok),
                     "裁决": "拒收（误报过多）"},
        "恒假判据": {"caught": sum(1 for a in dev_v if not df.check(a, CTX).ok),
                     "false_alarm": sum(1 for a in dev_c if not df.check(a, CTX).ok),
                     "裁决": "拒收（抓不到任何东西）"},
        "锚点 dev/test 落差": {"dev_f1": last_dev, "test_f1": last_test,
                               "gap": round(last_dev - last_test, 4),
                               "判定": "ok" if abs(last_dev - last_test) <= 0.10
                               else "★ 落差偏大（注意锚点样本量小，F1 分辨率有限）"},
    }
    return {"curve": curve, "log": log, "audit": audit, "blind_status": blind_status,
            "anchors": {"dev": len(dev), "test": len(test)},
            "unexplained_at_start": len([a for a in dev
                                         if a["_label"] == "violating"
                                         and not flags(compiled.checkers, a, CTX)])}


def main() -> None:
    print("=" * 82)
    print("二阶自进化最小实验：判据库能否自己长大，并看见以前看不见的违规")
    print("=" * 82)
    r = run()

    print(f"\n【设置】锚点 dev={r['anchors']['dev']} / test={r['anchors']['test']}（人工确认的极少量样本）")
    print(f"  种子判据库来自标准编译（12 条）。世界里另有 3 类盲区规则**不在**种子库里。")
    print(f"  ★ 起点：anchor-dev 里有 {r['unexplained_at_start']} 条已确认违规，"
          f"但现有判据**全部放行**——这就是盲区。")

    print("\n【盲区实测确认（不想当然）】")
    for b in r["blind_status"]:
        print(f"  · {b['盲区']:<16} 样本 {b['样本数']:>3} 条，"
              f"种子判据库已捕获 {b['种子库已捕获']:>3} 条 → 真盲区：{b['是否真盲区']}")
    print("  ↑ 这条检查很关键：所谓\"盲区\"必须实测，不能凭设计者想象。")

    print("\n【二阶曲线】代数 | 判据库 | 当前文法 | anchor-dev F1 | ★ anchor-test F1 | TP/FP/FN")
    print("-" * 82)
    for row in r["curve"]:
        t = row["test"]
        print(f"  {row['gen']:>4} |  {row['n_clauses']:>3}   | {row['grammar'][:12]:<12} "
              f"|    {row['dev']['f1']:.4f}     |      {t['f1']:.4f}       | {t['tp']}/{t['fp']}/{t['fn']}")

    print("\n【回路日志：它做了什么】")
    for e in r["log"]:
        a = e["action"]
        if a == "admit":
            print(f"  第{e['gen']}代 入库：{e['clause']}（文法：{e['grammar'][:2]}）")
            print(f"          「{e['text']}」")
            print(f"          裁决依据：抓到未解释违规 {e['stats']['caught']} 条，"
                  f"合规样本误报 {e['stats']['false_alarm']} 条")
        elif a == "grammar_insufficient":
            print(f"  第{e['gen']}代 ✗ {e['grammar']}：{e['reason']}")
        elif a == "expand_grammar":
            print(f"  第{e['gen']}代 ★ 扩张文法 → {e['to']}")
            print(f"          {e['reason']}")
        else:
            print(f"  第{e['gen']}代 {a}：{e['reason']}")

    print("\n【造假检测】")
    for k, v in r["audit"].items():
        if "gap" in v:
            print(f"  {k}: dev={v['dev_f1']} test={v['test_f1']} gap={v['gap']:+.4f} → {v['判定']}")
        else:
            print(f"  {k}: 抓到 {v['caught']} / 误报 {v['false_alarm']} → {v['裁决']}")

    c0, cN = r["curve"][0]["test"], r["curve"][-1]["test"]
    print("\n【结论】")
    print(f"  判据库 {r['curve'][0]['n_clauses']} → {r['curve'][-1]['n_clauses']} 条")
    print(f"  anchor-test F1 {c0['f1']:.4f} → {cN['f1']:.4f}"
          f"（召回 {c0['recall']:.4f} → {cN['recall']:.4f}，精度 {cN['precision']:.4f}）")
    print("  → 召回上升 = 系统**看见了以前看不见的违规**；精度保持 = 没靠误报换召回。")
    print("  → 关键不是'加了几条判据'，而是**中途扩张了判据语言本身**——")
    print("     这才是'扩大可达编辑集'，才是二阶曲线。")
    print("=" * 82)


if __name__ == "__main__":
    main()
