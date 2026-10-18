"""制造业 Domain Pack · 样例标准：《工业设备数据资产登记与校验规范（演示样例）》。

⚠️⚠️ **本文全部条款均为「为演示编写」的样例，不是真实标准，不得当作规范引用。**
    标准编号、条款编号、附录内容都是虚构的；只有**条款的形态**（约束形式）是照着
    真实工业规范做的。真实标准在后续阶段替换（行业未定，见 docs/10 DomainPack 架构）。

为什么要有这份东西
------------------
自进化回路里唯一依赖行业的一格是「评估」（docs/10）。评估的靶子来自权威文本，
所以一个 Domain Pack 的第一件交付物就是**一份可被编译成判据的标准**。

本样例刻意把 8 类判据全部用上（每类 ≥2 条），因为判据语言的表达能力
决定了「哪些真值能机械抽取」——也就是**硬锚点覆盖率**（docs/30 第三节）：

    硬锚点覆盖率 = 可机械判定条款 / 全部条款     ↑ 越高，人越省

同时**故意留 3 条不可编译条款**（compilable=False）。它们不是失败，
是「可达编辑集」下一步要扩张的方向（docs/21 元机制）；也是本 Domain Pack
里**软确认**部分的样本——只能靠人一次性点头，搬不到机械端。
"""
from __future__ import annotations

from standard.compiler import Clause, ClauseKind  # 绝对导入：与 standard/demo.py 的跑法一致

# ---------------------------------------------------------------------------
# 标准标识
# ---------------------------------------------------------------------------
STANDARD_NAME = "工业设备数据资产登记与校验规范（演示样例）"
STANDARD_NO = "Q/MFG-DEMO-2026"          # 虚构编号
STANDARD_NOTICE = "★ 本文为演示编写的样例标准，非真实标准，不得作为规范引用。"


