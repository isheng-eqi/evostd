"""政务领域包 · 样例标准（**演示编写，非真实标准**）。

用途：验证 Domain Pack 的**可插拔性** —— 换行业只换这个包，
内核（`src/evolve`）、标准编译器（`src/standard`）、MCP 服务（`src/evostd`）**一行不改**。

与制造业包的差异（故意设计得不同，才测得出插拔性）：
  · 条款类型分布不同（政务更重"目录/共享属性/开放条件"）
  · 受控词表不同（数据分级、共享类型、开放属性都是政务特有）
  · 埋了**两类本领域特有的盲区**，验证语言扩张是否通用
"""
from __future__ import annotations

from standard.compiler import Clause, ClauseKind
from standard.grammar_v2 import ClauseKindV2

STANDARD_NAME = "政务数据目录编制与共享规范（演示样例）"
STANDARD_NO = "Q/GOV-DEMO-2026"


def standards() -> list[Clause]:
    S = STANDARD_NAME
    C = []
    n = [0]

    def add(title, kind, text, params, compilable=True):
        n[0] += 1
        C.append(Clause(f"第{n[0]}条", title, kind, text, params,
                        standard=S, compilable=compilable))

    # ── 受控词表（政务特有的三张表）─────────────────────────────
    add("数据分级受控", ClauseKind.ENUM,
        "政务数据的数据分级应属于受控词表：公开、内部、秘密。",
        {"field": "data_level", "allowed": ["公开", "内部", "秘密"]})
    add("共享类型受控", ClauseKind.ENUM,
        "政务数据的共享类型应属于受控词表：无条件共享、有条件共享、不予共享。",
        {"field": "share_type", "allowed": ["无条件共享", "有条件共享", "不予共享"]})
    add("开放属性受控", ClauseKind.ENUM,
        "政务数据的开放属性应属于受控词表：无条件开放、有条件开放、不予开放。",
        {"field": "open_type", "allowed": ["无条件开放", "有条件开放", "不予开放"]})
    add("更新周期受控", ClauseKind.ENUM,
        "政务数据的更新周期应属于受控词表：实时、每日、每周、每月、每季、每年。",
        {"field": "update_cycle", "allowed": ["实时", "每日", "每周", "每月", "每季", "每年"]})

    # ── 数值范围 ────────────────────────────────────────────────
    add("字段数量范围", ClauseKind.RANGE,
        "政务数据目录的字段数量应为 1 至 500 之间的整数。",
        {"field": "field_count", "min": 1, "max": 500})
    add("数据量范围", ClauseKind.RANGE,
        "政务数据目录登记的数据量应为 0 至 1000000 之间的整数。",
        {"field": "record_count", "min": 0, "max": 1000000})

    # ── 格式约束 ────────────────────────────────────────────────
    add("目录标识格式", ClauseKind.PATTERN,
        "政务数据目录标识应为“GOV-”前缀加 6 位数字。",
        {"field": "catalog_id", "pattern": r"GOV-\d{6}"})
    add("提供方代码格式", ClauseKind.PATTERN,
        "数据提供方代码应为 6 位大写字母或数字。",
        {"field": "provider_code", "pattern": r"[A-Z0-9]{6}"})
    add("文件哈希格式", ClauseKind.PATTERN,
        "原始文件哈希应为 64 位小写十六进制字符串。",
        {"field": "file_hash", "pattern": r"[0-9a-f]{64}"})

    # ── 必填 / 条件必填 ─────────────────────────────────────────
    add("目录名称必填", ClauseKind.REQUIRED,
        "政务数据目录应登记目录名称，不得为空。", {"field": "catalog_name"})
    add("提供方必填", ClauseKind.REQUIRED,
        "政务数据目录应登记数据提供方，不得为空。", {"field": "provider"})
    add("责任处室必填", ClauseKind.REQUIRED,
        "政务数据目录应登记责任处室，不得为空。", {"field": "owner_dept"})
    add("秘密级须登记解密日期", ClauseKind.CONDITIONAL,
        "数据分级为“秘密”的政务数据，应同时登记解密日期。",
        {"when_field": "data_level", "when_value": "秘密", "require_field": "declassify_date"})
    add("有条件共享须登记共享条件", ClauseKind.CONDITIONAL,
        "共享类型为“有条件共享”的政务数据，应同时登记共享条件。",
        {"when_field": "share_type", "when_value": "有条件共享", "require_field": "share_condition"})

    # ── 条件禁止（第4类语言）────────────────────────────────────
    add("无条件共享不得附条件", ClauseKindV2.FORBID_WHEN,
        "共享类型为“无条件共享”的政务数据，其共享条件应为空值，不得填写。",
        {"when_field": "share_type", "when_value": "无条件共享",
         "forbid_field": "share_condition"})

    # ── 跨字段一致性 ────────────────────────────────────────────
    add("采集不晚于入库", ClauseKind.CONSISTENCY,
        "政务数据的采集日期不得晚于入库日期。",
        {"left": "collect_date", "right": "ingest_date", "op": "<="})
    add("入库不晚于发布", ClauseKind.CONSISTENCY,
        "政务数据的入库日期不得晚于发布日期。",
        {"left": "ingest_date", "right": "publish_date", "op": "<="})

    # ── 跨文档引用 / 禁止取值 ───────────────────────────────────
    add("提供方须在名录内", ClauseKind.REFERENCE,
        "数据提供方应存在于政务部门名录中。",
        {"field": "provider", "registry": "gov_agencies"})
    add("来源系统须在册", ClauseKind.REFERENCE,
        "来源系统应存在于已登记信息系统清单中。",
        {"field": "source_system", "registry": "registered_systems"})
    add("不得引用已下线系统", ClauseKind.FORBIDDEN,
        "政务数据不得引用已下线信息系统的数据。",
        {"field": "source_system", "registry": "retired_systems"})

    # ── 不可编译（软确认区）────────────────────────────────────
    add("数据真实性", ClauseKind.REQUIRED,
        "政务数据目录登记的内容应真实反映业务实际，不得虚报。", {},
        compilable=False)
    add("更新及时性", ClauseKind.REQUIRED,
        "政务数据应按照登记的更新周期及时更新，不得长期不更新。", {},
        compilable=False)

    return C


# 政务特有的受控清单（对应制造业的 equipment_registry 等）
def controlled_lists() -> dict:
    return {
        "gov_agencies": ["市数据局", "市财政局", "市人社局", "市卫健委", "市市场监管局"],
        "registered_systems": ["SYS-000001", "SYS-000002", "SYS-000003", "SYS-000004"],
        "retired_systems": ["SYS-900001", "SYS-900002"],
        "controlled_doc_ids": ["GOV-DOC-001", "GOV-DOC-002", "GOV-TBL-101", "GOV-IMG-201"],
        "unauthorized_storage": ["/tmp/gov-open"],
    }
