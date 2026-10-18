"""政务领域包 · 多模态语料与资产（**演示编写，非真实数据**）。

语料里特意写进了**限额表**（更新时限按分级设上限），用来验证
`table_extractor` 的机械抽表能力**不是制造业专属**。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ============================================================================
#  语料（三模态）
# ============================================================================
@dataclass
class TextDoc:
    doc_id: str
    title: str
    text: str


@dataclass
class TableDoc:
    doc_id: str
    title: str
    columns: list[str]
    rows: list[list[Any]]


@dataclass
class MediaDoc:
    doc_id: str
    title: str
    caption: str


DOCS = [
    TextDoc("GOV-DOC-001", "政务数据目录编制规程（片段）",
            "政务数据目录标识应唯一，格式为 GOV- 前缀加 6 位数字。"
            "数据分级分为 公开、内部、秘密；公开级可对外开放，秘密级须登记解密日期。"
            "数据提供方应为市数据局、市财政局、市人社局、市卫健委、市市场监管局之一。"
            "更新时限 公开级 不超过 7 天，内部级 不超过 30 天，秘密级 不超过 90 天。"),
    TextDoc("GOV-DOC-002", "政务数据共享管理规程（片段）",
            "共享类型分为 无条件共享、有条件共享、不予共享。"
            "无条件共享的数据不得附加共享条件；有条件共享的数据应登记共享条件与审批依据。"
            "数据来源系统应在已登记信息系统清单内，不得引用已下线系统。"),
    TextDoc("GOV-DOC-003", "政务数据开放与安全审查规程（片段）",
            "开放属性分为 无条件开放、有条件开放、不予开放。"
            "秘密级数据不得对外开放。来源文件哈希应为 64 位小写十六进制。"
            "数据采集日期不得晚于入库日期，入库日期不得晚于发布日期。"),
    TableDoc("GOV-TBL-101", "政务数据目录登记表",
             ["catalog_id", "catalog_name", "provider", "data_level", "field_count"],
             [["GOV-000001", "人口基础信息", "市数据局", "公开", 42],
              ["GOV-000002", "财政预算执行", "市财政局", "内部", 88],
              ["GOV-000003", "参保人员信息", "市人社局", "秘密", 156]]),
    TableDoc("GOV-TBL-102", "数据共享审批表",
             ["catalog_id", "share_type", "share_condition", "open_type"],
             [["GOV-000001", "无条件共享", "", "无条件开放"],
              ["GOV-000002", "有条件共享", "需市级审批", "有条件开放"]]),
    MediaDoc("GOV-IMG-201", "政务数据共享流程图（第 2 版）",
             "标注：数据提供方→数据局→使用方，三处审批节点已着色"),
]


def doc_raw_text(d) -> str:
    if isinstance(d, TextDoc):
        return d.text
    if isinstance(d, TableDoc):
        parts = [" | ".join(d.columns)]
        for r in d.rows:
            parts.append(" | ".join(str(x) for x in r))
        return "\n".join(parts)
    return getattr(d, "caption", "")


def all_docs() -> list:
    return DOCS


def corpus_term_universe() -> dict:
    """语料侧术语（机械计数，不手写）。"""
    terms = ["政务数据", "数据目录", "数据分级", "共享类型", "开放属性",
             "更新时限", "提供方", "责任处室", "解密日期", "共享条件",
             "来源系统", "入库日期", "发布日期", "字段数量"]
    blob = "".join(doc_raw_text(d) for d in DOCS)
    return {t: blob.count(t) for t in terms if blob.count(t) > 0}


def build_ctx() -> dict:
    from domains.gov_catalog.standard_gov import controlled_lists
    return dict(controlled_lists())
