"""制造业 Domain Pack · 小型多模态语料（演示用，不需要真图片）。

⚠️ 本文件全部内容为**演示编写的样例**，标准编号、设备编号、人名均为虚构。

这个语料在回路里的位置
----------------------
它**不是**判据的来源（判据来自 `standard_industrial.CLAUSES`）。
它承担两件事（见 docs/21 的验证器三条）：
  ① 给「语料侧术语覆盖率审计」提供**外部、不依赖本体**的真值 —— 一条资产
     有没有说清楚，可以用"它覆盖了多少语料侧术语"机械地量出来；
  ② 给判据提供**受控清单**（白名单/黑名单）：设备台账、受控标准、受控资料、
     受控部门、已停用设备、已废止标准。**判据读的 ctx 就是从这里组装的。**

为什么"语料侧术语覆盖率"是硬锚点友好的
--------------------------------------
术语表是从语料里**机械数出来**的（`term_frequency()` 就是 str.count），
不需要任何人工标注，也不依赖本体设计得好不好 → 它是免费的硬锚点。

★ 本文件的一条自律规则：**术语频次一律由机械计数生成，绝不手写。**
  手写的频次会不自觉写成"希望它出现几次"。`audit_declared_terms()` 就是这条
  规则的执行者：它用同一把尺子重新数一遍，不一致就报出来。实测发现，第一版
  手写的 9 份语料里有 10 份对不上——这条检查被保留下来，不进仓库的只有手写本身。

多模态的三种形态（用结构化描述代替真实文件）
--------------------------------------------
  文本类  TextDoc   —— 规范/规程片段（标题 + 正文）
  表格类  TableDoc  —— 设备台账表、点检表（list[dict] + 列定义）
  图文类  MediaDoc  —— 图纸/影像资料（字段描述内容与标注）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


CORPUS_NOTICE = "★ 演示语料：编号、设备、人名均为虚构，不是真实台账。"


# ===========================================================================
#  受控清单（判据的 ctx 白名单/黑名单就来自这里）
# ===========================================================================
# 设备台账受控清单（REFERENCE 白名单）—— 含已停用设备：已停用 ≠ 不存在，
# 它仍在台账里，但被 FORBIDDEN 单独禁掉。这个区分是刻意的（见 docs/23 教训：
# 白名单表达不了"不得引用已废止的东西"，所以必须再加一类黑名单判据）。
EQUIPMENT_REGISTRY: list[str] = [
    "EQP-1000001", "EQP-1000002", "EQP-1000003", "EQP-1000004",
    "EQP-1000005", "EQP-1000007", "EQP-1000011",
]

# ★ 已停用设备（FORBIDDEN 黑名单）—— 注意它们同时在上面的白名单里
RETIRED_EQUIPMENT: list[str] = ["EQP-1000007", "EQP-1000011"]

# 受控标准清单（REFERENCE 白名单）
# ⚠️ 演示用：编号照真实 GB/T、IEC、ISO 的写法编，但"哪几本在受控范围内"是虚构的。
CONTROLLED_STANDARDS: list[str] = [
    "GB/T 33863.1-2017",     # OPC UA 系列（演示引用）
    "GB/T 42131-2022",       # 人工智能（演示引用）
    "GB/T 41867-2022",       # 信息技术（演示引用）
    "GB/T 32893-2016",       # 机械振动（演示引用）
    "GB/T 6075.1-2012",      # 振动评价（演示引用）
    "IEC 62264-1:2013",      # 企业控制系统集成
    "IEC 62541-1:2020",      # OPC UA
    "ISO 14224:2016",        # 设备可靠性数据收集
    "ISO 10816-3:2009",      # 振动
]

# ★ 已废止标准（FORBIDDEN 黑名单）—— 曾经在受控清单里，现已废止，不得再引用。
#   注意：它们**不在** CONTROLLED_STANDARDS 里，所以第33条白名单也会拦住它们；
#   第37条黑名单的价值在于：废止信息是**独立的一份清单**，可以单独被替换/审计。
ABOLISHED_STANDARDS: list[str] = [
    "GB/T 19001-2008",       # 已由 GB/T 19001-2016 代替（演示）
    "JB/T 8830-1999",        # 已被代替（演示）
    "IEC 61131-2:2003",      # 旧版（演示）
]

# 受控资料清单（REFERENCE 白名单）—— 语料里的文本与表格资料编号
CONTROLLED_DOC_IDS: list[str] = [
    "DOC-MFG-001", "DOC-MFG-002", "DOC-MFG-003", "DOC-MFG-004",
    "TBL-MFG-101", "TBL-MFG-102", "TBL-MFG-103",
    "IMG-MFG-201", "IMG-MFG-202", "IMG-MFG-203",
]

# 受控部门清单（REFERENCE 白名单）
# 注意 安全环保部 也在清单里：受控 ≠ 每个部门都能当设备责任人，
# 这条清单只回答"这个部门名存不存在于组织清单中"。
CONTROLLED_DEPTS: list[str] = [
    "设备管理部", "检修车间", "点检站", "工艺技术部", "安全环保部",
]

# ★ 未授权存储路径（FORBIDDEN 黑名单）—— 演示：公共网盘 / 个人目录
UNAUTHORIZED_STORAGE: list[str] = [
    "public://pan.example.com/upload",
    "http://pan.example.com/s/abc123",
    "C:/Users/public/Desktop/设备资料",
]


def build_ctx() -> dict[str, Any]:
    """组装判据运行所需的 ctx（键名必须与条款 params['registry'] 严格一致）。"""
    return {
        "equipment_registry": EQUIPMENT_REGISTRY,
        "retired_equipment": RETIRED_EQUIPMENT,
        "controlled_standards": CONTROLLED_STANDARDS,
        "abolished_standards": ABOLISHED_STANDARDS,
        "controlled_doc_ids": CONTROLLED_DOC_IDS,
        "controlled_depts": CONTROLLED_DEPTS,
        "unauthorized_storage": UNAUTHORIZED_STORAGE,
    }


# ===========================================================================
#  术语计数的唯一一把尺子
# ===========================================================================
def term_frequency(text: str, terms: Iterable[str]) -> dict[str, int]:
    """机械计数：某段文本里每个术语出现的次数。**纯函数，无模型、无标注。**"""
    return {t: text.count(t) for t in terms if text.count(t) > 0}


# ===========================================================================
#  语料文档模型（三种模态共用同一接口：doc_id / modality / terms / raw_text）
# ===========================================================================
def _autoterms(doc: Any, vocab: list[str]) -> dict[str, int]:
    """★ 由机械计数生成术语表——本文件里**没有一条手写的频次**。"""
    return term_frequency(doc.raw_text(), vocab)


@dataclass
class TextDoc:
    """文本类语料：一段规范/规程。raw 是原文。"""
    doc_id: str
    title: str
    raw: str
    modality: str = "文本"
    vocab: list[str] = field(default_factory=list)   # 候选术语（计数后丢掉频次为 0 的）
    terms: dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.terms = _autoterms(self, self.vocab)

    def raw_text(self) -> str:
        return f"{self.title} {self.raw}"

    def terms_list(self) -> list[str]:
        return list(self.terms.keys())


@dataclass
class TableDoc:
    """表格类语料：list[dict] 数据 + 列定义（列名 → 含义/取值约束）。"""
    doc_id: str
    title: str
    columns: dict[str, str]
    rows: list[dict]
    modality: str = "表格"
    vocab: list[str] = field(default_factory=list)
    terms: dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.terms = _autoterms(self, self.vocab)

    def raw_text(self) -> str:
        """表格的"原文"= 标题 + 列名 + 行内取值。

        ⚠️ 刻意**不含列定义的解释文本**：解释文本是人写的说明，把它算进原文会让
        "术语出现次数"取决于解释写得多啰嗦。取值才是表里真实的内容。
        """
        parts: list[str] = [self.title, " ".join(self.columns.keys())]
        for r in self.rows:
            parts += [str(v) for v in r.values() if v is not None]
        return " ".join(parts)

    def terms_list(self) -> list[str]:
        return list(self.terms.keys())


@dataclass
class MediaDoc:
    """图文类语料：用结构化字段描述图纸/影像的内容与标注（不需要真图片）。"""
    doc_id: str
    title: str
    media_type: str                 # 图纸 / 影像 / 照片（对应第13条受控词表）
    content_desc: str               # 影像/图纸里"画的是什么"
    annotation: dict[str, str]      # 人工标注：部位、现象、标注人、标注日期
    modality: str = "图文"
    vocab: list[str] = field(default_factory=list)
    terms: dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.terms = _autoterms(self, self.vocab)

    def raw_text(self) -> str:
        parts = [self.title, self.media_type, self.content_desc]
        parts += [f"{k}{v}" for k, v in self.annotation.items()]
        return " ".join(parts)

    def terms_list(self) -> list[str]:
        return list(self.terms.keys())


# ===========================================================================
#  文本类语料：4 份规范/规程片段
# ===========================================================================
TEXT_DOCS: list[TextDoc] = [
    TextDoc(
        "DOC-MFG-001", "设备台账登记规程（片段）",
        "设备资产编号应唯一，格式为 EQP- 前缀加 7 位数字。设备类别分为 A类、B类、C类；"
        "A类为关键机组，B类为主要设备，C类为辅助设备。设备状态应登记为在用、备用、"
        "检修、停用之一。设备停用时应登记停用日期，并在设备台账中保留记录，不得删除。"
        "责任部门应为设备管理部、检修车间、点检站之一。检修周期 A类 不超过 90 天，"
        "B类 不超过 180 天，C类 不超过 365 天。",
        vocab=["设备资产编号", "设备类别", "设备状态", "停用日期", "设备台账",
               "责任部门", "检修周期", "关键机组", "主要设备", "辅助设备"],
    ),
    TextDoc(
        "DOC-MFG-002", "设备点检作业规程（片段）",
        "点检站应按点检表开展日常点检，点检间隔 A类 不超过 24 小时，B类 不超过 72 小时，"
        "C类 不超过 168 小时。点检结论应填写为正常、异常、待复检之一；结论为异常时，"
        "应同时登记处置措施并转检修工单。点检记录应登记点检人、点检日期与设备资产编号。"
        "点检发现振动超限的，应引用 GB/T 6075.1-2012 判定。",
        vocab=["点检", "点检站", "点检表", "点检间隔", "点检结论", "点检人",
               "点检日期", "设备资产编号", "处置措施", "检修工单", "振动"],
    ),
    TextDoc(
        "DOC-MFG-003", "设备检修工单管理规程（片段）",
        "检修工单编号格式为 WO- 前缀加 6 位数字。工单应登记设备资产编号、检修人、"
        "开工日期与完工日期，开工日期不得晚于完工日期。检修更换件、试车结论与验收人"
        "应一并登记。工单应在完工后 5 个工作日内归档，归档时应登记归档日期。"
        "检修质量应按 ISO 14224:2016 收集可靠性数据。",
        vocab=["检修工单", "工单", "检修人", "开工日期", "完工日期", "更换件",
               "试车结论", "验收人", "归档", "归档日期", "可靠性数据"],
    ),
    TextDoc(
        "DOC-MFG-004", "数据资产密级与归档管理规程（片段）",
        "设备数据资产密级分为公开、内部、秘密三级；密级为秘密的资产应登记解密日期。"
        "数据资产的采集日期、入库日期与归档日期应依次不倒退。图纸与影像资料应登记"
        "文件哈希，哈希为 64 位小写十六进制。资料引用的标准编号应来自受控标准清单，"
        "不得引用已废止标准；设备编号不得引用已停用设备。秘密级资料不得存放于"
        "未授权的公共存储路径。",
        vocab=["密级", "解密日期", "采集日期", "入库日期", "归档日期", "文件哈希",
               "图纸", "影像资料", "受控标准清单", "已废止标准", "已停用设备",
               "公共存储路径"],
    ),
]


# ===========================================================================
#  表格类语料：3 张表（设备台账表 + 2 张点检表）
# ===========================================================================
EQUIPMENT_LEDGER_COLUMNS: dict[str, str] = {
    "equipment_no": "设备资产编号，格式 EQP- 前缀加 7 位数字",
    "equipment_name": "设备名称",
    "equipment_class": "设备类别，受控词表：A类/B类/C类",
    "equipment_status": "设备状态，受控词表：在用/备用/检修/停用",
    "owner_dept": "责任部门，受控词表见部门清单",
    "install_date": "安装日期 YYYY-MM-DD",
    "commission_date": "投运日期 YYYY-MM-DD",
    "retire_date": "停用日期 YYYY-MM-DD，仅停用设备填写",
    "maintenance_cycle_days": "检修周期（天），A类≤90 / B类≤180 / C类≤365",
}

EQUIPMENT_LEDGER_ROWS: list[dict] = [
    {"equipment_no": "EQP-1000001", "equipment_name": "1号主传动电机", "equipment_class": "A类",
     "equipment_status": "在用", "owner_dept": "设备管理部", "install_date": "2019-04-11",
     "commission_date": "2019-05-06", "retire_date": None, "maintenance_cycle_days": 90},
    {"equipment_no": "EQP-1000002", "equipment_name": "2号液压站", "equipment_class": "B类",
     "equipment_status": "在用", "owner_dept": "检修车间", "install_date": "2020-09-01",
     "commission_date": "2020-09-20", "retire_date": None, "maintenance_cycle_days": 150},
    {"equipment_no": "EQP-1000003", "equipment_name": "3号空压机", "equipment_class": "B类",
     "equipment_status": "检修", "owner_dept": "检修车间", "install_date": "2018-03-15",
     "commission_date": "2018-04-02", "retire_date": None, "maintenance_cycle_days": 180},
    {"equipment_no": "EQP-1000004", "equipment_name": "4号冷却塔风机", "equipment_class": "C类",
     "equipment_status": "备用", "owner_dept": "设备管理部", "install_date": "2021-06-08",
     "commission_date": "2021-06-30", "retire_date": None, "maintenance_cycle_days": 300},
    {"equipment_no": "EQP-1000005", "equipment_name": "5号除尘风机", "equipment_class": "C类",
     "equipment_status": "在用", "owner_dept": "安全环保部", "install_date": "2022-01-10",
     "commission_date": "2022-02-01", "retire_date": None, "maintenance_cycle_days": 365},
    # ★ 下面两台**已停用**：保留在台账中（白名单里仍在），但不得再被引用（黑名单）
    {"equipment_no": "EQP-1000007", "equipment_name": "7号老式离心泵", "equipment_class": "B类",
     "equipment_status": "停用", "owner_dept": "设备管理部", "install_date": "2012-05-20",
     "commission_date": "2012-06-15", "retire_date": "2024-11-30", "maintenance_cycle_days": 180},
    {"equipment_no": "EQP-1000011", "equipment_name": "11号淘汰型配电柜", "equipment_class": "C类",
     "equipment_status": "停用", "owner_dept": "设备管理部", "install_date": "2011-08-01",
     "commission_date": "2011-09-10", "retire_date": "2023-07-01", "maintenance_cycle_days": 365},
]

CHECK_SHEET_COLUMNS: dict[str, str] = {
    "work_order_no": "点检/工单编号，格式 WO- 前缀加 6 位数字",
    "equipment_no": "设备资产编号，应存在于设备台账",
    "check_date": "点检日期 YYYY-MM-DD",
    "check_result": "点检结论，受控词表：正常/异常/待复检",
    "check_interval_hours": "应检间隔（小时）",
    "inspector": "点检人",
    "disposal": "处置措施，结论为异常时必填",
}

DAILY_CHECK_ROWS: list[dict] = [
    {"work_order_no": "WO-200101", "equipment_no": "EQP-1000001", "check_date": "2026-03-02",
     "check_result": "正常", "check_interval_hours": 24, "inspector": "张海",
     "disposal": None},
    {"work_order_no": "WO-200102", "equipment_no": "EQP-1000002", "check_date": "2026-03-02",
     "check_result": "异常", "check_interval_hours": 72, "inspector": "李工",
     "disposal": "紧固地脚螺栓并复检油位"},
    {"work_order_no": "WO-200103", "equipment_no": "EQP-1000004", "check_date": "2026-03-03",
     "check_result": "待复检", "check_interval_hours": 168, "inspector": "王敏",
     "disposal": None},
    # ★ 这一行是"台账里有、但设备已停用"的历史遗留记录，正是第36条存在的原因
    {"work_order_no": "WO-200104", "equipment_no": "EQP-1000007", "check_date": "2024-10-08",
     "check_result": "正常", "check_interval_hours": 168, "inspector": "赵强",
     "disposal": None},
]

VIBRATION_CHECK_ROWS: list[dict] = [
    {"work_order_no": "WO-200201", "equipment_no": "EQP-1000001", "check_date": "2026-03-05",
     "check_result": "正常", "check_interval_hours": 24, "inspector": "张海",
     "disposal": None},
    {"work_order_no": "WO-200202", "equipment_no": "EQP-1000005", "check_date": "2026-03-06",
     "check_result": "异常", "check_interval_hours": 168, "inspector": "陈工",
     "disposal": "更换轴承并做动平衡"},
]

TABLE_DOCS: list[TableDoc] = [
    TableDoc("TBL-MFG-101", "设备台账表", EQUIPMENT_LEDGER_COLUMNS, EQUIPMENT_LEDGER_ROWS,
             vocab=["设备台账", "设备资产编号", "设备类别", "设备状态", "停用日期",
                    "检修周期", "责任部门", "安装日期", "投运日期", "关键机组",
                    "在用", "停用", "A类", "B类", "C类"]),
    TableDoc("TBL-MFG-102", "日常点检表", CHECK_SHEET_COLUMNS, DAILY_CHECK_ROWS,
             vocab=["点检", "点检结论", "点检人", "点检日期", "设备资产编号",
                    "处置措施", "应检间隔", "异常", "正常", "待复检"]),
    TableDoc("TBL-MFG-103", "振动专检表", CHECK_SHEET_COLUMNS, VIBRATION_CHECK_ROWS,
             vocab=["点检", "振动", "点检结论", "点检人", "设备资产编号",
                    "处置措施", "应检间隔"]),
]


# ===========================================================================
#  图文类语料：3 条图纸/影像资料
# ===========================================================================
MEDIA_DOCS: list[MediaDoc] = [
    MediaDoc(
        "IMG-MFG-201", "1号主传动电机 总装图（第 3 版）", "图纸",
        "A1 幅面总装图，含电机与减速机对中公差、地脚螺栓孔位、润滑点位置；"
        "图中标注 A类关键机组，检修周期 90 天。",
        {"标注人": "工艺技术部-刘工", "标注日期": "2026-02-18",
         "标注部位": "对中公差 0.05mm", "适用设备": "EQP-1000001"},
        vocab=["图纸", "总装图", "关键机组", "检修周期", "润滑点", "对中公差",
               "标注人", "标注日期", "标注部位"],
    ),
    MediaDoc(
        "IMG-MFG-202", "2号液压站 泄漏影像", "影像",
        "现场拍摄影像 12 秒，可见液压站阀块结合面渗油；已标注渗漏部位与拍摄角度。",
        {"标注人": "点检站-李工", "标注日期": "2026-03-02",
         "标注部位": "阀块结合面", "现象": "渗油", "适用设备": "EQP-1000002"},
        vocab=["影像", "液压站", "渗油", "标注人", "标注日期", "标注部位", "点检站"],
    ),
    MediaDoc(
        "IMG-MFG-203", "7号老式离心泵 历史照片", "照片",
        "已停用设备的历史存档照片，拍摄于 2023 年，用于设备台账留档，"
        "不作为当前在役设备的资料。",
        {"标注人": "设备管理部-赵工", "标注日期": "2023-06-11",
         "标注部位": "泵体铭牌", "适用设备": "EQP-1000007"},
        vocab=["照片", "离心泵", "已停用设备", "设备台账", "标注人", "标注日期",
               "标注部位"],
    ),
]


# ===========================================================================
#  语料整体接口
# ===========================================================================
def all_docs() -> list[Any]:
    """全部语料（三种模态混在一起，顺序稳定）。"""
    return [*TEXT_DOCS, *TABLE_DOCS, *MEDIA_DOCS]


def doc_raw_text(doc: Any) -> str:
    """取任意模态语料的"原文文本"——术语计数与覆盖率审计都走这个口。"""
    return doc.raw_text()


def corpus_term_universe() -> dict[str, int]:
    """语料侧术语总表（跨全部语料汇总频次）——覆盖率审计的分母。"""
    universe: dict[str, int] = {}
    for doc in all_docs():
        for t, n in doc.terms.items():
            universe[t] = universe.get(t, 0) + n
    return {t: n for t, n in universe.items() if n > 0}


def audit_declared_terms() -> list[dict]:
    """自检：文档里**声明**的术语频次，与从原文**机械数出来**的频次是否一致。

    ★ 这是本 Domain Pack 的"不许自欺"检查。术语表如果靠手写，很容易写成
      "希望它出现几次"而不是"它真的出现了几次"——第一版手写时 10 份语料全部对不上。
      现在频次由 `term_frequency()` 生成，这条检查退化成回归测试：它必须恒为空。
    """
    rows: list[dict] = []
    for doc in all_docs():
        text = doc_raw_text(doc)
        declared = {t: n for t, n in doc.terms.items() if n > 0}
        counted = term_frequency(text, declared.keys())
        diff = {t: (declared.get(t, 0), counted.get(t, 0))
                for t in set(declared) | set(counted)
                if declared.get(t, 0) != counted.get(t, 0)}
        rows.append({"doc_id": doc.doc_id, "title": doc.title, "模态": doc.modality,
                     "术语数": len(declared), "实际命中": len(counted), "不一致": diff})
    return rows


def term_coverage(text: str) -> dict:
    """★ 语料侧术语覆盖率审计（**外部真值，不依赖本体**）。

    给一段文本（例如一条资产序列化后的全字段），量出它覆盖了多少语料侧术语。
    这个数字完全机械可算 —— 是典型**硬锚点**：免费、外部、无争议、不需要人。
    """
    universe = corpus_term_universe()
    if not universe:
        return {"覆盖率": 1.0, "命中": 0, "术语总数": 0, "未覆盖术语": []}
    hit = [t for t in universe if t in text]
    miss = [t for t in universe if t not in text]
    return {"覆盖率": round(len(hit) / len(universe), 6),
            "命中": len(hit), "术语总数": len(universe), "未覆盖术语": miss}


# ===========================================================================
#  语料视角的"应该长什么样"——给盲区判定当**外部依据**用
# ===========================================================================
# ★ 关键：这张表**不在任何判据里**，它只是语料里白纸黑字写着的一条事实
#   （DOC-MFG-001：「检修周期 A类 不超过 90 天，B类 不超过 180 天，C类 不超过 365 天」）。
#   现有 8 类判据语言**表达不了"某字段的取值上限取决于另一个字段的取值"**：
#   RANGE 的 min/max 是常量，CONSISTENCY 的 op 只比较两个字段而不知常量表。
#   所以这类违规会被种子判据全部放行 —— 这是一个**被测出来的**真盲区，
#   也是下一步扩张判据语言（带查表的 RANGE）的入口。
CLASS_CYCLE_LIMIT: dict[str, int] = {"A类": 90, "B类": 180, "C类": 365}
CLASS_CYCLE_SOURCE = ("语料 DOC-MFG-001《设备台账登记规程（片段）》："
                      "检修周期 A类 不超过 90 天，B类 不超过 180 天，C类 不超过 365 天")

# ★ 同一张"按类别查上限"的表，第二处：点检间隔（DOC-MFG-002 里写着）。
#   把它也列出来，是为了说明"查表式约束"在真实语料里**反复出现**，不是孤例。
CLASS_INTERVAL_LIMIT: dict[str, int] = {"A类": 24, "B类": 72, "C类": 168}
CLASS_INTERVAL_SOURCE = ("语料 DOC-MFG-002《设备点检作业规程（片段）》："
                         "点检间隔 A类 不超过 24 小时，B类 不超过 72 小时，"
                         "C类 不超过 168 小时")


def corpus_says_class_cycle(equipment_class: str, cycle_days: int) -> tuple[bool, str]:
    """用**语料里写着的事实**核对检修周期与设备类别是否匹配（这是外部依据，不是判据）。"""
    limit = CLASS_CYCLE_LIMIT.get(equipment_class)
    if limit is None:
        return True, f"语料中无 {equipment_class} 的检修周期上限记载"
    if cycle_days > limit:
        return False, (f"检修周期={cycle_days} 天超过 {equipment_class} 上限 {limit} 天"
                       f"（依据：{CLASS_CYCLE_SOURCE}）")
    return True, ""


def corpus_says_class_interval(equipment_class: str, interval_hours: int) -> tuple[bool, str]:
    """同上，第二处查表式约束：点检间隔。"""
    limit = CLASS_INTERVAL_LIMIT.get(equipment_class)
    if limit is None:
        return True, f"语料中无 {equipment_class} 的点检间隔上限记载"
    if interval_hours > limit:
        return False, (f"点检间隔={interval_hours} 小时超过 {equipment_class} 上限 "
                       f"{limit} 小时（依据：{CLASS_INTERVAL_SOURCE}）")
    return True, ""
