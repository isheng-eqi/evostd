"""演示样例：《工业数据资产登记与校验规范》（**演示样例，非真实标准**）。

为什么需要它：自进化需要靶子，而靶子必须来自权威文本。
真实标准在 P3 阶段替换（行业未定）；这里先把**条款的形态**做真——
七类可编译条款 + 两条不可编译条款，覆盖真实规范里能见到的绝大多数约束形式。

⚠️ 本文所有条款均为**为演示编写的样例**，不得当作真实标准引用。
"""
from __future__ import annotations

from .compiler import Clause, ClauseKind

STANDARD_NAME = "工业数据资产登记与校验规范（演示样例）"

CLAUSES: list[Clause] = [
    Clause("第5条", "资产标识格式", ClauseKind.PATTERN,
           "资产标识应为全局唯一字符串，由 8 至 64 位字母、数字或连字符组成。",
           {"field": "asset_id", "pattern": r"[A-Za-z0-9\-]{8,64}"},
           standard=STANDARD_NAME),

    Clause("第6条", "资产模态受控", ClauseKind.ENUM,
           "数据资产的模态应属于受控词表：文本、表格、图文、影像。",
           {"field": "modality", "allowed": ["文本", "表格", "图文", "影像"]},
           standard=STANDARD_NAME),

    Clause("第7条", "密级受控", ClauseKind.ENUM,
           "数据资产密级应属于受控词表：公开、内部、秘密。",
           {"field": "secrecy", "allowed": ["公开", "内部", "秘密"]},
           standard=STANDARD_NAME),

    Clause("第8条", "责任部门必填", ClauseKind.REQUIRED,
           "数据资产应登记责任部门，不得为空。",
           {"field": "owner_dept"}, standard=STANDARD_NAME),

    Clause("第9条", "秘密级附加要求", ClauseKind.CONDITIONAL,
           "密级为“秘密”的数据资产，应同时登记解密日期。",
           {"when_field": "secrecy", "when_value": "秘密", "require_field": "declassify_date"},
           standard=STANDARD_NAME),

    Clause("第10条", "影像模态附加要求", ClauseKind.CONDITIONAL,
           "资产若包含影像模态，应同时登记标注说明。",
           {"when_field": "modality", "when_value": "影像", "require_field": "annotation_note"},
           standard=STANDARD_NAME),

    Clause("第11条", "时间序一致性", ClauseKind.CONSISTENCY,
           "数据资产的采集时间不得晚于入库时间。",
           {"left": "collect_time", "right": "ingest_time", "op": "<="},
           standard=STANDARD_NAME),

    Clause("第12条", "质量评分区间", ClauseKind.RANGE,
           "数据资产质量评分应为 0 至 100 之间的数值。",
           {"field": "quality_score", "min": 0, "max": 100}, standard=STANDARD_NAME),

    Clause("第13条", "字段完整度区间", ClauseKind.RANGE,
           "字段完整度应为 0 至 1 之间的小数。",
           {"field": "completeness", "min": 0.0, "max": 1.0}, standard=STANDARD_NAME),

    Clause("第14条", "原始文件哈希格式", ClauseKind.PATTERN,
           "原始文件哈希应为 64 位小写十六进制字符串。",
           {"field": "file_hash", "pattern": r"[0-9a-f]{64}"}, standard=STANDARD_NAME),

    Clause("第15条", "引用标准受控", ClauseKind.REFERENCE,
           "数据资产引用的标准编号，应存在于受控标准清单中。",
           {"field": "cited_standards", "registry": "controlled_standards"},
           standard=STANDARD_NAME),

    Clause("第16条", "存储位置必填", ClauseKind.REQUIRED,
           "数据资产应登记存储位置，不得为空。",
           {"field": "storage_uri"}, standard=STANDARD_NAME),

    # ---- 以下两条**故意不可编译**：它们是"可达编辑集"下一步要扩张的方向 ----
    Clause("第20条", "业务真实性", ClauseKind.REQUIRED,
           "数据资产应真实反映其所描述的业务过程，不得虚构。",
           {"field": "_uncompilable_business_truth_"},
           standard=STANDARD_NAME, compilable=False),

    Clause("第21条", "描述清晰完整", ClauseKind.REQUIRED,
           "数据资产的描述信息应清晰、完整，便于跨部门理解与复用。",
           {"field": "_uncompilable_description_quality_"},
           standard=STANDARD_NAME, compilable=False),
]


# 受控标准清单（第15条要用）
CONTROLLED_STANDARDS = [
    "GB/T 33863.1-2017", "GB/T 42131-2022", "GB/T 41867-2022",
    "IEC 62264", "IEC 62541",
]

# 演示资产
GOOD_ASSET = {
    "asset_id": "AST-2026-0001", "modality": "表格", "secrecy": "内部",
    "owner_dept": "设备管理部", "collect_time": "2026-03-01", "ingest_time": "2026-03-05",
    "quality_score": 87.5, "completeness": 0.93,
    "file_hash": "a" * 64, "cited_standards": ["GB/T 33863.1-2017"],
    "storage_uri": "oss://assets/2026/0001.csv",
}

BAD_ASSETS: list[tuple[str, dict]] = [
    ("标识格式不合规 + 密级不在受控词表", {
        **GOOD_ASSET, "asset_id": "短", "secrecy": "机密"}),

    ("秘密级但缺解密日期 + 影像模态缺标注说明", {
        **GOOD_ASSET, "secrecy": "秘密", "modality": "影像"}),

    ("采集时间晚于入库时间 + 质量评分越界", {
        **GOOD_ASSET, "collect_time": "2026-04-01", "ingest_time": "2026-03-05",
        "quality_score": 130}),

    ("引用了受控清单外的标准 + 必填字段为空", {
        **GOOD_ASSET, "cited_standards": ["GB/T 99999-2099"], "owner_dept": "  "}),

    ("哈希格式错误 + 完整度越界", {
        **GOOD_ASSET, "file_hash": "ZZZ", "completeness": 1.8}),
]