# ---------------------------------------------------------------------------
# 条款
# ---------------------------------------------------------------------------
CLAUSES: list[Clause] = [
    # ======================= RANGE · 数值范围 =======================
    Clause("第5条", "检修周期上限", ClauseKind.RANGE,
           "设备数据资产登记的检修周期应为 1 至 365 个自然日之间的整数。",
           {"field": "maintenance_cycle_days", "min": 1, "max": 365},
           standard=STANDARD_NAME),

    Clause("第6条", "数据质量评分区间", ClauseKind.RANGE,
           "设备数据资产的数据质量评分应为 0 至 100 之间的数值。",
           {"field": "quality_score", "min": 0, "max": 100},
           standard=STANDARD_NAME),

    Clause("第7条", "点检间隔区间", ClauseKind.RANGE,
           "点检记录的应检间隔应为 1 至 720 小时之间的整数。",
           {"field": "check_interval_hours", "min": 1, "max": 720},
           standard=STANDARD_NAME),

    Clause("第8条", "图纸修订版次", ClauseKind.RANGE,
           "图纸资料的修订版次应为 0 至 99 之间的整数。",
           {"field": "revision", "min": 0, "max": 99},
           standard=STANDARD_NAME),

    # ======================= ENUM · 受控词表 =======================
    Clause("第9条", "设备类别受控", ClauseKind.ENUM,
           "设备类别应属于受控词表：A类、B类、C类。",
           {"field": "equipment_class", "allowed": ["A类", "B类", "C类"]},
           standard=STANDARD_NAME),

    Clause("第10条", "设备状态受控", ClauseKind.ENUM,
           "设备状态应属于受控词表：在用、备用、检修、停用。",
           {"field": "equipment_status",
            "allowed": ["在用", "备用", "检修", "停用"]},
           standard=STANDARD_NAME),

    Clause("第11条", "点检结论受控", ClauseKind.ENUM,
           "点检结论应属于受控词表：正常、异常、待复检。",
           {"field": "check_result", "allowed": ["正常", "异常", "待复检"]},
           standard=STANDARD_NAME),

    Clause("第12条", "密级受控", ClauseKind.ENUM,
           "数据资产密级应属于受控词表：公开、内部、秘密。",
           {"field": "secrecy", "allowed": ["公开", "内部", "秘密"]},
           standard=STANDARD_NAME),

    Clause("第13条", "资料类型受控", ClauseKind.ENUM,
           "图文资料的类型应属于受控词表：图纸、影像、照片。",
           {"field": "media_type", "allowed": ["图纸", "影像", "照片"]},
           standard=STANDARD_NAME),

    # ======================= PATTERN · 格式约束 =======================
    Clause("第14条", "资产标识格式", ClauseKind.PATTERN,
           "数据资产标识应为“AST-MFG-”前缀加年份与四位流水号。",
           {"field": "asset_id", "pattern": r"AST-MFG-\d{4}-\d{4}"},
           standard=STANDARD_NAME),

    Clause("第15条", "设备资产编号格式", ClauseKind.PATTERN,
           "设备资产编号应为“EQP-”前缀加 7 位数字。",
           {"field": "equipment_no", "pattern": r"EQP-\d{7}"},
           standard=STANDARD_NAME),

    Clause("第16条", "检修工单编号格式", ClauseKind.PATTERN,
           "检修工单编号应为“WO-”前缀加 6 位数字。",
           {"field": "work_order_no", "pattern": r"WO-\d{6}"},
           standard=STANDARD_NAME),

    Clause("第17条", "影像文件哈希格式", ClauseKind.PATTERN,
           "影像或图纸原始文件哈希应为 64 位小写十六进制字符串。",
           {"field": "file_hash", "pattern": r"[0-9a-f]{64}"},
           standard=STANDARD_NAME),

    # ======================= REQUIRED · 必填约束 =======================
    Clause("第18条", "设备台账字段必填", ClauseKind.REQUIRED,
           "设备数据资产应登记设备资产编号，不得为空。",
           {"field": "equipment_no"}, standard=STANDARD_NAME),

    Clause("第19条", "责任部门必填", ClauseKind.REQUIRED,
           "设备数据资产应登记责任部门，不得为空。",
           {"field": "owner_dept"}, standard=STANDARD_NAME),

    Clause("第20条", "设备类别必填", ClauseKind.REQUIRED,
           "设备数据资产应登记设备类别，不得为空。",
           {"field": "equipment_class"}, standard=STANDARD_NAME),

    Clause("第21条", "采集时间必填", ClauseKind.REQUIRED,
           "设备数据资产应登记数据采集日期，不得为空。",
           {"field": "collect_time"}, standard=STANDARD_NAME),

    Clause("第22条", "检修人签名必填", ClauseKind.REQUIRED,
           "检修工单应登记检修人签名，不得为空。",
           {"field": "maintainer"}, standard=STANDARD_NAME),

    # ======================= CONDITIONAL · 条件必填 =======================
    Clause("第23条", "停用设备附加要求", ClauseKind.CONDITIONAL,
           "设备状态为“停用”的设备数据资产，应同时登记停用日期。",
           {"when_field": "equipment_status", "when_value": "停用",
            "require_field": "retire_date"}, standard=STANDARD_NAME),

    Clause("第24条", "秘密级附加要求", ClauseKind.CONDITIONAL,
           "密级为“秘密”的数据资产，应同时登记解密日期。",
           {"when_field": "secrecy", "when_value": "秘密",
            "require_field": "declassify_date"}, standard=STANDARD_NAME),

    Clause("第25条", "影像资料附加要求", ClauseKind.CONDITIONAL,
           "资料类型为“影像”的图文资料，应同时登记标注说明。",
           {"when_field": "media_type", "when_value": "影像",
            "require_field": "annotation_note"}, standard=STANDARD_NAME),

    Clause("第26条", "点检异常附加要求", ClauseKind.CONDITIONAL,
           "点检结论为“异常”的点检记录，应同时登记处置措施。",
           {"when_field": "check_result", "when_value": "异常",
            "require_field": "disposal"}, standard=STANDARD_NAME),

    # ======================= CONSISTENCY · 跨字段一致性 =======================
    # 注意：一致性判据只在**两侧字段都存在且非空**时生效——这是判据语言的
    # 一个刻意的"沉默区"，后面 demo 打印的"盲区"就藏在这里。
    Clause("第27条", "时间序一致性（采集/入库）", ClauseKind.CONSISTENCY,
           "设备数据资产的采集日期不得晚于入库日期。",
           {"left": "collect_time", "right": "ingest_time", "op": "<="},
           standard=STANDARD_NAME),

    Clause("第28条", "时间序一致性（入库/归档）", ClauseKind.CONSISTENCY,
           "设备数据资产的入库日期不得晚于归档日期。",
           {"left": "ingest_time", "right": "archive_time", "op": "<="},
           standard=STANDARD_NAME),

    Clause("第29条", "工单时间序一致性", ClauseKind.CONSISTENCY,
           "检修工单的开工日期不得晚于完工日期。",
           {"left": "work_start", "right": "work_end", "op": "<="},
           standard=STANDARD_NAME),

    # ★ 原写法把它编译成 CONSISTENCY(left=equipment_status, right=retire_date, op="==")，
    #   即"设备状态 == 停用日期"——一句语义完全不同的判据。
    #   是**判据冲突检测**机械发现了这个错（第10条的合规锚点在这条上永远违例）。
    #   正确类型是 FORBID_WHEN：条件成立时某字段**必须为空**。
    Clause("第30条", "在用状态不得登记停用日期", ClauseKind.FORBID_WHEN,
           "设备状态为“在用”的设备数据资产，其停用日期应为空值，不得填写。",
           {"when_field": "equipment_status", "when_value": "在用",
            "forbid_field": "retire_date"},
           standard=STANDARD_NAME),

    Clause("第31条", "安装/投运时间序", ClauseKind.CONSISTENCY,
           "设备的安装日期不得晚于投运日期。",
           {"left": "install_date", "right": "commission_date", "op": "<="},
           standard=STANDARD_NAME),

    # ======================= REFERENCE · 跨文档引用（白名单） =======================
    Clause("第32条", "设备编号受控", ClauseKind.REFERENCE,
           "数据资产引用的设备资产编号，应存在于设备台账受控清单中。",
           {"field": "equipment_no", "registry": "equipment_registry"},
           standard=STANDARD_NAME),

    Clause("第33条", "引用标准受控", ClauseKind.REFERENCE,
           "数据资产引用的标准编号，应存在于受控标准清单中。",
           {"field": "cited_standards", "registry": "controlled_standards"},
           standard=STANDARD_NAME),

    Clause("第34条", "来源资料受控", ClauseKind.REFERENCE,
           "数据资产引用的来源资料编号，应存在于受控资料清单中。",
           {"field": "source_doc_ids", "registry": "controlled_doc_ids"},
           standard=STANDARD_NAME),

    Clause("第35条", "责任部门受控", ClauseKind.REFERENCE,
           "数据资产登记的责任部门，应存在于受控部门清单中。",
           {"field": "owner_dept", "registry": "controlled_depts"},
           standard=STANDARD_NAME),

    # ======================= FORBIDDEN · 禁止取值（黑名单） =======================
    # ★ 这两条是"判据语言扩张"的产物：白名单表达不了"不得引用已停用/已废止的东西"。
    #   它们的存在本身就是 docs/23 那次失败的化石。
    Clause("第36条", "禁止引用已停用设备", ClauseKind.FORBIDDEN,
           "数据资产不得引用已停用设备的资产编号。",
           {"field": "equipment_no", "registry": "retired_equipment"},
           standard=STANDARD_NAME),

    Clause("第37条", "禁止引用已废止标准", ClauseKind.FORBIDDEN,
           "数据资产不得引用已废止的标准编号。",
           {"field": "cited_standards", "registry": "abolished_standards"},
           standard=STANDARD_NAME),

    Clause("第38条", "禁止未授权密级外发", ClauseKind.FORBIDDEN,
           "数据资产的存储位置不得为未授权的公共存储路径。",
           {"field": "storage_uri", "registry": "unauthorized_storage"},
           standard=STANDARD_NAME),

    # ======================= 不可编译条款（软确认区） =======================
    # 这 3 条**只能靠人判断**。它们不是设计缺陷，而是这条成本谱的"贵端"：
    # 外部真值 = 硬锚点（免费） + 软确认（贵）。方向迭代就是不断把真值从贵端搬到便宜端。
    Clause("第41条", "设备状态真实性", ClauseKind.REQUIRED,
           "设备数据资产记录的设备状态应真实反映设备在现场的实际状态，不得虚报。",
           {"field": "_uncompilable_equipment_truth_"},
           standard=STANDARD_NAME, compilable=False),

    Clause("第42条", "检修记录完整性", ClauseKind.REQUIRED,
           "检修工单登记的内容应完整反映本次检修的实际作业过程与更换件情况。",
           {"field": "_uncompilable_maintenance_completeness_"},
           standard=STANDARD_NAME, compilable=False),

    Clause("第43条", "图纸影像真实性", ClauseKind.REQUIRED,
           "图纸与影像资料应真实对应当前在役设备，不得使用与现场不符的历史资料充数。",
           {"field": "_uncompilable_media_authenticity_"},
           standard=STANDARD_NAME, compilable=False),
]


# ---------------------------------------------------------------------------
# 条款分布自检（供 demo 打印；纯函数、无副作用）
# ---------------------------------------------------------------------------
def kind_distribution() -> dict[str, int]:
    """统计可编译条款的类型分布，用来核对"8 类每类 ≥2 条"。"""
    dist: dict[str, int] = {}
    for c in CLAUSES:
        if c.compilable:
            dist[c.kind.value] = dist.get(c.kind.value, 0) + 1
    return dist


def uncompilable_clauses() -> list[Clause]:
    """不可编译条款 = 本 Domain Pack 的『软确认』清单（需要人一次性点头的部分）。"""
    return [c for c in CLAUSES if not c.compilable]
