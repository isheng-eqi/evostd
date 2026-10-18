"""CLI：把一部标准文本半自动摄入成判据库，并给出**人工待办清单**。

用法：
    python tools/ingest_standard.py <标准文本文件> [--name "标准名"] [--domain gov_catalog]

它回答的问题是：**给我一部真实标准，多久能变成可跑的判据库？**

输出四段：
  ① 切分与归类结果（每条条款 → 判据类型）
  ② 编译率与判据类型分布
  ③ 硬锚点覆盖率与自检（能机械验证多少）
  ④ ★ 人工待办清单（机器搞不定的，缺什么、要补什么）
"""
from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))   # ★ 代码在 项目/src 下

from standard.anchors_v2 import coverage_v2, derive_hard_anchors_v2, verify_anchors_v2
from standard.compiler import compile_standard
from standard.ingest import ingest
from standard.table_extractor import FIELD_ALIASES, KEY_FIELD_BY_DOMAIN


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--name", default="（未命名标准）")
    ap.add_argument("--domain", default="")
    ap.add_argument("--registries", default="", help="逗号分隔的受控清单名")
    ap.add_argument("--aliases", default="", help="别名表 JSON 文件（中文名→字段名）")
    ap.add_argument("--ctx", default="", help="外部真值 JSON（受控清单内容 + now）")
    ap.add_argument("--id-field", default="asset_id",
                    help="资产的标识字段名（制造业 asset_id / 政务 catalog_id）")
    a = ap.parse_args()

    text = open(a.path, encoding="utf-8").read()
    aliases = dict(FIELD_ALIASES)
    if a.aliases and os.path.exists(a.aliases):
        import json as _json
        # ★ 用 utf-8-sig：Windows 上生成的 JSON 常带 BOM，utf-8 会直接报错
        aliases.update(_json.load(open(a.aliases, encoding="utf-8-sig")))
    regs = {x.strip() for x in a.registries.split(",") if x.strip()}

    clauses, rep = ingest(text, standard_name=a.name, aliases=aliases,
                          registry_names=regs, id_field=a.id_field)
    s = rep.summary()
    W = 78
    print("=" * W)
    print("标准摄入报告")
    print(f"来源：{a.path}")
    print(f"标准名：{a.name}")
    print("=" * W)

    print("\n① 切分与归类")
    for c in rep.clauses:
        k = (getattr(c.kind, "value", str(c.kind))) if c.compilable else "不可编译"
        flag = "✓" if c.compilable else "○"
        print(f"  {flag} {c.clause_id:<8} [{k:<8}] {c.text[:52]}")

    print(f"\n② 编译结果：{s['n_clauses']} 条 → 可编译 {s['n_compilable']} "
          f"（{s['compile_rate']*100:.1f}%）")
    print("   判据类型分布：" + ", ".join(f"{k}×{v}" for k, v in sorted(s["kind_stats"].items())))

    if s["n_compilable"]:
        c = compile_standard([x for x in clauses if x.compilable])
        # ★ 抽表器与基准时间判据都要外部真值：受控清单内容 + 基准时间。
        #   缺了它们，跨文档引用类锚点无法自检、基准时间类锚点直接弃权——
        #   这不是失败，是**如实反映"还缺哪些外部真值"**。
        ctx = {}
        if a.ctx and os.path.exists(a.ctx):
            import json as _json
            ctx.update(_json.load(open(a.ctx, encoding="utf-8-sig")))
        if a.domain:
            kf = KEY_FIELD_BY_DOMAIN.get(a.domain)
            if kf:
                ctx["_key_field_hint"] = kf
        if not ctx.get("now"):
            print("\n   ⚠ 未提供基准时间（--ctx 里的 now）→ 基准时间类判据将弃权")
        try:
            _base = {a.id_field: "BASE-000001"}      # 基础夹具用本领域的标识字段
            anchors = derive_hard_anchors_v2([x for x in clauses if x.compilable], _base, ctx)
            cov = coverage_v2([x for x in clauses if x.compilable], anchors)
            ver = verify_anchors_v2(anchors, [x for x in clauses if x.compilable], ctx)
            print(f"\n③ 硬锚点：{cov['n_anchors']} 条 | "
                  f"覆盖 {cov['n_with_anchor']}/{cov['n_compilable']} = {cov['rate']:.4f} | "
                  f"自检不一致 {ver['n_mismatch']}")
            print("   按类型：" + ", ".join(f"{k} {v}" for k, v in cov["per_kind"].items()))
        except Exception as e:
            print(f"\n③ 硬锚点：★ 生成失败（{type(e).__name__}: {e}）")

    print(f"\n④ ★ 人工待办清单（{s['n_todo']} 项）——机器搞不定的部分")
    if not rep.todo:
        print("   无。全部条款已机械编译。")
    for t in rep.todo:
        print(f"   · {t['no']:<8} 卡在：{t['blocked_by']}")
        print(f"     需要人补：{t['need']}")
        print(f"     原文：{t['text']}")
    print("=" * W)
    print(f"结论：一部 {s['n_clauses']} 条的标准，"
          f"机械编译 {s['n_compilable']} 条（{s['compile_rate']*100:.1f}%），"
          f"另有 {s['n_todo']} 项需要人一次性补齐。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
