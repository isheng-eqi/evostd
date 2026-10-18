"""政务领域包（Domain Pack）—— 第二个行业的实现，用于验证**可插拔性**。

契约（与制造业包完全一致，内核只认这四件事）：
    standards()       → list[Clause]
    docs()            → list[文档对象]（多模态）
    build_verifier()  → 可执行验证器
    narrative()       → 一张自描述卡片

★ 本包**不改动** `src/evolve`、`src/standard`、`src/evostd` 的任何一行。
  换行业 = 换这个目录。这就是 Domain Pack 架构主张的硬检验。
"""
from __future__ import annotations

from domains.gov_catalog.assets import all_assets, blind_spot_report, ctx
from domains.gov_catalog.corpus import (all_docs, build_ctx, corpus_term_universe,
                                        doc_raw_text)
from domains.gov_catalog.standard_gov import (STANDARD_NAME, STANDARD_NO,
                                              controlled_lists, standards)

PACK_NAME = "gov_catalog"
INDUSTRY = "政务（数据目录与共享）"


def docs() -> list:
    return all_docs()


def build_verifier():
    from standard.compiler import compile_standard
    return compile_standard(standards())


def narrative() -> dict:
    std = standards()
    compilable = [c for c in std if c.compilable]
    return {
        "pack": PACK_NAME,
        "industry": INDUSTRY,
        "standard": STANDARD_NAME,
        "standard_no": STANDARD_NO,
        "n_clauses": len(std),
        "n_compilable": len(compilable),
        "n_uncompilable": len(std) - len(compilable),
        "n_assets": len(all_assets()),
        "n_docs": len(all_docs()),
        "modalities": sorted({type(d).__name__ for d in all_docs()}),
        "controlled_lists": sorted(controlled_lists().keys()),
        "corpus_terms": len(corpus_term_universe()),
    }


__all__ = ["PACK_NAME", "INDUSTRY", "STANDARD_NAME", "STANDARD_NO",
           "standards", "docs", "build_verifier", "narrative",
           "all_assets", "ctx", "blind_spot_report",
           "all_docs", "doc_raw_text", "build_ctx", "corpus_term_universe",
           "controlled_lists"]
