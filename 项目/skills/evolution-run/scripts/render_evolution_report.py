#!/usr/bin/env python3
"""Render an evolution run report (Markdown) plus its generation curve (CSV).

Input : the JSON payloads of ``evo_status`` / ``evo_finalize`` / ``evo_archive``.
Output: ``<stem>.md`` and ``evolution-curve.csv``, both declared in SKILL.md
        ``script_outputs``.

stdout carries exactly one line -- the Nexent artifact contract JSON. Nothing
else may be printed to stdout.

Determinism: no clock, no randomness, no network, no model calls. Every number
in the report is copied from the input payloads or computed from them.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from pathlib import Path

MIME_MD = "text/markdown"
MIME_CSV = "text/csv"
DEFAULT_MD = "evolution-report.md"
DEFAULT_CSV = "evolution-curve.csv"
DEFAULT_MAX_GAP = 0.1
PLATEAU_GENS = 3


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def load_json(path: str | None) -> dict:
    if not path:
        return {}
    candidate = Path(path)
    if not candidate.is_file():
        return {}
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def cell(value: object) -> str:
    return as_text(value).replace("|", "\\|").replace("\n", " ").strip()


def num(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def print_contract(payload: dict) -> None:
    line = json.dumps(payload, ensure_ascii=False)
    try:
        print(line)
    except UnicodeEncodeError:
        print(json.dumps(payload, ensure_ascii=True))


# --------------------------------------------------------------------------
# deterministic analysis
# --------------------------------------------------------------------------
def analyse(curve: list, final: dict, max_gap: float) -> dict:
    """Compute the plateau, the stop reasons and the recommendation."""
    gens = [c for c in curve if isinstance(c, dict)]
    result: dict = {
        "generations": len(gens),
        "valid_generations": sum(1 for c in gens if num(c.get("gen_score")) is not None),
        "invalid_generations": sum(1 for c in gens if num(c.get("gen_score")) is None),
        "best_score": None,
        "plateau_gens": None,
        "stalled": False,
        "archive_tail_flat": False,
        "reasons": [],
        "level": "continue",
    }
    if not gens:
        return result

    best = None
    for item in gens:
        value = num(item.get("best_score"))
        if value is not None:
            best = value
    result["best_score"] = best

    if best is not None:
        first_best = None
        for index, item in enumerate(gens):
            if num(item.get("best_score")) == best:
                first_best = index
                break
        if first_best is not None:
            plateau = len(gens) - 1 - first_best
            result["plateau_gens"] = plateau
            result["stalled"] = plateau >= PLATEAU_GENS

    tail = [c.get("archive_size") for c in gens[-PLATEAU_GENS:]]
    tail = [t for t in tail if isinstance(t, int)]
    result["archive_tail_flat"] = len(tail) >= 2 and len(set(tail)) == 1

    # ---- stop reasons, evaluated in priority order ----
    reasons: list[tuple] = []

    validity = final.get("run_validity") if isinstance(final.get("run_validity"), dict) else {}
    verdict = as_text(validity.get("verdict"))
    if verdict and verdict != "ok":
        reasons.append(("有效性判决异常", f"run_validity.verdict = {verdict}", "stop",
                        "本轮结论不成立。先修验证器或数据集切分，不要继续推进，也不要把本轮分数当成成果。"))

    gap = num(final.get("proxy_reality_gap"))
    if gap is not None and gap > max_gap:
        reasons.append(("代理与真实指标脱节", f"proxy_reality_gap = {gap} > {max_gap}", "stop",
                        "代理指标涨了但真实指标没跟上。停止推进，先复核验证器的代理口径。"))

    audit = final.get("audit") if isinstance(final.get("audit"), dict) else {}
    collapse = audit.get("collapse") if isinstance(audit.get("collapse"), dict) else {}
    collapse_verdict = as_text(collapse.get("verdict"))
    if collapse_verdict and collapse_verdict != "ok":
        reasons.append(("验证器可能塌缩", f"audit.collapse.verdict = {collapse_verdict}，"
                                          f"spread = {as_text(collapse.get('spread'))}", "stop",
                        "分数失去区分度时继续跑只是在优化噪声。停止推进并复核验证器。"))

    probes = [p for p in (audit.get("probes") or []) if isinstance(p, dict)]
    failed = [as_text(p.get("probe")) for p in probes if p.get("correctly_rejected") is False]
    if failed:
        reasons.append(("对抗探针未被正确拒绝", "探针 " + "、".join(failed) + " 拿到了不该拿的分", "stop",
                        "本轮所有分数提升都不可信，必须写在报告最前面。先修验证器。"))

    gate = final.get("gate") if isinstance(final.get("gate"), dict) else {}
    harmful = gate.get("harmful") or []
    if harmful:
        reasons.append(("闸门判定有害候选", f"{len(harmful)} 条候选在真实指标上造成伤害", "stop",
                        "逐条列出有害候选的 cid 与血统，并回滚或隔离它们。"))

    budget = final.get("budget") if isinstance(final.get("budget"), dict) else {}
    used, cap = num(budget.get("used")), num(budget.get("evals"))
    if used is not None and cap is not None and used >= cap:
        reasons.append(("预算耗尽", f"budget.used = {used} / {cap}", "warn",
                        "预算已用尽。要提高预算就新开一轮（换 run_id），不要在同一 run 上追加。"))

    if result["stalled"] and result["archive_tail_flat"]:
        reasons.append(("已停滞", f"best_score 连续 {result['plateau_gens']} 代未提升，"
                                  f"且最近 {PLATEAU_GENS} 代存档数不再增长", "warn",
                        "曲线收敛。继续跑收益极低，建议转 clause-proposal 补判据盲区，"
                        "或提高变异算子多样性后新开一轮。"))
    elif result["stalled"]:
        reasons.append(("best 分数停滞", f"best_score 连续 {result['plateau_gens']} 代未提升，"
                                          f"但存档数仍在增长", "warn",
                        "多样性还在扩张，可以再跑少量代数观察是否有新的最优。"))

    invalid = num(budget.get("invalid"))
    if invalid is not None and result["generations"]:
        rate = invalid / max(result["generations"], 1)
        if rate > 0.5:
            reasons.append(("无效候选比例过高", f"invalid = {invalid} / 代数 {result['generations']}", "warn",
                            "变异算子大量产出非法配置。收紧变异约束再继续。"))

    if not reasons:
        reasons.append(("仍在爬升", "未触发任何停止条件", "continue",
                        "曲线仍在改善，可以继续 evo_step；注意预算余量。"))

    order = {"stop": 0, "warn": 1, "continue": 2}
    reasons.sort(key=lambda r: order.get(r[2], 3))
    result["reasons"] = reasons
    result["level"] = reasons[0][2]
    return result


def render_markdown(status: dict, final: dict, archive: dict, seed: str,
                    analysis: dict, max_gap: float) -> str:
    curve = [c for c in (status.get("curve") or final.get("curve") or []) if isinstance(c, dict)]
    best = status.get("best") if isinstance(status.get("best"), dict) else \
        final.get("best") if isinstance(final.get("best"), dict) else {}
    budget = final.get("budget") if isinstance(final.get("budget"), dict) else \
        status.get("budget") if isinstance(status.get("budget"), dict) else {}
    validity = final.get("run_validity") if isinstance(final.get("run_validity"), dict) else {}
    audit = final.get("audit") if isinstance(final.get("audit"), dict) else {}
    gate = final.get("gate") if isinstance(final.get("gate"), dict) else {}
    archive_block = final.get("archive") if isinstance(final.get("archive"), dict) else archive

    out: list[str] = []
    out.append("# 自进化回路运行报告")
    out.append("")
    out.append("> 由 `scripts/render_evolution_report.py` 确定性渲染。"
               "分数、裁决、审计**全部来自 MCP 侧**，本报告不含任何模型判定。")
    out.append("")

    # ---- headline warning block ----
    stops = [r for r in analysis["reasons"] if r[2] == "stop"]
    if stops:
        out.append("## ⚠️ 先看这里")
        out.append("")
        for title, detail, _, action in stops:
            out.append(f"- **{title}**：{detail}")
            out.append(f"  - 处置：{action}")
        out.append("")

    out.append("## 一、实验身份")
    out.append("")
    out.append("| 项 | 值 |")
    out.append("| --- | --- |")
    out.append(f"| 领域 | {cell(status.get('domain') or final.get('domain')) or '（未标注）'} |")
    out.append(f"| run_id | `{cell(status.get('run_id') or final.get('run_id')) or '（未提供）'}` |")
    out.append(f"| seed | `{cell(seed) or '（未提供 —— 本轮不可复现）'}` |")
    out.append(f"| 标签 | {cell(final.get('label')) or '（无）'} |")
    out.append(f"| 实际代数 | {analysis['generations']}（有效 {analysis['valid_generations']}，"
               f"全非法 {analysis['invalid_generations']}） |")
    out.append(f"| 评估预算 | {cell(budget.get('used')) or '—'} / {cell(budget.get('evals')) or '—'} |")
    out.append(f"| 墙钟耗时 | {cell(budget.get('wall_s')) or '—'} s |")
    out.append(f"| 最优候选 | `{cell(best.get('cid')) or '—'}`　分数 {cell(best.get('score')) or '—'} |")
    out.append(f"| 有效性判决 | **{cell(validity.get('verdict')) or '（未提供）'}**"
               f"　margin = {cell(validity.get('margin')) or '—'} |")
    out.append(f"| 代理-真实落差 | {cell(final.get('proxy_reality_gap')) or '—'}"
               f"（阈值 {max_gap}） |")
    out.append("")

    out.append("## 二、代际曲线")
    out.append("")
    if not curve:
        out.append("（输入里没有 `curve` 数组，无法渲染曲线。）")
    else:
        out.append("| 代 | 当代分数 | 历史最优 | 存档数 | 累计非法 |")
        out.append("| --- | --- | --- | --- | --- |")
        for item in curve:
            gen_score = num(item.get("gen_score"))
            out.append(f"| {cell(item.get('gen'))} | "
                       f"{'（全非法）' if gen_score is None else cell(gen_score)} | "
                       f"{cell(item.get('best_score'))} | {cell(item.get('archive_size'))} | "
                       f"{cell(item.get('invalid_cum'))} |")
        out.append("")
        plateau = analysis["plateau_gens"]
        if plateau is None:
            out.append("- 形态判读：`best_score` 全程无可比数值，无法判断收敛形态。")
        elif plateau == 0:
            out.append("- 形态判读：**仍在爬升**，最后一代刷新了历史最优。")
        elif analysis["stalled"]:
            out.append(f"- 形态判读：**爬升后平台**，历史最优已保持 {plateau} 代未刷新。")
        else:
            out.append(f"- 形态判读：历史最优保持 {plateau} 代未刷新，尚未达到停滞阈值"
                       f"（{PLATEAU_GENS} 代）。")
        out.append(f"- 当代分数为 `null` 表示该代候选全部非法，**不是 0 分**；"
                   f"累计非法 {cell(curve[-1].get('invalid_cum'))} 条。")
    out.append("")

    out.append("## 三、存档与血统")
    out.append("")
    if not archive_block:
        out.append("（未提供 `evo_archive` 或 `evo_finalize.archive`。）")
    else:
        out.append(f"- 入档候选数：{cell(archive_block.get('n_candidates'))}")
        out.append(f"- 最优：`{cell(archive_block.get('best'))}`　"
                   f"分数 {cell(archive_block.get('best_score'))}")
        out.append(f"- 被拒候选：{cell(archive_block.get('rejected'))}")
        per_island = archive_block.get("per_island")
        if isinstance(per_island, dict) and per_island:
            islands = "，".join(f"岛 {k}：{v} 个" for k, v in sorted(per_island.items()))
            out.append(f"- 岛屿分布：{cell(islands)}")
            empty = [k for k, v in per_island.items() if not v]
            if empty and len(empty) == len(per_island):
                out.append("- ⚠️ 所有岛屿都为空，存档没有真正建立。")
            elif empty:
                out.append(f"- ⚠️ 空岛：{cell('、'.join(sorted(empty)))}（岛屿拓扑未被利用）")
        lineage = archive.get("lineage") or archive_block.get("lineage")
        if isinstance(lineage, list) and lineage:
            out.append("")
            out.append("| 候选 | 父节点 | 岛 | 分数 |")
            out.append("| --- | --- | --- | --- |")
            for node in lineage[:10]:
                if isinstance(node, dict):
                    out.append(f"| `{cell(node.get('cid') or node.get('candidate_id'))}` | "
                               f"`{cell(node.get('parent_id'))}` | {cell(node.get('island_id'))} | "
                               f"{cell(node.get('score'))} |")
    out.append("")

    out.append("## 四、闸门裁决")
    out.append("")
    verdicts = gate.get("verdicts") if isinstance(gate.get("verdicts"), dict) else {}
    out.append(f"- 裁决记录数：{cell(gate.get('n_records')) or '—'}")
    if verdicts:
        out.append("- 分布：" + "，".join(f"{k} = {v}" for k, v in sorted(verdicts.items())))
    else:
        out.append("- 分布：（未提供）")
    harmful = gate.get("harmful") or []
    if harmful:
        out.append("")
        out.append("**有害候选（必须逐条处理）：**")
        out.append("")
        for item in harmful:
            out.append(f"- `{cell(item.get('cid') if isinstance(item, dict) else item)}`"
                       f"　{cell(item.get('reason') if isinstance(item, dict) else '')}")
    else:
        out.append("- 有害候选：无。")
    out.append("")

    out.append("## 五、对抗审计")
    out.append("")
    out.append(f"- 被审计验证器：`{cell(audit.get('verifier')) or '—'}`")
    out.append(f"- 探针通过判据：`{cell(audit.get('criterion')) or '—'}`　"
               f"通过率 {cell(audit.get('probe_pass_rate')) or '—'}")
    probes = [p for p in (audit.get("probes") or []) if isinstance(p, dict)]
    if probes:
        out.append("")
        out.append("| 探针 | 分数 | 候选有效 | 是否被正确拒绝 |")
        out.append("| --- | --- | --- | --- |")
        for probe in probes:
            mark = "✅ 是" if probe.get("correctly_rejected") else "❌ 否"
            out.append(f"| `{cell(probe.get('probe'))}` | {cell(probe.get('score'))} | "
                       f"{cell(probe.get('valid'))} | {mark} |")
    collapse = audit.get("collapse") if isinstance(audit.get("collapse"), dict) else {}
    if collapse:
        out.append("")
        out.append(f"- 塌缩检测：n = {cell(collapse.get('n'))}，"
                   f"min = {cell(collapse.get('min'))}，max = {cell(collapse.get('max'))}，"
                   f"spread = **{cell(collapse.get('spread'))}**，"
                   f"判决 = **{cell(collapse.get('verdict'))}**")
        if as_text(collapse.get("note")):
            out.append(f"  - 说明：{cell(collapse.get('note'))}")
    out.append("")

    out.append("## 六、停止原因与下一步")
    out.append("")
    for title, detail, level, action in analysis["reasons"]:
        label = {"stop": "停止", "warn": "警告", "continue": "继续"}.get(level, level)
        out.append(f"- **[{label}] {title}**：{detail}")
        out.append(f"  - {action}")
    out.append("")

    out.append("## 七、口径声明")
    out.append("")
    out.append("1. **R1 外部**：分数只由确定性程序给出，本报告不含任何模型判定。")
    out.append("2. **R2 确定**：同输入同输出。本轮 `seed` 见第一节；无 seed 则本轮不可复现。")
    out.append("3. **R3 保留集**：审计与落地点检只读保留集；"
               "只有一套切分时有效性判决不成立。")
    out.append("4. 曲线形状是过程，`run_validity.verdict` 才是结论。")
    out.append(f"5. 本报告所有阈值：停滞判定 {PLATEAU_GENS} 代，代理落差阈值 {max_gap}。")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# self test
# --------------------------------------------------------------------------
SELFTEST_STATUS = {
    "run_id": "run-selftest-001", "domain": "ontology-growth", "seed": 20261011,
    "budget": {"evals": 10, "used": 10, "wall_s": 0.09},
    "best": {"cid": "c-0003-134628", "score": 1.0, "cmp": 0.908},
    "curve": [
        {"gen": 0, "gen_score": 0.3304, "best_score": 0.3304, "archive_size": 1, "invalid_cum": 0},
        {"gen": 1, "gen_score": 0.6969, "best_score": 0.6969, "archive_size": 2, "invalid_cum": 0},
        {"gen": 2, "gen_score": 0.7818, "best_score": 0.7818, "archive_size": 3, "invalid_cum": 0},
        {"gen": 3, "gen_score": 1.0, "best_score": 1.0, "archive_size": 4, "invalid_cum": 0},
        {"gen": 4, "gen_score": 0.8, "best_score": 1.0, "archive_size": 5, "invalid_cum": 0},
        {"gen": 5, "gen_score": None, "best_score": 1.0, "archive_size": 5, "invalid_cum": 1},
    ],
}
SELFTEST_FINAL = {
    "run_id": "run-selftest-001", "domain": "ontology-growth", "label": "selftest",
    "generations_run": 6, "budget": {"evals": 10, "invalid": 1, "wall_s": 0.09},
    "best": {"cid": "c-0003-134628", "score": 1.0, "cmp": 0.908},
    "archive": {"n_candidates": 5, "best": "c-0003-134628", "best_score": 1.0,
                "per_island": {"0": 5, "1": 0}, "rejected": 0},
    "gate": {"n_records": 5, "harmful": [], "verdicts": {"neutral": 5}},
    "audit": {"verifier": "ruleset-f1-verifier",
              "probes": [{"probe": "select-all", "score": 0.666667, "valid": True,
                          "correctly_rejected": True}],
              "collapse": {"n": 5, "min": 0.3304, "max": 1.0, "spread": 0.669643,
                           "verdict": "ok", "note": "分数无区分度 → 验证器可能已塌缩"},
              "criterion": "probe < best", "probe_pass_rate": 1.0},
    "run_validity": {"verdict": "ok", "best_score": 1.0, "worst_probe_score": 0.6667,
                     "margin": 0.3333},
    "proxy_reality_gap": 0.0,
}
SELFTEST_ARCHIVE = {"n_candidates": 5, "best": "c-0003-134628", "best_score": 1.0,
                    "per_island": {"0": 5, "1": 0}, "rejected": 0}


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def emit(paths: list, message: str) -> int:
    artifacts = []
    for path in paths:                       # written first, stat second
        artifacts.append({
            "kind": "file",
            "absolute_path": str(path.resolve()),
            "file_name": path.name,
            "mime_type": MIME_CSV if path.suffix == ".csv" else MIME_MD,
            "file_size_bytes": path.stat().st_size,
        })
    print_contract({"status": "success", "artifacts": artifacts, "message": message})
    return 0


def fail(message: str) -> int:
    print_contract({"status": "error", "error": message})
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render an evolution run report and its generation curve."
    )
    parser.add_argument("--status", help="Path to the evo_status JSON payload.")
    parser.add_argument("--final", help="Path to the evo_finalize JSON payload.")
    parser.add_argument("--archive", help="Path to the evo_archive JSON payload.")
    parser.add_argument("--seed", default="", help="The seed used for evo_init.")
    parser.add_argument("--max-gap", type=float, default=DEFAULT_MAX_GAP,
                        help="Proxy-vs-reality gap threshold (default 0.1).")
    parser.add_argument("--out", default=DEFAULT_MD, help="Output Markdown file name.")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="Output CSV file name.")
    parser.add_argument("--selftest", action="store_true",
                        help="Run a built-in fixture; used by skills/_validate.py.")
    args = parser.parse_args()

    if args.selftest:
        tmp = Path(tempfile.mkdtemp())
        return run(SELFTEST_STATUS, SELFTEST_FINAL, SELFTEST_ARCHIVE, args.seed,
                   args.max_gap, tmp / args.out, tmp / args.csv, "(selftest) ")

    if not args.status and not args.final:
        return fail("at least one of --status / --final is required (or use --selftest)")
    status = load_json(args.status)
    final = load_json(args.final)
    if not status and not final:
        return fail("neither --status nor --final could be read as a JSON object")
    return run(status, final, load_json(args.archive), args.seed, args.max_gap,
               Path(args.out), Path(args.csv), "")


def run(status: dict, final: dict, archive: dict, seed: str, max_gap: float,
        md_path: Path, csv_path: Path, tag: str) -> int:
    curve = [c for c in (status.get("curve") or final.get("curve") or []) if isinstance(c, dict)]
    if not curve:
        return fail("no curve found: pass the evo_status payload containing a 'curve' array")

    analysis = analyse(curve, final, max_gap)
    report = render_markdown(status, final, archive, seed, analysis, max_gap)

    rows = []
    for item in curve:
        gen_score = num(item.get("gen_score"))
        rows.append({
            "gen": as_text(item.get("gen")),
            "gen_score": "" if gen_score is None else gen_score,
            "best_score": as_text(item.get("best_score")),
            "archive_size": as_text(item.get("archive_size")),
            "invalid_cum": as_text(item.get("invalid_cum")),
        })

    md_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report, encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:   # write both ...
        writer = csv.DictWriter(handle,
                                fieldnames=["gen", "gen_score", "best_score",
                                            "archive_size", "invalid_cum"])
        writer.writeheader()
        writer.writerows(rows)

    level = analysis["level"]
    headline = analysis["reasons"][0][0] if analysis["reasons"] else "无判读"
    return emit([md_path, csv_path],                                  # ... then stat both
                f"{tag}进化报告已生成：{analysis['generations']} 代，"
                f"判决 {level}（{headline}）")


if __name__ == "__main__":
    sys.exit(main())
