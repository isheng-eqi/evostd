"""制造业 Domain Pack：把《工业设备数据资产登记与校验规范（演示样例）》编译成判据。

⚠️ 本包内全部标准、语料、资产均为**演示编写的样例**，不是真实数据，不得对外引用。

对外接口（对齐 docs/10 的 DomainPack 协议）
------------------------------------------
    from domains.manufacturing import (
        CLAUSES, STANDARD_NAME,          # standards()：要与之对齐的标准体系
        docs,                            # corpus()：多模态语料
        build_verifier,                  # ★ build_verifier()：外部、确定性、可 held-out
        all_assets,                      # 带 _label/_why 的资产（自进化适应度的样本）
        blind_spot_report,               # ★ 盲区实测：哪些违规现有判据测不到
        narrative,                       # narrative()：业务价值叙事
    )

    verifier = build_verifier()           # 一次编译，之后是纯函数
    verifier.compliance(asset, ctx)       # 合规率 = 适应度，确定性、可回指条款

一句话分工
----------
    硬锚点：CLAUSES 里 40 条可机械判定的条款 + 语料侧术语覆盖率
    软确认：3 条不可编译条款 + 4 类必须人确认的盲区规则
"""
from __future__ import annotations

from typing import Any

from standard.compiler import (
    Clause, ClauseKind, CompileResult, Finding, StandardVerifier, compile_standard,
)

from .assets import (
    ASSETS_NOTICE,
    COMPLIANT_ASSETS,
    VIOLATING_ASSETS,
    all_assets,
    asset_text,
    blind_spot_kinds,
    blind_spot_report,
    caught_looking_blind,
    class_cycle_synthetic,
    class_interval_synthetic,
    cross_asset_blind_spots,
    ctx,
    false_positive_report,
)
from .corpus import (
    ABOLISHED_STANDARDS,
    CONTROLLED_DEPTS,
    CONTROLLED_DOC_IDS,
    CONTROLLED_STANDARDS,
    CORPUS_NOTICE,
    EQUIPMENT_REGISTRY,
    MEDIA_DOCS,
    RETIRED_EQUIPMENT,
    TABLE_DOCS,
    TEXT_DOCS,
    UNAUTHORIZED_STORAGE,
    all_docs,
    audit_declared_terms,
    build_ctx,
    corpus_term_universe,
    term_coverage,
    term_frequency,
)
from .standard_industrial import (
    STANDARD_NAME,
    STANDARD_NOTICE,
    STANDARD_NO,
    CLAUSES,
    kind_distribution,
    uncompilable_clauses,
)

# Domain Pack 标识
PACK_NAME = "manufacturing"
PACK_TITLE = "制造业 · 工业设备数据资产认知"


# ---------------------------------------------------------------------------
#  DomainPack 协议实现（四个方法，与 docs/10 一一对应）
# ---------------------------------------------------------------------------
def standards() -> list[Clause]:
    """标准：本包要与之对齐的条款集（编译前的原文形态）。"""
    return list(CLAUSES)


def docs() -> list[Any]:
    """语料：多模态（文本 / 表格 / 图文）。"""
    return all_docs()


def build_verifier() -> StandardVerifier:
    """★ 外部、确定性、可 held-out 的验证器。

    这是整个 Domain Pack 唯一的"行业进入自进化回路"的落点（docs/10 第二节）。
    它不含任何模型自评：同输入同输出，违例一律回指到条款原文。
    """
    return StandardVerifier(compile_standard(CLAUSES))


def compile_pack() -> CompileResult:
    """编译结果（含 uncompilable —— 软确认区的入口）。"""
    return compile_standard(CLAUSES)


def verify(asset: dict, context: dict | None = None) -> list[Finding]:
    """一次性的便捷校验（等价于 build_verifier().verify(...)）。"""
    return build_verifier().verify(asset, context or ctx())


def compliance(asset: dict, context: dict | None = None) -> float:
    """合规率 = 适应度。确定性、可回指条款、不含模型自评。"""
    return build_verifier().compliance(asset, context or ctx())


def narrative() -> str:
    """业务价值叙事（给人看的，不参与裁决）。"""
    return (
        "制造现场的设备台账、点检记录、检修工单、图纸影像，长期以'沉睡资产'的形态"
        "散落在台账本、点检表和共享盘里：字段在，但字段背后的规范没有被机器读懂。\n"
        "本 Domain Pack 把《工业设备数据资产登记与校验规范（演示样例）》编译成 "
        "40 条可执行判据 + 一张受控清单（设备台账 / 受控标准 / 部门 / 已废止标准 / "
        "未授权存储路径），于是每一份设备资料都能被机械化地判一次对错，"
        "并且每一条违例都能回指到条款原文。\n"
        "更关键的是它**承认自己测不到什么**：3 条只能靠人判断的条款，以及 4 类"
        "现有判据语言表达不了的盲区（已停用设备仍被引用、跨三字段的时间序全序、"
        "按设备类别查表的检修周期上限…）。这些盲区就是判据语言下一步扩张的入口——"
        "人的工作量与盲区数量成正比，而**不与标准规模成正比**。"
    )


__all__ = [
    # 标识
    "PACK_NAME", "PACK_TITLE",
    "STANDARD_NAME", "STANDARD_NO", "STANDARD_NOTICE", "CORPUS_NOTICE", "ASSETS_NOTICE",
    # 标准
    "CLAUSES", "Clause", "ClauseKind", "CompileResult", "Finding", "StandardVerifier",
    "kind_distribution", "uncompilable_clauses",
    # 语料
    "docs", "all_docs", "TEXT_DOCS", "TABLE_DOCS", "MEDIA_DOCS",
    "EQUIPMENT_REGISTRY", "RETIRED_EQUIPMENT", "CONTROLLED_STANDARDS",
    "ABOLISHED_STANDARDS", "CONTROLLED_DOC_IDS", "CONTROLLED_DEPTS",
    "UNAUTHORIZED_STORAGE", "build_ctx", "corpus_term_universe", "term_coverage",
    "term_frequency", "audit_declared_terms",
    # 资产
    "all_assets", "COMPLIANT_ASSETS", "VIOLATING_ASSETS", "ctx", "asset_text",
    "blind_spot_report", "blind_spot_kinds", "class_cycle_synthetic",
    "class_interval_synthetic",
    "false_positive_report", "caught_looking_blind", "cross_asset_blind_spots",
    # 协议
    "standards", "build_verifier", "compile_pack", "verify", "compliance", "narrative",
]
