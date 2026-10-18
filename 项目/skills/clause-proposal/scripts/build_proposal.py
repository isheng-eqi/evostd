#!/usr/bin/env python3
"""Validate drafted clause candidates and emit a submittable proposal package.

Input : one JSON file assembled by the agent (see SKILL.md step 5), holding the
        asset_blindspots payload, the std_hard_anchors payload, the
        std_clause_list payload, the ablation evidence and the drafted
        candidates.
Output: ``<stem>.json`` (machine submittable) + ``<stem>.md`` (human readable),
        both declared in SKILL.md ``script_outputs``.

stdout carries exactly one line -- the Nexent artifact contract JSON. Nothing
else may be printed to stdout.

Determinism: no clock, no randomness, no network, no model calls.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

MIME_JSON = "application/json"
MIME_MD = "text/markdown"
DEFAULT_STEM = "clause-proposal"
GRAMMAR_EXTENSION = "__grammar_extension__"

# kind -> required params keys
REQUIRED_PARAMS = {
    "格式约束": ("field", "pattern"),
    "受控词表": ("field", "allowed"),
    "必填约束": ("field",),
    "条件必填": ("when_field", "when_value", "require_field"),
    "数值范围": ("field",),
    "跨字段一致性": ("left", "right", "op"),
    "跨文档引用": ("field", "registry"),
    "禁止取值": ("field", "registry"),
}
CONSISTENCY_OPS = ("<=", ">=", "<", ">", "==")
REGISTRY_KINDS = ("跨文档引用", "禁止取值")
FIELD_KINDS = ("格式约束", "受控词表", "必填约束", "数值范围", "跨文档引用", "禁止取值")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
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


def print_contract(payload: dict) -> None:
    """Print one JSON line; fall back to ASCII escapes if stdout cannot encode it."""
    line = json.dumps(payload, ensure_ascii=False)
    try:
        print(line)
    except UnicodeEncodeError:
        print(json.dumps(payload, ensure_ascii=True))


def ident_of(item: object, prefix: str, index: int, keys: tuple) -> str:
    if isinstance(item, dict):
        for key in keys:
            value = as_text(item.get(key))
            if value:
                return value
    return f"{prefix}-{index + 1:03d}"


def constraint_target(kind: str, params: dict) -> str:
    """The constraint surface used for duplicate detection."""
    if kind in FIELD_KINDS:
        return as_text(params.get("field"))
    if kind == "条件必填":
        return (f"{as_text(params.get('when_field'))}="
                f"{as_text(params.get('when_value'))}"
                f"->{as_text(params.get('require_field'))}")
    if kind == "跨字段一致性":
        pair = "|".join(sorted([as_text(params.get("left")), as_text(params.get("right"))]))
        return f"{pair}{as_text(params.get('op'))}"
    return ""


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------
def validate(candidate: dict, blindspot_ids: set, anchor_ids: set,
             existing: list, existing_ids: set, registries: dict,
             ablations: dict) -> tuple[list, dict]:
    """Return (failures, normalized_candidate)."""
    failures: list[str] = []
    kind = as_text(candidate.get("kind"))
    params = candidate.get("params")
    params = params if isinstance(params, dict) else {}

    clause_id = as_text(candidate.get("clause_id"))
    if not clause_id:
        failures.append("clause_id 缺失")
    elif clause_id in existing_ids:
        failures.append(f"clause_id 与现有判据冲突：{clause_id}")

    if not as_text(candidate.get("title")):
        failures.append("title 缺失")

    # --- grammar extension proposal: params are intentionally not applicable ---
    submit_as = "clause"
    if kind == GRAMMAR_EXTENSION:
        submit_as = "grammar_extension"
        if len(as_text(candidate.get("rationale"))) < 20:
            failures.append("判据语言扩张提案必须写清 rationale（现有类型为什么表达不了）")
    elif kind not in REQUIRED_PARAMS:
        failures.append(f"kind 不在八类判据内：{kind or '（空）'}")

    if submit_as == "clause" and kind in REQUIRED_PARAMS:
        for key in REQUIRED_PARAMS[kind]:
            if key not in params or params.get(key) in (None, "", []):
                failures.append(f"params.{key} 缺失")

        if kind == "格式约束" and params.get("pattern"):
            try:
                re.compile(as_text(params["pattern"]))
            except re.error as exc:
                failures.append(f"params.pattern 无法编译：{exc}")
        if kind == "受控词表" and params.get("allowed") is not None:
            allowed = params.get("allowed")
            if not isinstance(allowed, list) or not allowed:
                failures.append("params.allowed 必须是非空数组")
        if kind == "数值范围":
            lo, hi = params.get("min"), params.get("max")
            if lo is None and hi is None:
                failures.append("params.min 与 params.max 至少要有一个")
            for name, value in (("min", lo), ("max", hi)):
                if value is not None and not isinstance(value, (int, float)):
                    failures.append(f"params.{name} 必须是数值")
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo > hi:
                failures.append("params.min 不得大于 params.max")
        if kind == "条件必填":
            if as_text(params.get("when_field")) == as_text(params.get("require_field")):
                failures.append("when_field 与 require_field 不得相同")
        if kind == "跨字段一致性":
            if as_text(params.get("left")) == as_text(params.get("right")):
                failures.append("left 与 right 不得相同")
            if params.get("op") not in CONSISTENCY_OPS:
                failures.append(f"params.op 必须是 {list(CONSISTENCY_OPS)} 之一")
        if kind in REGISTRY_KINDS:
            registry = as_text(params.get("registry"))
            if registry and registry not in registries:
                failures.append(f"params.registry 未在已声明清单中：{registry}")

        target = constraint_target(kind, params)
        for clause in existing:
            if not isinstance(clause, dict):
                continue
            old_kind = as_text(clause.get("kind"))
            old_params = clause.get("params") if isinstance(clause.get("params"), dict) else {}
            if old_kind == kind and constraint_target(old_kind, old_params) == target and target:
                failures.append(
                    f"与现有判据重复：{as_text(clause.get('clause_id'))}"
                    f"（同类型 {kind}，同约束面 {target}）"
                )
                break

    # --- blindspot evidence ---
    ids = [as_text(i) for i in (candidate.get("blindspot_ids") or []) if as_text(i)]
    if not ids:
        failures.append("blindspot_ids 为空：候选必须对应至少一条已归集的盲区")
    for item in ids:
        if item not in blindspot_ids:
            failures.append(f"blindspot_ids 引用了未归集的盲区：{item}")
            continue
        evidence = ablations.get(item)
        if not isinstance(evidence, dict):
            failures.append(f"盲区 {item} 缺少实测消融证据（ablations）")
            continue
        violations = evidence.get("violations")
        if violations is None:
            failures.append(f"盲区 {item} 的消融证据缺 violations 字段")
        elif violations != 0:
            failures.append(
                f"盲区 {item} 实测未通过：现行判据已报出 {violations} 条违例，它不是盲区"
            )
        if not evidence.get("snapshot_id"):
            failures.append(f"盲区 {item} 的消融证据缺 snapshot_id，结论不可复算")

    # --- free-truth support ---
    anchors = [as_text(a) for a in (candidate.get("anchor_ids") or []) if as_text(a)]
    samples = [as_text(s) for s in (candidate.get("compliant_samples") or []) if as_text(s)]
    for item in anchors:
        if item not in anchor_ids:
            failures.append(f"anchor_ids 引用了不存在的硬锚点：{item}")
    if not anchors and not samples:
        failures.append("缺少支撑：既没有硬锚点，也没有合规样本（见过合规样本才准采纳）")

    if not as_text(candidate.get("rationale")):
        failures.append("rationale 缺失")
    if not as_text(candidate.get("expected_effect")):
        failures.append("expected_effect 缺失：裁决后必须能用 std_verify 复算")

    normalized = {
        "clause_id": clause_id,
        "title": as_text(candidate.get("title")),
        "kind": kind,
        "params": params,
        "standard": as_text(candidate.get("standard")),
        "blindspot_ids": ids,
        "anchor_ids": anchors,
        "compliant_samples": samples,
        "rationale": as_text(candidate.get("rationale")),
        "expected_effect": as_text(candidate.get("expected_effect")),
        "submit_as": submit_as,
        "checks": {"passed": not failures, "failures": failures},
    }
    return failures, normalized


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
def render_markdown(data: dict, blindspot_rows: list, anchor_rows: list,
                    results: list, submittable: list, blocked: list) -> str:
    out: list[str] = []
    out.append("# 候选判据提案书")
    out.append("")
    out.append("> 由 `scripts/build_proposal.py` 确定性生成。**判定权不在本流程**："
               "采纳与否由 MCP 侧裁决给出。")
    out.append("")
    out.append(f"- 标准：{cell(data.get('standard')) or '（未标注）'}")
    out.append(f"- 判据库快照：`{cell(data.get('snapshot_id')) or '（未提供）'}`")
    out.append(f"- 归集盲区：{len(blindspot_rows)} 条　起草候选：{len(results)} 条　"
               f"可提交：{len(submittable)} 条　未准入：{len(blocked)} 条")
    out.append("")

    out.append("## 一、盲区与实测结论")
    out.append("")
    out.append("「放行」只有在写明判据条数与判据库快照时才算证据。")
    out.append("")
    out.append("| 盲区 | 现象 | 样本数 | 现行判据数 | 实测违例 | 是否真盲区 |")
    out.append("| --- | --- | --- | --- | --- | --- |")
    for row in blindspot_rows:
        verdict = "✅ 是" if row["violations"] == 0 and row["has_evidence"] else "❌ 否 / 证据不足"
        out.append(f"| `{cell(row['id'])}` | {cell(row['label'])} | {row['n_assets']} | "
                   f"{cell(row['checked_clauses'])} | {cell(row['violations'])} | {verdict} |")
    if not blindspot_rows:
        out.append("| — | （未归集到盲区） | 0 | — | — | — |")
    out.append("")

    out.append("## 二、硬锚点支撑")
    out.append("")
    out.append("硬锚点是标准文本里机械可判定的部分，零人工、无争议。")
    out.append("")
    out.append("| 锚点 | 来源条款 | 摘要 |")
    out.append("| --- | --- | --- |")
    for row in anchor_rows:
        out.append(f"| `{cell(row['id'])}` | {cell(row['source'])} | {cell(row['label'])} |")
    if not anchor_rows:
        out.append("| — | — | （未取到硬锚点） |")
    out.append("")

    out.append("## 三、候选判据逐条校验")
    out.append("")
    for index, item in enumerate(results, start=1):
        checks = item["checks"]
        mark = "✅ 通过" if checks["passed"] else "❌ 未准入"
        out.append(f"### 3.{index} {item['clause_id']}　{item['title']}　（{item['kind']}）{mark}")
        out.append("")
        out.append(f"- 参数：`{json.dumps(item['params'], ensure_ascii=False, sort_keys=True)}`")
        out.append(f"- 支撑盲区：{cell('、'.join(item['blindspot_ids'])) or '（无）'}")
        out.append(f"- 硬锚点：{cell('、'.join(item['anchor_ids'])) or '（无）'}")
        out.append(f"- 合规样本：{cell('、'.join(item['compliant_samples'])) or '（无）'}")
        out.append(f"- 提交形态：`{item['submit_as']}`")
        out.append(f"- 理由：{cell(item['rationale']) or '（缺失）'}")
        out.append(f"- 预期效果：{cell(item['expected_effect']) or '（缺失）'}")
        if checks["failures"]:
            out.append("- **未通过原因**：")
            for reason in checks["failures"]:
                out.append(f"  - {reason}")
        out.append("")

    out.append("## 四、提交清单")
    out.append("")
    if submittable:
        out.append("下列候选通过了全部静态校验，可逐条调用 `std_propose`：")
        out.append("")
        for item in submittable:
            out.append(f"- `{cell(item['clause_id'])}`　{item['title']}　（{item['kind']}）")
    else:
        out.append("**没有候选通过静态校验。** 不要提交任何内容；先按第三节的失败原因补齐证据。")
    out.append("")
    if blocked:
        out.append("未准入（不得提交）：")
        out.append("")
        for item in blocked:
            out.append(f"- `{cell(item['clause_id'])}`　{item['title']}　"
                       f"原因：{cell('；'.join(item['checks']['failures']))}")
        out.append("")

    out.append("## 五、下一步")
    out.append("")
    out.append("1. 用 `clause-proposal.json` 里 `submittable` 数组的条目逐条调用 `std_propose(spec=...)`。")
    out.append("2. 记录返回的 `adopted` / `rejected` 与理由，**原样**引用，不做解释性改写。")
    out.append("3. 被拒时按 SKILL.md 第七节的分流表处理：表达不了→提判据语言扩张；"
               "证据不足→补锚点；重复→丢弃；误纳→收紧参数。")
    out.append("4. 用 `std_ledger(limit=20)` 确认提案与裁决已落账。")
    out.append("5. 采纳后用 `std_verify` 复算 `expected_effect`，确认判据行为与预期一致。")
    out.append("")

    out.append("## 六、口径声明")
    out.append("")
    out.append("1. 本文件不包含任何「这条判据是对的」的结论；那是裁决的事。")
    out.append("2. 盲区成立与否只由 `violations == 0` 判定，不由措辞或直觉判定。")
    out.append("3. 所有参数都必须能指向一条锚点或一句条款原文，指向不了的参数视为猜测。")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# self test
# --------------------------------------------------------------------------
SELFTEST = {
    "standard": "工业数据资产登记与校验规范（演示样例）",
    "snapshot_id": "clause-db-selftest",
    "blindspots": [
        {"id": "BS-001", "label": "资产标识已注销仍被引用", "n_assets": 2},
        {"id": "BS-002", "label": "模态词表外取值", "n_assets": 3},
    ],
    "ablations": {
        "BS-001": {"assets": ["AST-2026-0031", "AST-2026-0032"], "checked_clauses": 12,
                   "violations": 0, "snapshot_id": "clause-db-selftest"},
        "BS-002": {"assets": ["AST-2026-0041"], "checked_clauses": 12,
                   "violations": 1, "snapshot_id": "clause-db-selftest"},
    },
    "anchors": [
        {"id": "AN-007", "source": "《演示样例》附录A", "label": "retired_assets 共 3 个条目"},
    ],
    "registries": {"retired_assets": ["AST-2019-0007"], "controlled_standards": ["GB/T 33863.1-2017"]},
    "existing_clauses": [
        {"clause_id": "第6条", "kind": "受控词表",
         "params": {"field": "modality", "allowed": ["文本", "表格", "图文", "影像"]}},
    ],
    "candidates": [
        {"clause_id": "第17条", "title": "资产标识不得指向已注销条目", "kind": "禁止取值",
         "params": {"field": "asset_id", "registry": "retired_assets"},
         "standard": "工业数据资产登记与校验规范（演示样例）",
         "blindspot_ids": ["BS-001"], "anchor_ids": ["AN-007"],
         "compliant_samples": ["AST-2026-0007"],
         "rationale": "白名单只能表达『取值必须在清单内』；本盲区要求『取值不得在已注销集合内』，"
                      "方向相反，必须用黑名单类型。锚点 AN-007 给出 retired_assets 条目。",
         "expected_effect": "对 BS-001 的 2 条样本产生 2 条违例；对现有 5 条合规样本产生 0 条违例"},
        {"clause_id": "第18条", "title": "模态仍在词表内（实测证伪）", "kind": "受控词表",
         "params": {"field": "modality", "allowed": ["文本", "表格"]},
         "blindspot_ids": ["BS-002"], "anchor_ids": ["AN-007"],
         "rationale": "该候选用于演示实测证伪时脚本如何拒绝。",
         "expected_effect": "无"},
    ],
}


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
            "mime_type": MIME_JSON if path.suffix == ".json" else MIME_MD,
            "file_size_bytes": path.stat().st_size,
        })
    print_contract({"status": "success", "artifacts": artifacts, "message": message})
    return 0


def fail(message: str) -> int:
    print_contract({"status": "error", "error": message})
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate drafted clause candidates and emit a proposal package."
    )
    parser.add_argument("--in", dest="infile", help="Path to proposal-input.json.")
    parser.add_argument("--out", default=DEFAULT_STEM, help="Output file stem.")
    parser.add_argument("--selftest", action="store_true",
                        help="Run a built-in fixture; used by skills/_validate.py.")
    args = parser.parse_args()

    if args.selftest:
        return run(SELFTEST, Path(tempfile.mkdtemp()) / args.out, "(selftest) ")

    if not args.infile:
        return fail("--in is required (or use --selftest)")
    src = Path(args.infile)
    if not src.is_file():
        return fail(f"input not found: {args.infile}")
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return fail(f"invalid input JSON: {exc}")
    if not isinstance(data, dict):
        return fail("input JSON must be an object")
    return run(data, Path(args.out), "")


def run(data: dict, stem: Path, tag: str) -> int:
    blindspots = [b for b in (data.get("blindspots") or []) if isinstance(b, dict)]
    anchors = [a for a in (data.get("anchors") or []) if isinstance(a, dict)]
    existing = [c for c in (data.get("existing_clauses") or []) if isinstance(c, dict)]
    candidates = [c for c in (data.get("candidates") or []) if isinstance(c, dict)]
    ablations = data.get("ablations") if isinstance(data.get("ablations"), dict) else {}
    registries = data.get("registries") if isinstance(data.get("registries"), dict) else {}

    if not candidates:
        return fail("no candidates in input: nothing to validate")

    blindspot_ids = {
        ident_of(b, "BS", i, ("id", "blindspot_id", "bid")) for i, b in enumerate(blindspots)
    }
    anchor_ids = {ident_of(a, "AN", i, ("id", "anchor_id", "aid")) for i, a in enumerate(anchors)}
    existing_ids = {as_text(c.get("clause_id")) for c in existing}

    results = []
    for candidate in candidates:
        _, normalized = validate(candidate, blindspot_ids, anchor_ids, existing,
                                 existing_ids, registries, ablations)
        results.append(normalized)

    submittable = [r for r in results if r["checks"]["passed"]]
    blocked = [r for r in results if not r["checks"]["passed"]]

    blindspot_rows = []
    for index, item in enumerate(blindspots):
        bid = ident_of(item, "BS", index, ("id", "blindspot_id", "bid"))
        evidence = ablations.get(bid) if isinstance(ablations.get(bid), dict) else {}
        blindspot_rows.append({
            "id": bid,
            "label": as_text(item.get("label") or item.get("term") or item.get("description")
                             or item.get("name")),
            "n_assets": len(evidence.get("assets") or []) if evidence else
                        len(item.get("assets") or []) or as_text(item.get("n_assets")) or "0",
            "checked_clauses": as_text(evidence.get("checked_clauses")) or "—",
            "violations": evidence.get("violations") if evidence else "—",
            "has_evidence": bool(evidence),
        })

    anchor_rows = []
    for index, item in enumerate(anchors):
        anchor_rows.append({
            "id": ident_of(item, "AN", index, ("id", "anchor_id", "aid")),
            "source": as_text(item.get("source") or item.get("clause_id")),
            "label": as_text(item.get("label") or item.get("sample") or item.get("text")
                             or item.get("value")),
        })

    package = {
        "generated_by": "scripts/build_proposal.py",
        "standard": as_text(data.get("standard")),
        "snapshot_id": as_text(data.get("snapshot_id")),
        "counts": {"blindspots": len(blindspots), "candidates": len(results),
                   "submittable": len(submittable), "blocked": len(blocked)},
        "submittable": [r["clause_id"] for r in submittable],
        "candidates": results,
    }
    report = render_markdown(data, blindspot_rows, anchor_rows, results, submittable, blocked)

    json_path = stem.with_suffix(".json")
    md_path = stem.with_suffix(".md")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(report, encoding="utf-8")            # write both first ...

    return emit([json_path, md_path],                         # ... then stat both
                f"{tag}提案包已生成：{len(submittable)} 条可提交，{len(blocked)} 条未准入")


if __name__ == "__main__":
    sys.exit(main())
