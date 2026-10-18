#!/usr/bin/env python3
"""Render a clause-grouped standard compliance report from a std_verify result.

Input : the JSON payloads of ``std_verify`` / ``std_clause_list`` /
        ``std_compliance`` (see references/verify-io-contract.md).
Output: one Markdown file, declared in SKILL.md ``script_outputs``.

stdout carries exactly one line -- the Nexent artifact contract JSON
(``doc/docs/zh/user-guide/resource-repository/custom-file-generation-skill.md``).
Debug information must never be printed to stdout.

Determinism: no clock, no randomness, no network, no model calls. The same
input bytes always produce the same report bytes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

MIME_TYPE = "text/markdown"
DEFAULT_OUT = "compliance-report.md"
DEFAULT_TITLE = "数据资产标准合规校验报告"

# Clause kind -> severity used only to order the narrative. Higher is louder.
SEVERITY = {
    "禁止取值": 5,
    "必填约束": 4,
    "条件必填": 4,
    "受控词表": 4,
    "格式约束": 3,
    "数值范围": 3,
    "跨字段一致性": 2,
    "跨文档引用": 2,
}
DEFAULT_SEVERITY = 1

FIX_HINT = {
    "禁止取值": "把取值换成现行版本，不要保留已废止条目。",
    "必填约束": "补齐该字段的值；空字符串与空数组同样算未填。",
    "条件必填": "补齐 require_field，或由业务方确认后改 when_field 的取值。",
    "受控词表": "把取值改成词表内的项；确需新取值时属于改标准，转 clause-proposal。",
    "格式约束": "按正则重填，并检查是否被截断或大小写被改动。",
    "数值范围": "修正到区间内，先确认是单位错还是真越界。",
    "跨字段一致性": "先确认两个字段哪个才是对的，再改另一个。",
    "跨文档引用": "换成受控清单内的条目；清单漏项时转 clause-proposal。",
}
DEFAULT_FIX = "按条款原文修正该字段的取值。"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def load_json(path: str | None) -> object | None:
    """Load a JSON file; return None when the path is absent or unreadable."""
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_file():
        return None
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clause_order(clause_id: str) -> int:
    """Sort key for clause ids such as 第6条 -> 6. Non numeric ids sort last."""
    match = re.search(r"(\d+)", str(clause_id or ""))
    return int(match.group(1)) if match else 10**6


def as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def cell(value: object) -> str:
    """Escape a value for use inside a Markdown table cell."""
    return as_text(value).replace("|", "\\|").replace("\n", " ").strip()


# --------------------------------------------------------------------------
# input normalisation
# --------------------------------------------------------------------------
def normalize_verify(raw: object) -> tuple[dict, list[dict]]:
    """Return (meta, assets).

    Accepts the canonical ``{"results": [...]}`` shape, a flat
    ``{"violations": [...]}`` list, or a bare array of per-asset rows.
    """
    meta: dict = {}
    rows: list[dict] = []

    if isinstance(raw, dict):
        for key in ("standard", "snapshot_id", "summary"):
            if key in raw:
                meta[key] = raw[key]
        if isinstance(raw.get("results"), list):
            rows = [r for r in raw["results"] if isinstance(r, dict)]
        elif isinstance(raw.get("violations"), list):
            grouped: dict[str, dict] = {}
            for item in raw["violations"]:
                if not isinstance(item, dict):
                    continue
                aid = as_text(item.get("asset_id")) or "UNKNOWN"
                bucket = grouped.setdefault(aid, {"asset_id": aid, "findings": []})
                bucket["findings"].append(item)
            rows = [grouped[key] for key in sorted(grouped)]
    elif isinstance(raw, list):
        rows = [r for r in raw if isinstance(r, dict)]

    assets: list[dict] = []
    for row in rows:
        asset_id = as_text(row.get("asset_id")) or "UNKNOWN"
        findings = [f for f in (row.get("findings") or []) if isinstance(f, dict)]
        assets.append(
            {
                "asset_id": asset_id,
                "violations": [f for f in findings if not f.get("ok", False)],
                "passed": [f for f in findings if f.get("ok", False)],
                "compliant": row.get("compliant"),
            }
        )
    return meta, assets


def build_clause_index(raw: object) -> tuple[dict, dict, list]:
    """Return (by_id, by_source, uncompilable) from a std_clause_list payload."""
    by_id: dict = {}
    by_source: dict = {}
    uncompilable: list = []
    if not isinstance(raw, dict):
        return by_id, by_source, uncompilable
    for clause in raw.get("clauses") or []:
        if not isinstance(clause, dict):
            continue
        cid = as_text(clause.get("clause_id"))
        if cid:
            by_id[cid] = clause
        src = as_text(clause.get("source"))
        if src:
            by_source[src] = clause
    for clause in raw.get("uncompilable") or []:
        if isinstance(clause, dict):
            uncompilable.append(clause)
    return by_id, by_source, uncompilable


# --------------------------------------------------------------------------
# report rendering
# --------------------------------------------------------------------------
def render(title: str, meta: dict, assets: list[dict], clause_raw: object,
           compliance_raw: object) -> str:
    by_id, by_source, uncompilable = build_clause_index(clause_raw)

    groups: dict[tuple, dict] = {}
    total_violations = 0
    for asset in assets:
        for finding in asset["violations"]:
            total_violations += 1
            cid = as_text(finding.get("clause_id")) or "未标注条款"
            source = as_text(finding.get("source")) or cid
            clause = by_id.get(cid) or by_source.get(source) or {}
            kind = as_text(finding.get("kind") or clause.get("kind"))
            key = (clause_order(cid), cid, source, kind)
            group = groups.setdefault(
                key,
                {
                    "clause_id": cid,
                    "source": source,
                    "kind": kind,
                    "text": as_text(finding.get("clause_text") or clause.get("text")),
                    "hits": [],
                },
            )
            if not group["text"]:
                group["text"] = as_text(finding.get("clause_text") or clause.get("text"))
            group["hits"].append(
                {
                    "asset_id": asset["asset_id"],
                    "message": as_text(finding.get("message")),
                    "evidence": finding.get("evidence") or {},
                }
            )

    ordered = sorted(
        groups.values(),
        key=lambda g: (-len(g["hits"]), -SEVERITY.get(g["kind"], DEFAULT_SEVERITY),
                       clause_order(g["clause_id"]), g["clause_id"]),
    )

    standard = as_text(meta.get("standard"))
    snapshot = as_text(meta.get("snapshot_id"))
    summary = meta.get("summary") if isinstance(meta.get("summary"), dict) else {}
    compliance = None
    if isinstance(compliance_raw, dict) and compliance_raw.get("compliance") is not None:
        compliance = compliance_raw.get("compliance")
    elif summary.get("compliance") is not None:
        compliance = summary.get("compliance")
    if not snapshot and isinstance(compliance_raw, dict):
        snapshot = as_text(compliance_raw.get("snapshot_id"))

    checked = summary.get("checked_clauses")
    if checked is None and isinstance(compliance_raw, dict):
        checked = compliance_raw.get("checked_clauses")
    if checked is None:
        checked = len(by_id) or None

    out: list[str] = []
    out.append(f"# {title}")
    out.append("")
    out.append("> 本报告由 `scripts/render_compliance_report.py` 确定性渲染，"
               "判定全部来自 MCP 侧判据库，**无人工判定、无模型判定**。")
    if not clause_raw:
        out.append(">")
        out.append("> ⚠️ 未提供 `std_clause_list` 输入，本报告**不含条款原文**，"
                   "且无法列出「无人把关的条款」。")
    out.append("")

    out.append("## 一、摘要")
    out.append("")
    out.append("| 指标 | 值 |")
    out.append("| --- | --- |")
    out.append(f"| 标准 | {cell(standard) or '（未标注）'} |")
    out.append(f"| 判据库快照 | `{cell(snapshot) or '（未提供，结论不可复算）'}` |")
    out.append(f"| 受检资产 | {len(assets)} 条 |")
    out.append(f"| 参与校验的判据 | {cell(checked) if checked is not None else '（未提供）'} 条 |")
    out.append(f"| 违例总数 | {total_violations} 条 |")
    out.append(f"| 涉及条款 | {len(ordered)} 条 |")
    if compliance is not None:
        out.append(f"| 合规率 | **{cell(compliance)}**（`std_compliance` 口径，判据等权） |")
    else:
        out.append("| 合规率 | （未提供 `std_compliance` 输入） |")
    out.append("")

    if not ordered:
        out.append("## 二、违例按条款归类")
        out.append("")
        out.append(f"**{len(assets)} 条受检资产在现行判据库下全部放行。**")
        out.append("")
        out.append("注意这句话的确切含义：**已编译判据全部放行**，不等于符合标准全文。"
                   "未编译条款与判据库盲区都不在本次结论范围内。")
        out.append("")
    else:
        out.append("## 二、违例按条款归类")
        out.append("")
        out.append("按「条款」分组，不按「资产」分组——要回答的是「哪条标准要求没被满足」。"
                   "组内顺序 = 命中资产数降序 → 严重度降序。")
        out.append("")
        for index, group in enumerate(ordered, start=1):
            kind = group["kind"] or "类型未标注"
            out.append(f"### 2.{index} {group['source']}"
                       f"（{kind}，命中 {len(group['hits'])} 条资产）")
            out.append("")
            out.append(f"- **条款原文**：{group['text'] or '（缺失，请用 std_clause_list 补齐）'}")
            out.append(f"- **修复动作**：{FIX_HINT.get(group['kind'], DEFAULT_FIX)}")
            out.append("")
            out.append("| 资产 | 违例说明 | 证据字段 |")
            out.append("| --- | --- | --- |")
            for hit in sorted(group["hits"], key=lambda h: h["asset_id"]):
                evidence = hit["evidence"]
                if isinstance(evidence, dict) and evidence:
                    rendered = "，".join(
                        f"`{k}`=`{as_text(v)}`" for k, v in sorted(evidence.items())
                    )
                else:
                    rendered = "（无结构化证据）"
                out.append(f"| `{cell(hit['asset_id'])}` | {cell(hit['message'])} | {rendered} |")
            out.append("")

    out.append("## 三、逐资产索引")
    out.append("")
    out.append("| 资产 | 违例数 | 命中条款 |")
    out.append("| --- | --- | --- |")
    for asset in sorted(assets, key=lambda a: a["asset_id"]):
        ids = sorted(
            {as_text(f.get("clause_id")) or "未标注条款" for f in asset["violations"]},
            key=clause_order,
        )
        out.append(f"| `{cell(asset['asset_id'])}` | {len(asset['violations'])} | "
                   f"{cell('、'.join(ids)) if ids else '—'} |")
    out.append("")

    out.append("## 四、无人把关的条款")
    out.append("")
    if not clause_raw:
        out.append("（未提供 `std_clause_list` 输入，本节无法生成。）")
    elif not uncompilable:
        out.append("本版判据库未申报不可编译条款。")
    else:
        out.append("下列条款**编译不出可执行判据**，本次校验**未覆盖**它们，"
                   "且**不计入合规率分母**：")
        out.append("")
        out.append("| 条款 | 标题 | 原文 |")
        out.append("| --- | --- | --- |")
        for clause in sorted(uncompilable, key=lambda c: clause_order(as_text(c.get("clause_id")))):
            out.append(f"| {cell(clause.get('clause_id'))} | {cell(clause.get('title'))} | "
                       f"{cell(clause.get('text'))} |")
        out.append("")
        out.append("这些条款正是 `clause-proposal` 下一步要处理的对象。")
    out.append("")

    out.append("## 五、口径与边界")
    out.append("")
    out.append("1. 判定来自 `std_verify`，合规率来自 `std_compliance`；两者口径不同，"
               "数字对不上时以 `std_compliance` 为准并在原对话里注记。")
    out.append("2. 「无违例」= 现行已编译判据全部放行，**不等于**符合标准全文。")
    out.append("3. 结论只对上方标注的判据库快照成立；判据库更新后须重跑。")
    out.append("4. 本报告不含任何模型判定，也不对未编译条款做任何推断。")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# self test
# --------------------------------------------------------------------------
SELFTEST_VERIFY = {
    "standard": "工业数据资产登记与校验规范（演示样例）",
    "snapshot_id": "clause-db-selftest",
    "summary": {"assets": 2, "checked_clauses": 12, "violations": 3, "compliance": 0.875},
    "results": [
        {
            "asset_id": "AST-2026-0001",
            "compliant": False,
            "findings": [
                {"clause_id": "第6条", "source": "《演示样例》第6条", "ok": False,
                 "message": "modality=「表格图像」不在受控词表 ['文本','表格','图文','影像'] 内",
                 "evidence": {"field": "modality", "value": "表格图像"},
                 "clause_text": "数据资产的模态应属于受控词表：文本、表格、图文、影像。"},
                {"clause_id": "第8条", "source": "《演示样例》第8条", "ok": False,
                 "message": "必填字段 owner_dept 缺失或为空",
                 "evidence": {"field": "owner_dept"},
                 "clause_text": "数据资产应登记责任部门，不得为空。"},
                {"clause_id": "第5条", "source": "《演示样例》第5条", "ok": True,
                 "message": "", "evidence": {}, "clause_text": "资产标识应为全局唯一字符串。"},
            ],
        },
        {
            "asset_id": "AST-2026-0002",
            "compliant": False,
            "findings": [
                {"clause_id": "第6条", "source": "《演示样例》第6条", "ok": False,
                 "message": "modality=「图纸」不在受控词表 ['文本','表格','图文','影像'] 内",
                 "evidence": {"field": "modality", "value": "图纸"},
                 "clause_text": "数据资产的模态应属于受控词表：文本、表格、图文、影像。"},
            ],
        },
    ],
}
SELFTEST_CLAUSES = {
    "snapshot_id": "clause-db-selftest",
    "clauses": [
        {"clause_id": "第5条", "title": "资产标识格式", "kind": "格式约束",
         "text": "资产标识应为全局唯一字符串，由 8 至 64 位字母、数字或连字符组成。",
         "source": "《演示样例》第5条", "state": "active", "compilable": True},
        {"clause_id": "第6条", "title": "资产模态受控", "kind": "受控词表",
         "text": "数据资产的模态应属于受控词表：文本、表格、图文、影像。",
         "source": "《演示样例》第6条", "state": "active", "compilable": True},
        {"clause_id": "第8条", "title": "责任部门必填", "kind": "必填约束",
         "text": "数据资产应登记责任部门，不得为空。",
         "source": "《演示样例》第8条", "state": "active", "compilable": True},
    ],
    "uncompilable": [
        {"clause_id": "第20条", "title": "业务真实性",
         "text": "数据资产应真实反映其所描述的业务过程，不得虚构。"},
    ],
}
SELFTEST_COMPLIANCE = {"compliance": 0.875, "assets": 2, "checked_clauses": 12,
                       "snapshot_id": "clause-db-selftest"}


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def print_contract(payload: dict) -> None:
    """Print one JSON line; fall back to ASCII escapes if stdout cannot encode it."""
    line = json.dumps(payload, ensure_ascii=False)
    try:
        print(line)
    except UnicodeEncodeError:
        print(json.dumps(payload, ensure_ascii=True))


def emit(out_path: Path, message: str) -> int:
    """Write contract JSON to stdout. Must be the only stdout output."""
    print_contract({
        "status": "success",
        "artifacts": [{
            "kind": "file",
            "absolute_path": str(out_path.resolve()),
            "file_name": out_path.name,
            "mime_type": MIME_TYPE,
            "file_size_bytes": out_path.stat().st_size,
        }],
        "message": message,
    })
    return 0


def fail(message: str) -> int:
    print_contract({"status": "error", "error": message})
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a clause-grouped compliance report from std_verify output."
    )
    parser.add_argument("--verify", help="Path to the std_verify JSON payload.")
    parser.add_argument("--clauses", help="Path to the std_clause_list JSON payload.")
    parser.add_argument("--compliance", help="Path to the std_compliance JSON payload.")
    parser.add_argument("--title", default=DEFAULT_TITLE, help="Report title.")
    parser.add_argument("--out", default=DEFAULT_OUT, help="Output Markdown file name.")
    parser.add_argument("--selftest", action="store_true",
                        help="Render a built-in fixture and exit; used by skills/_validate.py.")
    args = parser.parse_args()

    if args.selftest:
        return run(SELFTEST_VERIFY, SELFTEST_CLAUSES, SELFTEST_COMPLIANCE,
                   args.title, Path(tempfile.mkdtemp()) / args.out, "(selftest)")

    if not args.verify:
        return fail("--verify is required (or use --selftest)")

    raw = load_json(args.verify)
    if raw is None:
        return fail(f"verify payload not found or not valid JSON: {args.verify}")
    clause_raw = load_json(args.clauses)
    compliance_raw = load_json(args.compliance)
    return run(raw, clause_raw, compliance_raw, args.title, Path(args.out), "")


def run(raw: object, clause_raw: object, compliance_raw: object,
        title: str, out_path: Path, tag: str) -> int:
    meta, assets = normalize_verify(raw)
    if not assets:
        return fail("verify payload contains no per-asset results")

    report = render(title, meta, assets, clause_raw, compliance_raw)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")   # write first ...

    total = sum(len(a["violations"]) for a in assets)
    return emit(out_path,                            # ... then stat
                f"{tag}合规报告已生成：{len(assets)} 条资产，{total} 条违例，"
                f"文件 {out_path.name}")


if __name__ == "__main__":
    sys.exit(main())
