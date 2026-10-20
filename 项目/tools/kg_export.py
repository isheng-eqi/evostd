"""知识图谱导出：把「判据库 + 资产 + 语料」导出成轻量属性图（三元组）与可视化。

为什么是"轻量属性图"而不是 OWL/RDF 全套
--------------------------------------
本机 Nexent 没有图数据库（docs/04 实测），装不了 Neo4j 也不该为此引入 Docker。
而赛题要的是"本体半自动化构建 + 持续更新 + 与标准体系对齐"，
这三件事的**可验证形态**是：

  1. 有一组明确的**类**（标准/条款/字段/受控表/资产/文档/设备/部门）
  2. 有一组明确的**关系**（含条款、约束、引用、来源、涉及、归属、取值）
  3. 关系是**从既有数据机械导出**的，不是手画的
  4. 能**逐条回指**：每条边都能说出它来自哪个字段/哪个条款参数
  5. 能**增量更新**：判据库一变，图跟着变（重跑即可，幂等）

所以这里导出的是 (头实体, 关系, 尾实体) 三元组 + 统计 + 两张 SVG 示意图，
纯标准库，零依赖，可重复执行。

用法：
    python tools/kg_export.py --domain manufacturing --out submission/kg
    python tools/kg_export.py --domain gov_catalog --out submission/kg-gov
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


# ---------------------------------------------------------------------------
#  数据加载：全部来自领域包与判据库，不手写实体
# ---------------------------------------------------------------------------
def load_domain(domain: str) -> dict:
    import importlib

    assets_mod = importlib.import_module(f"domains.{domain}.assets")
    pack = importlib.import_module(f"domains.{domain}")
    docs = []
    try:
        corpus_mod = importlib.import_module(f"domains.{domain}.corpus")
        docs = corpus_mod.all_docs()
    except Exception:
        pass
    assets = [{k: v for k, v in a.items() if not k.startswith("_")} for a in assets_mod.all_assets()]
    return {"pack": pack, "assets": assets, "docs": docs}


def load_clauses(domain: str) -> list:
    """直接取领域包的 `Clause` 对象（含 v2 扩张判据）。

    ★ 不能用 `std_clause_list()`：它返回的是**给平台看的摘要**
      （clause_id / status / text / evidence），**不含 source 与 params**。
      结果第一次建图时「标准 0 条、字段 0 个」——图看着有 38 个条款，
      却没有一条边——典型的"结构完整、内容全空"。
      这里直接拿 Clause 对象，source 与 params 都在。
    """
    import importlib

    from standard.grammar_v2_experiment import v2_clauses

    pack = importlib.import_module(f"domains.{domain}")
    base = list(pack.standards())
    try:
        extra = v2_clauses(getattr(pack, "STANDARD_NAME", ""), domain=domain,
                           docs=pack.docs())
    except Exception as exc:                      # v2 不可用时退回 v1，但要说出来
        print(f"[kg] v2 判据装载失败，仅导出 v1：{type(exc).__name__}: {exc}", file=sys.stderr)
        extra = []
    return base + list(extra)


# ---------------------------------------------------------------------------
#  建图
# ---------------------------------------------------------------------------
STD_RE = re.compile(r"《([^》]*)》")


def _std_name(clause) -> str:
    src = getattr(clause, "source", "") or ""
    m = STD_RE.search(src)
    return m.group(1).strip() if m else ""


def build_graph(domain: str, spec: dict) -> dict:
    clauses = load_clauses(domain)
    assets = spec["assets"]
    docs = spec["docs"]

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    seen_edges: set[tuple] = set()

    def node(nid: str, ntype: str, label: str, **attrs):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "type": ntype, "label": label, **attrs}
        return nid

    def edge(s: str, p: str, o: str, **attrs):
        key = (s, p, o)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append({"s": s, "p": p, "o": o, **attrs})

    # 1) 标准 → 条款 → 字段 / 受控表
    for c in clauses:
        cid = str(getattr(c, "clause_id", ""))
        if not cid:
            continue
        std = _std_name(c)
        kind = getattr(getattr(c, "kind", None), "value", str(getattr(c, "kind", "")))
        cnode = node(f"clause:{cid}", "条款", cid,
                     text=(getattr(c, "text", "") or "")[:80], kind=kind)
        if std:
            snode = node(f"std:{std}", "标准", std)
            edge(snode, "含条款", cnode)
        params = getattr(c, "params", None) or {}
        for key in ("field", "key_field", "when_field", "forbid_field",
                    "left", "right", "id_field"):
            f = params.get(key)
            if isinstance(f, str) and f:
                fnode = node(f"field:{f}", "字段", f)
                edge(cnode, "约束", fnode, via=key)
        for key in ("table_key", "registry"):
            t = params.get(key)
            if isinstance(t, str) and t:
                tnode = node(f"table:{t}", "受控表", t)
                edge(cnode, "查表", tnode)

    # 2) 资产 → 标准 / 文档 / 设备 / 部门 / 字段取值
    for a in assets:
        aid = a.get("asset_id") or a.get("catalog_id") or a.get("asset_no")
        if not aid:
            continue
        anode = node(f"asset:{aid}", "资产", str(aid), label_raw=a.get("_label"))
        for s in a.get("cited_standards") or []:
            snode = node(f"std:{s}", "标准", s)
            edge(anode, "引用", snode)
        for d in a.get("source_doc_ids") or []:
            dnode = node(f"doc:{d}", "文档", d)
            edge(anode, "来源", dnode)
        eq = a.get("equipment_no")
        if eq:
            enode = node(f"equipment:{eq}", "设备", eq)
            edge(anode, "涉及", enode)
        dep = a.get("owner_dept")
        if dep:
            dnode = node(f"dept:{dep}", "部门", dep)
            edge(anode, "归属", dnode)
        for k, v in a.items():
            if f"field:{k}" in nodes and v not in (None, "", []):
                edge(anode, "取值", f"field:{k}", value=str(v)[:40])

    # 3) 文档 → 模态（作为文档节点的属性，不单独建类型）
    for d in docs:
        did = getattr(d, "doc_id", None)
        if did and f"doc:{did}" in nodes:
            nodes[f"doc:{did}"]["modality"] = getattr(d, "modality", "")

    return {"domain": domain, "nodes": list(nodes.values()), "edges": edges}


# ---------------------------------------------------------------------------
#  统计：这些数字就是文档里可引用的"本体质量"
# ---------------------------------------------------------------------------
# 六维标识的"六个维度各取一个字段"——**不同领域的字段名不同**。
# 第一版把制造业的六个字段名写死在统计里，政务包直接算出 0%：
# 不是政务资产缺维度，是度量用错了领域。这里按领域声明。
SIX_DIM = {
    "manufacturing": ("asset_id", "source_doc_ids", "cited_standards",
                      "equipment_no", "owner_dept", "ingest_time"),
    "gov_catalog": ("catalog_id", "data_level", "share_type",
                    "owner_dept", "publish_date", "field_count"),
}


def graph_stats(g: dict, assets: list[dict]) -> dict:
    from collections import Counter

    by_type = Counter(n["type"] for n in g["nodes"])
    by_pred = Counter(e["p"] for e in g["edges"])
    clauses = [n for n in g["nodes"] if n["type"] == "条款"]
    with_std = sum(1 for c in clauses if any(
        e["s"].startswith("std:") and e["o"] == c["id"] and e["p"] == "含条款" for e in g["edges"]))

    six = SIX_DIM.get(g["domain"], SIX_DIM["manufacturing"])
    full = sum(1 for a in assets if all(a.get(k) for k in six))

    constrained = {e["o"] for e in g["edges"] if e["p"] == "约束"}
    valued = {e["o"] for e in g["edges"] if e["p"] == "取值"}
    covered = len(constrained & valued)

    return {
        "domain": g["domain"],
        "n_nodes": len(g["nodes"]),
        "n_edges": len(g["edges"]),
        "n_by_type": dict(by_type),
        "n_by_predicate": dict(by_pred),
        "clause_standard_coverage": round(with_std / len(clauses), 4) if clauses else None,
        "asset_six_dim_full_rate": round(full / len(assets), 4) if assets else None,
        "n_assets": len(assets),
        "field_coverage": {
            "constrained_fields": len(constrained),
            "valued_fields": len(valued),
            "constrained_and_valued": covered,
        },
    }


# ---------------------------------------------------------------------------
#  可视化：两张自包含 SVG（类型示意图 + 单资产证据子图）
# ---------------------------------------------------------------------------
def _svg_frame(w: int, h: int, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" font-family="Microsoft YaHei, SimHei, sans-serif">'
            f'<rect width="{w}" height="{h}" fill="#ffffff"/>{body}</svg>')


def _box(x, y, w, h, text, fill, stroke, fs=15):
    t = html.escape(text)
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="1.6"/>'
            f'<text x="{x + w / 2}" y="{y + h / 2 + fs * 0.35}" text-anchor="middle" '
            f'font-size="{fs}" fill="#1f2328">{t}</text>')


def _arrow(x1, y1, x2, y2, label, dash=False):
    dash_attr = ' stroke-dasharray="6 4"' if dash else ''
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#8c959f" '
            f'stroke-width="1.6"{dash_attr} marker-end="url(#a)"/>'
            f'<text x="{mx}" y="{my - 6}" text-anchor="middle" font-size="12" fill="#57606a">'
            f'{html.escape(label)}</text>')


def svg_schema() -> str:
    W, H = 1100, 640
    defs = ('<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            'markerHeight="7" orient="auto-start-reverse">'
            '<path d="M0,0 L10,5 L0,10 z" fill="#8c959f"/></marker></defs>')
    b = [defs]
    b.append('<text x="550" y="38" text-anchor="middle" font-size="20" fill="#1f2328">'
             '本体类型与关系（由判据库机械导出）</text>')
    boxes = {
        "std": (80, 90, 180, 56, "标准", "#ddf4ff", "#54aeff"),
        "clause": (420, 90, 180, 56, "条款", "#dafbe1", "#4ac26b"),
        "field": (760, 90, 180, 56, "字段", "#fff8c5", "#d4a72c"),
        "table": (420, 270, 180, 56, "受控表", "#fbefff", "#c297ff"),
        "asset": (420, 400, 180, 56, "资产", "#ffebe9", "#ff8182"),
        "doc": (80, 400, 180, 56, "文档（三模态）", "#eaeef2", "#8c959f"),
        "equipment": (80, 250, 180, 56, "设备", "#eaeef2", "#8c959f"),
        "dept": (760, 400, 180, 56, "部门", "#eaeef2", "#8c959f"),
    }
    for x, y, w, h, t, f, s in boxes.values():
        b.append(_box(x, y, w, h, t, f, s))
    b.append(_arrow(260, 118, 420, 118, "含条款"))
    b.append(_arrow(600, 118, 760, 118, "约束"))
    b.append(_arrow(510, 146, 510, 270, "查表", dash=True))
    b.append(_arrow(420, 428, 260, 428, "来源"))
    b.append(_arrow(420, 410, 270, 150, "引用", dash=True))
    b.append(_arrow(600, 418, 790, 150, "取值", dash=True))
    b.append(_arrow(600, 428, 760, 428, "归属"))
    b.append(_arrow(420, 440, 260, 290, "涉及"))
    b.append('<text x="80" y="560" font-size="13" fill="#57606a">'
             '实线 = 结构与约束；虚线 = 资产的证据链（引用/取值/查表）</text>')
    b.append('<text x="80" y="584" font-size="13" fill="#57606a">'
             '每条边都能回指：来自哪个字段名 / 哪个条款参数 / 哪个资产字段</text>')
    return _svg_frame(W, H, "".join(b))


def svg_instance(g: dict, asset_id: str, violations: list | None = None) -> str:
    """单资产证据子图：资产 → 引用标准 / 来源文档 / 设备 / 部门 / 命中条款 → 依据标准。

    ★ 第一版把 30 条"取值"边全画出来，结果 30 个黄框叠在同一个位置、
      标签互相压住——**信息量很大，可读性为零**。现在把取值聚合成一个节点，
      只把"命中条款"逐条展开（那才是决策要看的证据链）。
    """
    W, H = 1200, 780
    defs = ('<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            'markerHeight="7" orient="auto-start-reverse">'
            '<path d="M0,0 L10,5 L0,10 z" fill="#8c959f"/></marker></defs>')
    aid = f"asset:{asset_id}"
    outs = [e for e in g["edges"] if e["s"] == aid]
    if not outs:
        return _svg_frame(W, H, '<text x="40" y="60" font-size="16">未找到该资产</text>')
    by_pred: dict[str, list[dict]] = {}
    for e in outs:
        by_pred.setdefault(e["p"], []).append(e)
    violations = violations or []

    b = [defs]
    b.append(f'<text x="{W/2}" y="38" text-anchor="middle" font-size="21" fill="#1f2328">'
             f'资产 {html.escape(asset_id)} 的证据链（逐条可回指）</text>')
    cx, cy = 470, 360
    b.append(_box(cx - 120, cy - 30, 240, 60, asset_id, "#ffebe9", "#ff8182", 17))

    # 上：引用标准
    stds = [e["o"].split(":", 1)[1] for e in by_pred.get("引用", [])][:4]
    xs = W / 2 - (len(stds) - 1) * 150 if stds else W / 2
    for i, s in enumerate(stds):
        x = xs + i * 300
        b.append(_box(x - 110, 80, 220, 46, s[:18], "#ddf4ff", "#54aeff", 13))
        b.append(_arrow(x, 126, cx, cy - 30, "引用", dash=True))

    # 左：来源文档
    docs = [e["o"].split(":", 1)[1] for e in by_pred.get("来源", [])][:4]
    for i, d in enumerate(docs):
        y = 230 + i * 62
        b.append(_box(40, y, 190, 44, d[:16], "#eaeef2", "#8c959f", 12))
        b.append(_arrow(230, y + 22, cx - 120, cy, "来源", dash=True))

    # 右：命中条款 → 依据标准
    for i, v in enumerate(violations[:3]):
        y = 220 + i * 96
        cid = str(v.get("clause_id"))
        b.append(_box(880, y, 240, 50, cid, "#dafbe1", "#4ac26b", 14))
        b.append(_arrow(cx + 120, cy, 880, y + 25, "命中"))
        src = (v.get("source") or "")
        m = STD_RE.search(src)
        if m:
            b.append(_box(880, y + 54, 240, 30, "依据：《" + m.group(1)[:12] + "》",
                          "#f6f8fa", "#d0d7de", 11))

    # 右下：设备
    eqs = [e["o"].split(":", 1)[1] for e in by_pred.get("涉及", [])][:2]
    for i, e in enumerate(eqs):
        b.append(_box(880, 560 + i * 60, 240, 44, e[:18], "#eaeef2", "#8c959f", 12))
        b.append(_arrow(cx + 100, cy + 26, 880, 582 + i * 60, "涉及", dash=True))
    # 左下：部门
    deps = [e["o"].split(":", 1)[1] for e in by_pred.get("归属", [])][:2]
    for i, d in enumerate(deps):
        b.append(_box(40, 560 + i * 60, 190, 44, d[:18], "#eaeef2", "#8c959f", 12))
        b.append(_arrow(cx - 120, cy + 20, 230, 582 + i * 60, "归属"))

    # 下：取值聚合
    n_val = len(by_pred.get("取值", []))
    b.append(_box(cx - 150, 660, 300, 46, f"字段取值 {n_val} 项（见 graph.json）",
                  "#fff8c5", "#d4a72c", 13))
    b.append(_arrow(cx, cy + 30, cx, 660, "取值", dash=True))
    b.append('<text x="40" y="752" font-size="13" fill="#57606a">'
             '命中条款由 std_verify 实际判定得到；每条边可回指 clause_id / 字段名 / 资产字段。</text>')
    return _svg_frame(W, H, "".join(b))


# ---------------------------------------------------------------------------
#  DOT（给需要 Graphviz 的场合）
# ---------------------------------------------------------------------------
def to_dot(g: dict) -> str:
    color = {"标准": "#54aeff", "条款": "#4ac26b", "字段": "#d4a72c", "受控表": "#c297ff",
             "资产": "#ff8182", "文档": "#8c959f", "设备": "#8c959f", "部门": "#8c959f"}
    lines = ["digraph kg {", '  rankdir=LR; node [shape=box, style=rounded, fontname="Microsoft YaHei"];']
    for n in g["nodes"]:
        lines.append(f'  "{n["id"]}" [label="{n["label"]}", color="{color.get(n["type"], "#999")}"];')
    for e in g["edges"]:
        lines.append(f'  "{e["s"]}" -> "{e["o"]}" [label="{e["p"]}"];')
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="manufacturing")
    ap.add_argument("--out", default="submission/kg")
    ap.add_argument("--asset", default="AST-MFG-2026-1002")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    spec = load_domain(args.domain)
    g = build_graph(args.domain, spec)
    stats = graph_stats(g, spec["assets"])

    # 命中条款：由 std_verify 实际判定，不手填（图上的"命中"边必须可复算）
    viols: list = []
    try:
        import evostd.server as S

        os.environ.setdefault("EVOSTD_NOW", "2026-10-21")
        S.build_service(args.domain)
        target = next((a for a in spec["assets"]
                       if (a.get("asset_id") or a.get("catalog_id")) == args.asset), None)
        if target is not None:
            r = S.std_verify(json.dumps([target], ensure_ascii=False))
            viols = r["results"][0].get("violations", [])
    except Exception as exc:
        print(f"[kg] 命中条款计算失败（图上不画命中边）：{type(exc).__name__}: {exc}",
              file=sys.stderr)

    with open(os.path.join(args.out, "graph.json"), "w", encoding="utf-8") as f:
        json.dump(g, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, "kg-stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, "graph.dot"), "w", encoding="utf-8") as f:
        f.write(to_dot(g))
    with open(os.path.join(args.out, "kg-schema.svg"), "w", encoding="utf-8") as f:
        f.write(svg_schema())
    with open(os.path.join(args.out, "kg-instance.svg"), "w", encoding="utf-8") as f:
        f.write(svg_instance(g, args.asset, viols))

    print(json.dumps(stats, ensure_ascii=False, indent=1))
    print(f"[kg] 已导出到 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
