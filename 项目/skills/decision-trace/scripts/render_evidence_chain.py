#!/usr/bin/env python3
"""Render an evidence-chain report for one claim.

Input : one JSON file assembled by the agent (see SKILL.md step 4), holding the
        claim, the ``asset_lineage`` payload, the clause list, the findings and
        the retrieved citation items.
Output: ``evidence-chain.md``, declared in SKILL.md ``script_outputs``.

stdout carries exactly one line -- the Nexent artifact contract JSON. Nothing
else may be printed to stdout.

Determinism: no clock, no randomness, no network, no model calls. The
credibility grade is computed from the payload, never asserted by a model.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

MIME_TYPE = "text/markdown"
DEFAULT_OUT = "evidence-chain.md"
GRADES = ["强", "中", "弱", "不成立"]


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


def node_label(nodes: dict, node_id: str) -> str:
    info = nodes.get(node_id)
    if isinstance(info, dict):
        return as_text(info.get("label") or info.get("name") or info.get("type")) or node_id
    if isinstance(info, str) and info:
        return info
    return node_id


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------
def analyse(data: dict) -> dict:
    lineage = data.get("lineage") if isinstance(data.get("lineage"), dict) else {}
    raw_nodes = lineage.get("nodes") if isinstance(lineage.get("nodes"), list) else []
    raw_edges = lineage.get("edges") if isinstance(lineage.get("edges"), list) else []
    cites = [c for c in (data.get("cites") or []) if isinstance(c, dict)]

    nodes: dict = {}
    for index, node in enumerate(raw_nodes):
        if isinstance(node, dict):
            nid = as_text(node.get("id") or node.get("node_id") or node.get("asset_id"))
            if nid:
                nodes[nid] = node
        else:
            nodes[as_text(node)] = as_text(node)

    used_cites: set = set()
    edges = []
    for index, edge in enumerate(raw_edges):
        if not isinstance(edge, dict):
            continue
        src = as_text(edge.get("from") or edge.get("source_node"))
        dst = as_text(edge.get("to") or edge.get("target_node"))
        op = as_text(edge.get("op") or edge.get("operation"))
        source = as_text(edge.get("source") or edge.get("doc") or edge.get("provenance"))
        cite_index = edge.get("cite_index")

        attached = []
        if cite_index is not None:
            for pos, cite in enumerate(cites):
                if cite.get("cite_index") == cite_index:
                    attached.append(cite)
                    used_cites.add(pos)
        if not attached and op:
            for pos, cite in enumerate(cites):
                if as_text(cite.get("hop")) == op:
                    attached.append(cite)
                    used_cites.add(pos)

        edges.append({
            "order": index + 1,
            "from": src,
            "from_label": node_label(nodes, src),
            "to": dst,
            "to_label": node_label(nodes, dst),
            "op": op,
            "source": source,
            "cite_index": cite_index,
            "cites": attached,
        })

    total = len(edges)
    sourced = sum(1 for e in edges if e["source"])
    cited = sum(1 for e in edges if e["cites"] or e["cite_index"] is not None)

    claim = data.get("claim") if isinstance(data.get("claim"), dict) else {}
    clause_id = as_text(claim.get("clause_id"))
    clauses = [c for c in (data.get("clauses") or []) if isinstance(c, dict)]
    matched = None
    if clause_id:
        for clause in clauses:
            if as_text(clause.get("clause_id")) == clause_id:
                matched = clause
                break

    if total == 0:
        grade = "不成立"
    elif sourced < total:
        grade = "弱"
    elif cited < total:
        grade = "中"
    else:
        grade = "强"

    downgraded = False
    if clause_id and matched is None and grade != "不成立":
        grade = GRADES[min(GRADES.index(grade) + 1, len(GRADES) - 1)]
        downgraded = True

    gaps = []
    for edge in edges:
        missing = []
        if not edge["source"]:
            missing.append("source")
        if not (edge["cites"] or edge["cite_index"] is not None):
            missing.append("cite_index")
        if missing:
            gaps.append({"edge": edge, "missing": missing})

    unmounted = [c for pos, c in enumerate(cites) if pos not in used_cites]

    return {
        "nodes": nodes, "edges": edges, "cites": cites, "claim": claim,
        "clauses": clauses, "matched_clause": matched, "clause_id": clause_id,
        "total": total, "sourced": sourced, "cited": cited,
        "grade": grade, "downgraded": downgraded, "gaps": gaps, "unmounted": unmounted,
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def render(data: dict, info: dict) -> str:
    claim = info["claim"]
    findings = [f for f in (data.get("findings") or []) if isinstance(f, dict)]

    out: list[str] = []
    out.append("# 决策证据链报告")
    out.append("")
    out.append("> 由 `scripts/render_evidence_chain.py` 确定性渲染。"
               "**没有 provenance 的跳不允许进入推理**；本报告不含任何模型判定。")
    out.append("")

    out.append("## 一、结论")
    out.append("")
    out.append(f"- 断言：{cell(claim.get('text')) or '（未提供）'}")
    out.append(f"- 类型：`{cell(claim.get('kind')) or '（未标注）'}`")
    out.append(f"- 关联资产：`{cell(claim.get('asset_id')) or '—'}`")
    out.append(f"- 关联条款：`{cell(info['clause_id']) or '—'}`")
    out.append(f"- 关联 run：`{cell(claim.get('run_id')) or '—'}`")
    out.append("")

    out.append("## 二、可采信强度")
    out.append("")
    out.append(f"### 评级：**{info['grade']}**")
    out.append("")
    out.append("| 计算项 | 值 |")
    out.append("| --- | --- |")
    out.append(f"| 链路跳数 H | {info['total']} |")
    out.append(f"| 带来源的跳数 S | {info['sourced']} |")
    out.append(f"| 带 cite_index 的跳数 C | {info['cited']} |")
    out.append(f"| 终点条款存在 | {'是' if info['matched_clause'] else '否'} |")
    out.append(f"| 是否因条款缺失降级 | {'是' if info['downgraded'] else '否'} |")
    out.append("")
    out.append("评级口径：`H == 0` → 不成立；`S < H` → 弱；`S == H 且 C < H` → 中；"
               "`S == H 且 C == H` → 强。终点条款在判据库中不存在时降一级。")
    out.append("")

    out.append("## 三、逐跳链路")
    out.append("")
    if not info["edges"]:
        out.append("**没有取得任何链路。** 该结论目前无法构成证据链，评级为「不成立」。")
        out.append("")
        out.append("可能原因：该资产未入库、血缘库无记录、或 `direction` / `depth` 选错。"
                   "请先用 `asset-onboarding` 确认资产已正确入库。")
    else:
        out.append("| # | 从 | 操作 | 到 | 来源 | cite_index |")
        out.append("| --- | --- | --- | --- | --- | --- |")
        for edge in info["edges"]:
            src = f"`{cell(edge['from'])}`" + (f"（{cell(edge['from_label'])}）"
                                               if edge["from_label"] != edge["from"] else "")
            dst = f"`{cell(edge['to'])}`" + (f"（{cell(edge['to_label'])}）"
                                             if edge["to_label"] != edge["to"] else "")
            source = cell(edge["source"]) or "**（无来源）**"
            indexes = "、".join(as_text(c.get("cite_index")) for c in edge["cites"]) \
                or (as_text(edge["cite_index"]) or "**（缺）**")
            out.append(f"| {edge['order']} | {src} | `{cell(edge['op']) or '—'}` | {dst} | "
                       f"{source} | {indexes} |")
    out.append("")

    out.append("## 四、终点依据")
    out.append("")
    if info["matched_clause"]:
        clause = info["matched_clause"]
        out.append(f"- 依据：{cell(clause.get('source')) or cell(clause.get('clause_id'))}")
        out.append(f"- 类型：`{cell(clause.get('kind')) or '—'}`")
        out.append(f"- 原文：「{cell(clause.get('text'))}」")
    elif info["clause_id"]:
        out.append(f"- ⚠️ 结论声明的 `clause_id = {cell(info['clause_id'])}` "
                   f"在判据库中**找不到对应条款**。整条链没有终点依据，评级已被降一级。")
    else:
        out.append("- 该结论未声明条款依据（`clause_id` 为空）。")
    if findings:
        out.append("")
        out.append("| 条款 | 判定 | 说明 | 证据 |")
        out.append("| --- | --- | --- | --- |")
        for finding in findings:
            verdict = "通过" if finding.get("ok") else "**违例**"
            evidence = finding.get("evidence")
            rendered = "，".join(f"`{k}`=`{as_text(v)}`"
                                for k, v in sorted(evidence.items())) \
                if isinstance(evidence, dict) and evidence else "—"
            out.append(f"| {cell(finding.get('source') or finding.get('clause_id'))} | {verdict} | "
                       f"{cell(finding.get('message'))} | {rendered} |")
    out.append("")

    out.append("## 五、来源引用清单")
    out.append("")
    if not info["cites"]:
        out.append("（未提供检索引用项，`cite_index` 覆盖率为 0。）")
    else:
        out.append("| cite_index | 文档 | 定位 | 摘录 | 挂载跳 |")
        out.append("| --- | --- | --- | --- | --- |")
        for cite in info["cites"]:
            attached = [e["order"] for e in info["edges"]
                        if cite in [c for c in e["cites"]]]
            out.append(f"| {cell(cite.get('cite_index'))} | {cell(cite.get('doc'))} | "
                       f"{cell(cite.get('locator'))} | {cell(cite.get('quote'))} | "
                       f"{cell('、'.join(str(a) for a in attached)) or '**未挂载**'} |")
    out.append("")

    out.append("## 六、缺口清单")
    out.append("")
    if not info["gaps"]:
        out.append("无缺口：每一跳都有来源，且都挂上了平台引用。")
    else:
        out.append("缺口是结论可信度的上限，必须主动告知用户。**不要补全、不要顶替、不要沉默。**")
        out.append("")
        out.append("| 跳 | 位置 | 缺什么 | 补它的动作 |")
        out.append("| --- | --- | --- | --- |")
        for gap in info["gaps"]:
            edge = gap["edge"]
            where = f"`{cell(edge['from'])}` → `{cell(edge['op'])}` → `{cell(edge['to'])}`"
            missing = "、".join(gap["missing"])
            if "source" in gap["missing"]:
                action = ("该跳的抽取/变换环节未输出 provenance 四元组"
                          "（doc_id + page + bbox/cell + span_offset + extractor_version + timestamp）")
            else:
                action = "该跳未接入平台对话溯源链路，需在检索返回中补齐引用序号"
            out.append(f"| {edge['order']} | {where} | {missing} | {cell(action)} |")
    out.append("")

    out.append("## 七、未挂载引用")
    out.append("")
    if not info["unmounted"]:
        out.append("无：全部引用项都挂到了具体的跳上。")
    else:
        out.append("下列引用项在检索中返回了，但**对不上任何一跳**。它们不能作为链路来源，"
                   "列为待查：")
        out.append("")
        for cite in info["unmounted"]:
            out.append(f"- `cite_index={cell(cite.get('cite_index'))}`　"
                       f"{cell(cite.get('doc'))}　{cell(cite.get('locator'))}　"
                       f"{cell(cite.get('quote'))}")
    out.append("")

    out.append("## 八、口径声明")
    out.append("")
    out.append("1. 评级由本脚本按固定公式计算，不由模型判定，不可口头升级。")
    out.append("2. 无来源的跳不进入推理；缺失项一律记入第六节，不做推测补全。")
    out.append("3. 血缘方向与深度见输入 `lineage`；深度不同的血缘图不可比。")
    out.append("4. 本报告不修改任何数据，也不重新判定结论对错。")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# self test
# --------------------------------------------------------------------------
SELFTEST = {
    "claim": {"text": "资产 AST-2026-0031 违反《演示样例》第6条（模态不在受控词表内）",
              "kind": "compliance", "asset_id": "AST-2026-0031",
              "clause_id": "第6条", "run_id": ""},
    "lineage": {
        "nodes": [
            {"id": "DOC-QC-2026Q1", "type": "document", "label": "质检报告 2026Q1.pdf"},
            {"id": "AST-2026-0031", "type": "asset", "label": "质检图文资产"},
            {"id": "F-0007", "type": "finding", "label": "模态判定"},
        ],
        "edges": [
            {"from": "DOC-QC-2026Q1", "to": "AST-2026-0031", "op": "extract_field",
             "source": "质检报告 2026Q1.pdf#page=3&cell=B12", "cite_index": 1},
            {"from": "AST-2026-0031", "to": "F-0007", "op": "verify_clause",
             "source": "clause-db-selftest", "cite_index": 2},
        ],
    },
    "clauses": [
        {"clause_id": "第6条", "title": "资产模态受控", "kind": "受控词表",
         "text": "数据资产的模态应属于受控词表：文本、表格、图文、影像。",
         "source": "《演示样例》第6条"},
    ],
    "findings": [
        {"clause_id": "第6条", "source": "《演示样例》第6条", "ok": False,
         "message": "modality=「表格图像」不在受控词表内",
         "evidence": {"field": "modality", "value": "表格图像"}},
    ],
    "cites": [
        {"cite_index": 1, "doc": "质检报告 2026Q1.pdf", "locator": "page=3 cell=B12",
         "quote": "模态：表格图像", "hop": "extract_field"},
        {"cite_index": 2, "doc": "clause-db-selftest", "locator": "第6条",
         "quote": "受控词表：文本、表格、图文、影像", "hop": "verify_clause"},
        {"cite_index": 3, "doc": "设备台账.xlsx", "locator": "Sheet1!A1",
         "quote": "（与本次结论无关的检索命中）", "hop": "unrelated"},
    ],
}


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def emit(out_path: Path, message: str) -> int:
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
        description="Render an evidence-chain report for one claim."
    )
    parser.add_argument("--in", dest="infile", help="Path to evidence-input.json.")
    parser.add_argument("--out", default=DEFAULT_OUT, help="Output Markdown file name.")
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


def run(data: dict, out_path: Path, tag: str) -> int:
    info = analyse(data)
    report = render(data, info)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")            # write first ...

    return emit(out_path,                                     # ... then stat
                f"{tag}证据链报告已生成：{info['total']} 跳，"
                f"{len(info['gaps'])} 处缺口，评级 {info['grade']}")


if __name__ == "__main__":
    sys.exit(main())
