#!/usr/bin/env python3
"""Build the six-dimension onboarding card and gate the asset ingest.

Input : one JSON file assembled by the agent (see SKILL.md step 5.1), holding
        the six-dimension draft, the ``std_hard_anchors`` payload, the clause
        list, the ``std_verify`` payload, optional waivers and (after a
        successful ingest) the ``asset_ingest`` payload.
Output: ``<stem>.json`` and ``<stem>.md``, both declared in SKILL.md
        ``script_outputs``.

The authoritative verdict is ``std_verify``. This script re-runs the mechanical
checks (controlled vocabulary / range / pattern / required / consistency) as an
independent second pair of eyes, and reports any disagreement instead of hiding
it.

stdout carries exactly one line -- the Nexent artifact contract JSON. Nothing
else may be printed to stdout.

Determinism: no clock, no randomness, no network, no model calls. No field value
is ever invented; missing values stay missing.
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
DEFAULT_STEM = "onboarding-card"

# Field -> (dimension id, label, default controlled vocabulary / range / pattern).
# Defaults mirror the compiled demo standard; the clause list overrides them
# whenever a matching clause is present.
DIMENSIONS = [
    ("D1", "身份维", [
        ("asset_id", "资产标识", {"pattern": r"[A-Za-z0-9\-]{8,64}", "generated": True}),
        ("cited_standards", "引用标准", {"registry": "controlled_standards"}),
    ]),
    ("D2", "形态维", [
        ("modality", "模态", {"enum": ["文本", "表格", "图文", "影像"]}),
        ("annotation_note", "标注说明", {"conditional_by": "modality", "when": "影像"}),
    ]),
    ("D3", "权限维", [
        ("secrecy", "密级", {"enum": ["公开", "内部", "秘密"]}),
        ("declassify_date", "解密日期", {"conditional_by": "secrecy", "when": "秘密"}),
    ]),
    ("D4", "归属维", [
        ("owner_dept", "责任部门", {"required": True}),
        ("storage_uri", "存储位置", {"required": True}),
    ]),
    ("D5", "时序维", [
        ("collect_time", "采集时间", {"consistency": ("collect_time", "ingest_time", "<=")}),
        ("ingest_time", "入库时间", {"consistency": ("collect_time", "ingest_time", "<=")}),
    ]),
    ("D6", "质量维", [
        ("quality_score", "质量评分", {"range": (0, 100)}),
        ("completeness", "字段完整度", {"range": (0.0, 1.0)}),
        ("file_hash", "文件哈希", {"pattern": r"[0-9a-f]{64}"}),
    ]),
]
CLAUSE_FOR_FIELD = {
    "asset_id": "第5条", "modality": "第6条", "secrecy": "第7条", "owner_dept": "第8条",
    "declassify_date": "第9条", "annotation_note": "第10条", "collect_time": "第11条",
    "ingest_time": "第11条", "quality_score": "第12条", "completeness": "第13条",
    "file_hash": "第14条", "cited_standards": "第15条", "storage_uri": "第16条",
}


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
    line = json.dumps(payload, ensure_ascii=False)
    try:
        print(line)
    except UnicodeEncodeError:
        print(json.dumps(payload, ensure_ascii=True))


def is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def clause_by_field(clauses: list, field: str) -> dict:
    """Find the clause that constrains ``field`` (by params.field or require_field)."""
    for clause in clauses:
        params = clause.get("params") if isinstance(clause.get("params"), dict) else {}
        if as_text(params.get("field")) == field:
            return clause
        if as_text(params.get("require_field")) == field:
            return clause
    return {}


def spec_from_clause(clause: dict, fallback: dict) -> dict:
    """Overlay the compiled clause parameters onto the default spec."""
    params = clause.get("params") if isinstance(clause.get("params"), dict) else {}
    spec = dict(fallback)
    kind = as_text(clause.get("kind"))
    if kind == "受控词表" and isinstance(params.get("allowed"), list) and params["allowed"]:
        spec["enum"] = params["allowed"]
    if kind == "格式约束" and as_text(params.get("pattern")):
        spec["pattern"] = as_text(params["pattern"])
    if kind == "数值范围" and (params.get("min") is not None or params.get("max") is not None):
        spec["range"] = (params.get("min"), params.get("max"))
    if kind == "条件必填":
        spec.pop("enum", None)
        spec.pop("required", None)
        spec["conditional_by"] = as_text(params.get("when_field"))
        spec["when"] = as_text(params.get("when_value"))
    if kind == "必填约束":
        spec["required"] = True
    return spec


def try_date(value: object):
    """Return an ISO-ish comparable string for YYYY-MM-DD / YYYY-MM-DDTHH:MM:SS."""
    text = as_text(value).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?", text):
        return None
    return text.replace(" ", "T")


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------
def run_checks(draft: dict, clauses: list, ingest: dict) -> tuple[list, list]:
    """Return (dimension_results, self_checks).

    Each dimension result: {id, name, status, fields:[{key,label,value,status,clause}]}
    Each self check:       {name, field, expected, actual, ok}
    """
    generated_id = as_text(ingest.get("asset_id")) or as_text(draft.get("asset_id"))
    dimensions: list = []
    checks: list = []

    for dim_id, dim_name, fields in DIMENSIONS:
        field_rows = []
        statuses = []
        for key, label, fallback in fields:
            clause = clause_by_field(clauses, key)
            spec = spec_from_clause(clause, fallback)
            value = generated_id if (key == "asset_id" and generated_id) else draft.get(key)
            clause_ref = as_text(clause.get("source")) or CLAUSE_FOR_FIELD.get(key, "")
            status = "ok"

            # ---- required / conditional ----
            conditional_by = spec.get("conditional_by")
            if conditional_by:
                trigger_value = draft.get(conditional_by)
                applies = as_text(trigger_value) == as_text(spec.get("when"))
                if not applies:
                    status = "not_applicable"
                elif is_blank(value):
                    status = "missing"
                    checks.append({
                        "name": "条件必填", "field": key,
                        "expected": f"{conditional_by} = {as_text(spec.get('when'))} 时必填",
                        "actual": "空", "ok": False,
                    })
            elif key == "asset_id" and not generated_id:
                status = "pending_ingest"
            elif spec.get("required") or key in ("owner_dept", "storage_uri"):
                if is_blank(value):
                    status = "missing"
                    checks.append({"name": "必填约束", "field": key,
                                   "expected": "存在且非空", "actual": "空", "ok": False})

            # ---- controlled vocabulary ----
            if status in ("ok", "not_applicable") and spec.get("enum") and not is_blank(value):
                allowed = [as_text(a) for a in spec["enum"]]
                if as_text(value) not in allowed:
                    status = "invalid"
                    checks.append({"name": "受控词表", "field": key,
                                   "expected": "、".join(allowed),
                                   "actual": as_text(value), "ok": False})

            # ---- pattern ----
            if status in ("ok", "not_applicable") and spec.get("pattern") and not is_blank(value):
                pattern = as_text(spec["pattern"])
                if not re.fullmatch(pattern, as_text(value)):
                    status = "invalid"
                    checks.append({"name": "格式约束", "field": key,
                                   "expected": pattern, "actual": as_text(value), "ok": False})

            # ---- numeric range ----
            if status in ("ok", "not_applicable") and spec.get("range") and not is_blank(value):
                low, high = spec["range"]
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    status = "invalid"
                    checks.append({"name": "数值范围", "field": key,
                                   "expected": f"数值 {low} ~ {high}",
                                   "actual": f"{type(value).__name__}: {as_text(value)}", "ok": False})
                else:
                    bad = (low is not None and value < low) or (high is not None and value > high)
                    if bad:
                        status = "invalid"
                        checks.append({"name": "数值范围", "field": key,
                                       "expected": f"{low} ~ {high}", "actual": as_text(value),
                                       "ok": False})

            field_rows.append({"key": key, "label": label, "value": as_text(value) or "（空）",
                               "status": status, "clause": clause_ref})
            statuses.append(status)

        if "missing" in statuses or "invalid" in statuses:
            dim_status = "invalid" if "invalid" in statuses else "missing"
        elif all(s == "not_applicable" for s in statuses):
            dim_status = "not_applicable"
        elif "pending_ingest" in statuses:
            dim_status = "pending_ingest"
        else:
            dim_status = "ok"
        dimensions.append({"id": dim_id, "name": dim_name, "status": dim_status,
                           "fields": field_rows})

    # ---- cross-field consistency ----
    left, right, op = "collect_time", "ingest_time", "<="
    for clause in clauses:
        params = clause.get("params") if isinstance(clause.get("params"), dict) else {}
        if as_text(params.get("op")):
            left = as_text(params.get("left")) or left
            right = as_text(params.get("right")) or right
            op = as_text(params.get("op"))
            break
    a, b = draft.get(left), draft.get(right)
    if not is_blank(a) and not is_blank(b):
        ca, cb = try_date(a), try_date(b)
        if ca is None or cb is None:
            checks.append({"name": "跨字段一致性", "field": f"{left}/{right}",
                           "expected": f"{left} {op} {right}（可比较的 ISO 日期）",
                           "actual": f"{as_text(a)} / {as_text(b)}", "ok": False})
        else:
            ok = {"<=": ca <= cb, ">=": ca >= cb, "<": ca < cb, ">": ca > cb,
                  "==": ca == cb}.get(op, True)
            checks.append({"name": "跨字段一致性", "field": f"{left}/{right}",
                           "expected": f"{left} {op} {right}",
                           "actual": f"{as_text(a)} {op} {as_text(b)}", "ok": ok})
    return dimensions, checks


def collect_violations(verify: dict) -> list:
    items: list = []
    if not isinstance(verify, dict):
        return items
    results = verify.get("results")
    if isinstance(results, list):
        for row in results:
            if not isinstance(row, dict):
                continue
            for finding in row.get("findings") or []:
                if isinstance(finding, dict) and not finding.get("ok", False):
                    items.append({
                        "asset_id": as_text(row.get("asset_id")),
                        "clause_id": as_text(finding.get("clause_id")),
                        "source": as_text(finding.get("source")),
                        "message": as_text(finding.get("message")),
                        "evidence": finding.get("evidence") or {},
                    })
    for finding in verify.get("violations") or []:
        if isinstance(finding, dict):
            items.append({
                "asset_id": as_text(finding.get("asset_id")),
                "clause_id": as_text(finding.get("clause_id")),
                "source": as_text(finding.get("source")),
                "message": as_text(finding.get("message")),
                "evidence": finding.get("evidence") or {},
            })
    return items


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
STATUS_LABEL = {
    "ok": "✅ 合格", "missing": "❌ 缺值", "invalid": "❌ 不合规",
    "not_applicable": "— 不适用", "pending_ingest": "⏳ 待入库生成",
}


def render_markdown(card: dict) -> str:
    out: list[str] = []
    out.append("# 数据资产入库卡")
    out.append("")
    out.append("> 由 `scripts/build_onboarding_card.py` 确定性生成。"
               "判定以 `std_verify` 为准；脚本自身只做机械自查，二者不一致时会显式列出。")
    out.append("")

    decision = card["decision"]
    out.append("## 一、入库结论")
    out.append("")
    out.append(f"### {decision['status'].upper()}")
    out.append("")
    for reason in decision["reasons"]:
        out.append(f"- {reason}")
    if decision["waived"]:
        out.append("")
        out.append("**已豁免的违例（必须留痕）：**")
        out.append("")
        out.append("| 条款 | 理由 | 批准人 |")
        out.append("| --- | --- | --- |")
        for item in decision["waived"]:
            out.append(f"| {cell(item.get('clause_id'))} | {cell(item.get('reason'))} | "
                       f"{cell(item.get('approved_by')) or '**（缺批准人）**'} |")
    out.append("")

    out.append("## 二、资产概览")
    out.append("")
    asset = card["asset"]
    out.append(f"- 资产标识：`{cell(asset.get('asset_id')) or '（待入库生成）'}`")
    out.append(f"- 模态：{cell(asset.get('modality')) or '（空）'}")
    out.append(f"- 模态判定依据：{cell(asset.get('modality_source')) or '（未提供）'}")
    out.append(f"- 密级：{cell(asset.get('secrecy')) or '（空）'}")
    out.append(f"- 责任部门：{cell(asset.get('owner_dept')) or '（空）'}")
    out.append(f"- 存储位置：`{cell(asset.get('storage_uri')) or '（空）'}`")
    out.append("")

    out.append("## 三、六维资产标识")
    out.append("")
    for dim in card["dimensions"]:
        out.append(f"### {dim['id']} {dim['name']}　{STATUS_LABEL.get(dim['status'], dim['status'])}")
        out.append("")
        out.append("| 字段 | 值 | 状态 | 依据条款 |")
        out.append("| --- | --- | --- | --- |")
        for row in dim["fields"]:
            out.append(f"| `{cell(row['key'])}` | {cell(row['value'])} | "
                       f"{STATUS_LABEL.get(row['status'], row['status'])} | {cell(row['clause'])} |")
        out.append("")

    out.append("## 四、硬锚点自查")
    out.append("")
    anchors = card["anchors"]
    out.append(f"- 取到硬锚点：{anchors['total']} 条")
    if anchors["by_clause"]:
        out.append("- 覆盖条款：" + "，".join(f"{k}（{v} 条）"
                                              for k, v in sorted(anchors["by_clause"].items())))
    else:
        out.append("- ⚠️ **未取到任何硬锚点。** 该条款类型还没有机械锚点，"
                   "此时不能靠猜值入库，应转 clause-proposal 补锚点。")
    out.append("")
    checks = card["self_check"]["checks"]
    if checks:
        out.append("| 检查面 | 字段 | 期望 | 实际 | 结论 |")
        out.append("| --- | --- | --- | --- | --- |")
        for check in checks:
            out.append(f"| {cell(check['name'])} | `{cell(check['field'])}` | "
                       f"{cell(check['expected'])} | {cell(check['actual'])} | "
                       f"{'✅ 通过' if check['ok'] else '❌ 未通过'} |")
    else:
        out.append("机械自查未发现问题。")
    out.append("")

    out.append("## 五、判据库实测（权威判定）")
    out.append("")
    violations = card["verify"]["violations"]
    out.append(f"- 违例数：**{violations}**")
    if violations:
        out.append("")
        out.append("| 条款 | 说明 | 证据 |")
        out.append("| --- | --- | --- |")
        for item in card["verify"]["items"]:
            evidence = item.get("evidence")
            rendered = "，".join(f"`{k}`=`{as_text(v)}`" for k, v in sorted(evidence.items())) \
                if isinstance(evidence, dict) and evidence else "—"
            out.append(f"| {cell(item.get('source') or item.get('clause_id'))} | "
                       f"{cell(item.get('message'))} | {rendered} |")
    out.append("")

    if card["discrepancies"]:
        out.append("## 六、自查与实测不一致")
        out.append("")
        out.append("脚本的机械自查与 `std_verify` 给出了不同结论。**以 `std_verify` 为准**，"
                   "但不一致本身必须上报——它通常意味着判据库与脚本的字段口径已经漂移。")
        out.append("")
        for item in card["discrepancies"]:
            out.append(f"- {item}")
        out.append("")

    out.append("## 七、缺口与下一步")
    out.append("")
    if card["gaps"]:
        for gap in card["gaps"]:
            out.append(f"- {gap}")
    else:
        out.append("- 无缺口。")
    out.append("")
    out.append(f"**下一步**：{card['next_step']}")
    out.append("")
    out.append("## 八、口径声明")
    out.append("")
    out.append("1. 入库只证明该资产通过了**现行判据库**的自查，不证明它符合标准全文。")
    out.append("2. 资产标识由 `asset_ingest` 生成，本脚本不发明任何字段值。")
    out.append("3. 时间字段全部来自输入，脚本不使用当前时钟。")
    out.append("4. 豁免项必须带批准人，并原样留在资产卡上。")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# self test
# --------------------------------------------------------------------------
SELFTEST = {
    "draft": {
        "asset_id": "AST-2026-0001", "modality": "表格", "secrecy": "内部",
        "owner_dept": "设备管理部", "collect_time": "2026-03-01", "ingest_time": "2026-03-05",
        "quality_score": 87.5, "completeness": 0.93, "file_hash": "a" * 64,
        "cited_standards": ["GB/T 33863.1-2017"], "storage_uri": "oss://assets/2026/0001.csv",
    },
    "modality_source": "CSV 首行为表头，行列结构完整，判定为表格模态",
    "anchors": [
        {"id": "AN-001", "clause_id": "第6条", "source": "《演示样例》第6条",
         "label": "模态受控词表：文本、表格、图文、影像"},
        {"id": "AN-002", "clause_id": "第14条", "source": "《演示样例》第14条",
         "label": "哈希格式：[0-9a-f]{64}"},
    ],
    "clauses": [
        {"clause_id": "第5条", "kind": "格式约束", "source": "《演示样例》第5条",
         "params": {"field": "asset_id", "pattern": r"[A-Za-z0-9\-]{8,64}"}},
        {"clause_id": "第6条", "kind": "受控词表", "source": "《演示样例》第6条",
         "params": {"field": "modality", "allowed": ["文本", "表格", "图文", "影像"]}},
        {"clause_id": "第7条", "kind": "受控词表", "source": "《演示样例》第7条",
         "params": {"field": "secrecy", "allowed": ["公开", "内部", "秘密"]}},
        {"clause_id": "第8条", "kind": "必填约束", "source": "《演示样例》第8条",
         "params": {"field": "owner_dept"}},
        {"clause_id": "第9条", "kind": "条件必填", "source": "《演示样例》第9条",
         "params": {"when_field": "secrecy", "when_value": "秘密",
                    "require_field": "declassify_date"}},
        {"clause_id": "第10条", "kind": "条件必填", "source": "《演示样例》第10条",
         "params": {"when_field": "modality", "when_value": "影像",
                    "require_field": "annotation_note"}},
        {"clause_id": "第11条", "kind": "跨字段一致性", "source": "《演示样例》第11条",
         "params": {"left": "collect_time", "right": "ingest_time", "op": "<="}},
        {"clause_id": "第12条", "kind": "数值范围", "source": "《演示样例》第12条",
         "params": {"field": "quality_score", "min": 0, "max": 100}},
        {"clause_id": "第13条", "kind": "数值范围", "source": "《演示样例》第13条",
         "params": {"field": "completeness", "min": 0.0, "max": 1.0}},
        {"clause_id": "第14条", "kind": "格式约束", "source": "《演示样例》第14条",
         "params": {"field": "file_hash", "pattern": r"[0-9a-f]{64}"}},
        {"clause_id": "第15条", "kind": "跨文档引用", "source": "《演示样例》第15条",
         "params": {"field": "cited_standards", "registry": "controlled_standards"}},
        {"clause_id": "第16条", "kind": "必填约束", "source": "《演示样例》第16条",
         "params": {"field": "storage_uri"}},
    ],
    "verify": {"results": [{"asset_id": "AST-2026-0001", "compliant": True, "findings": []}]},
    "waivers": [],
    "ingest": {"asset_id": "AST-2026-0001",
               "catalog_item": {"name": "设备点检台账 2026Q1", "modality": "表格"}},
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
        description="Build the six-dimension onboarding card and gate the ingest."
    )
    parser.add_argument("--in", dest="infile", help="Path to onboarding-input.json.")
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
    draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
    if not draft:
        return fail("draft is missing from the input")
    clauses = [c for c in (data.get("clauses") or []) if isinstance(c, dict)]
    anchors = [a for a in (data.get("anchors") or []) if isinstance(a, dict)]
    verify = data.get("verify") if isinstance(data.get("verify"), dict) else {}
    waivers = [w for w in (data.get("waivers") or []) if isinstance(w, dict)]
    ingest = data.get("ingest") if isinstance(data.get("ingest"), dict) else {}

    dimensions, checks = run_checks(draft, clauses, ingest)
    violations = collect_violations(verify)

    waived_ids = {as_text(w.get("clause_id")) for w in waivers if as_text(w.get("clause_id"))}
    open_violations = [v for v in violations if as_text(v.get("clause_id")) not in waived_ids]

    failed_checks = [c for c in checks if not c["ok"]]
    missing_dims = [d for d in dimensions if d["status"] in ("missing", "invalid")]

    reasons = []
    if missing_dims:
        for dim in missing_dims:
            bad = "、".join(f"`{f['key']}`" for f in dim["fields"]
                            if f["status"] in ("missing", "invalid"))
            reasons.append(f"{dim['id']} {dim['name']}未通过：{bad}")
    if open_violations:
        reasons.append(f"判据库实测存在 {len(open_violations)} 条未豁免违例，不得入库")
    if failed_checks and not missing_dims:
        names = "、".join(f"`{c['field']}`（{c['name']}）" for c in failed_checks)
        reasons.append(f"机械自查未通过：{names}")

    status = "deny" if reasons else "allow"

    discrepancies = []
    if status == "allow" and violations and open_violations == [] and violations:
        discrepancies.append("存在违例但全部已豁免；请确认豁免理由仍成立。")
    if failed_checks and not violations:
        discrepancies.append("脚本机械自查未通过，但 std_verify 未报违例——"
                             "两者字段口径可能已漂移，请复核判据库快照。")
    if not failed_checks and open_violations:
        discrepancies.append("脚本机械自查通过，但 std_verify 报出违例——"
                             "以 std_verify 为准，并检查脚本默认口径是否滞后。")

    gaps = []
    for check in failed_checks:
        if check["name"] == "条件必填":
            gaps.append(f"`{check['field']}` 因条件成立而必填，当前为空，需从文件或用户处补值。")
        elif check["name"] == "必填约束":
            gaps.append(f"`{check['field']}` 必填项为空，需补值（纯空白不算填）。")
        elif check["name"] == "受控词表":
            gaps.append(f"`{check['field']}` 取值 {check['actual']} 不在受控词表内，"
                        f"需换成 {check['expected']} 之一。")
        elif check["name"] == "格式约束":
            gaps.append(f"`{check['field']}` 不匹配 {check['expected']}，需按格式重填。")
        elif check["name"] == "数值范围":
            gaps.append(f"`{check['field']}` 越界（期望 {check['expected']}，实际 {check['actual']}）。")
        elif check["name"] == "跨字段一致性":
            gaps.append(f"`{check['field']}` 序关系不成立（{check['actual']}）；"
                        f"先确认哪个值才是对的。")
    if not anchors:
        gaps.append("未取到硬锚点：该条款类型缺少机械锚点，请转 clause-proposal 补锚点。")
    if not ingest.get("asset_id") and not draft.get("asset_id"):
        gaps.append("资产标识待 `asset_ingest` 生成后回填。")

    next_step = ("可以调用 `asset_ingest` 入库；入库成功后把返回填进 `ingest` 字段重新出卡。"
                 if status == "allow" else
                 "不得入库。按上方缺口补齐字段后，重新走硬锚点自查与 std_verify。")

    by_clause: dict = {}
    for anchor in anchors:
        key = as_text(anchor.get("clause_id") or anchor.get("source")) or "未标注"
        by_clause[key] = by_clause.get(key, 0) + 1

    card = {
        "generated_by": "scripts/build_onboarding_card.py",
        "asset": {
            "asset_id": as_text(ingest.get("asset_id")) or as_text(draft.get("asset_id")),
            "modality": as_text(draft.get("modality")),
            "modality_source": as_text(data.get("modality_source")),
            "secrecy": as_text(draft.get("secrecy")),
            "owner_dept": as_text(draft.get("owner_dept")),
            "storage_uri": as_text(draft.get("storage_uri")),
            "catalog_item": ingest.get("catalog_item") or {},
        },
        "decision": {
            "status": status,
            "reasons": reasons or ["六维齐全，机械自查通过，判据库实测零违例。"],
            "waived": waivers,
        },
        "dimensions": dimensions,
        "anchors": {"total": len(anchors), "by_clause": by_clause},
        "self_check": {"checks": checks, "failed": len(failed_checks)},
        "verify": {"violations": len(violations), "open_violations": len(open_violations),
                   "items": violations},
        "discrepancies": discrepancies,
        "gaps": gaps,
        "next_step": next_step,
    }
    report = render_markdown(card)

    json_path = stem.with_suffix(".json")
    md_path = stem.with_suffix(".md")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(report, encoding="utf-8")             # write both first ...

    return emit([json_path, md_path],                         # ... then stat both
                f"{tag}资产卡已生成：入库结论 {status}，"
                f"缺口 {len(gaps)} 项，未豁免违例 {len(open_violations)} 条")


if __name__ == "__main__":
    sys.exit(main())
