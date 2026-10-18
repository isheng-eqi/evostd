"""制造业 Domain Pack · 可校验的资产实例（合规 8 条 + 违规 12 条）。

⚠️ 演示样例：设备编号、人名、文件哈希均为虚构。

这份数据为什么要这么设计
------------------------
它同时服务三个目的，缺一不可：

1. **给判据当靶子**：每条资产都能被 `StandardVerifier` 跑一遍，报出"违反第 N 条"。
2. **给盲区当证据**：★ 其中若干条违规**必须通过种子判据的全部检查**——它们就是
   docs/23 说的"盲区"。盲区不能靠设计者想象，必须实测（`blind_spot_report()`）。
3. **给自进化当适应度**：`_label` / `_why` 是资产侧真值，合规率是确定性适应度。

★ 本文件自带一条"不许自欺"的纪律：`[盲区]` 是**猜测**，不是事实
----------------------------------------------------------------
写这个文件的第一版时，我给 3 条违规标了 `[盲区]`（已停用设备仍被引用 /
已废止标准 / 时间序倒挂）。实测结果是：**前两条被种子判据抓个正着**，猜错了；
第三条（入库晚于归档）也被第28条抓到。真正零捕获的只有另外几条。
所以现在 `[盲区]` 只发给"实测确实零捕获"的样本；猜错的样本原样留在数据里，
标记改成 `[可见]` / `[未捕获]`，并在 `_why` 里写明"曾误判为盲区"。
这正是 docs/23 发现 2 的复现：**盲区必须实测，不能凭设计者想象。**

★ 4 类实测真盲区（全部零捕获）
-----------------------------
B1 未来日期（2030 年）        —— `check_date` 格式合法、枚举合法；"不得晚于今天"
                                需要判据知道当前时间，而判据被刻意做成不依赖时钟的
                                纯函数（compiler.py 设计要点 3）。
B2 检修周期与设备类别不匹配     —— 上限**取决于另一个字段的取值**（查表式约束）。
                                RANGE 的 min/max 是编译期常量，op 只比较两个字段。
B3 同一文件哈希登记在两个资产号 —— **跨资产**约束：任何单条判据都只看一个 asset，
                                `verify(asset)` 的签名里根本没有"其他资产"。
B4 点检间隔与设备类别不匹配     —— 与 B2 同因不同表（第二处查表式约束），
                                证明这类约束在语料里反复出现，不是孤例。
"""
from __future__ import annotations

from typing import Any

from .corpus import (
    CLASS_CYCLE_LIMIT, CLASS_CYCLE_SOURCE,
    CLASS_INTERVAL_LIMIT, CLASS_INTERVAL_SOURCE,
    EQUIPMENT_REGISTRY, RETIRED_EQUIPMENT, CONTROLLED_STANDARDS, build_ctx,
)

ASSETS_NOTICE = "★ 演示资产：全部为虚构数据，用于校验演示。"

# 一条已知合规的资产，给"跨资产盲区"当对照样本用
_KNOWN_GOOD_ID = "AST-MFG-2026-0001"


# ---------------------------------------------------------------------------
# 公共字段底盘：所有资产共享的一组合规取值
# 每条资产再加自己的差异字段——差量写法让"违规点"一眼可见。
# ---------------------------------------------------------------------------
_BASE: dict[str, Any] = {
    "secrecy": "内部",
    "collect_time": "2026-03-01",
    "ingest_time": "2026-03-05",
    "archive_time": "2026-03-10",
    "install_date": "2019-04-11",
    "commission_date": "2019-05-06",
    "retire_date": None,              # 在用设备必须为空（第30条）
    "maintenance_cycle_days": 90,
    "quality_score": 88.0,
    "file_hash": "a1b2c3d4e5f60718293a4b5c6d7e8f901a2b3c4d5e6f708192a3b4c5d6e7f801",
    "storage_uri": "oss://mfg-assets/2026/equipment/0001.json",
    "cited_standards": ["GB/T 33863.1-2017"],
    "source_doc_ids": ["DOC-MFG-001"],
    "owner_dept": "设备管理部",
    "maintainer": "王工",
    "equipment_no": "EQP-1000001",
    "equipment_class": "A类",
    "equipment_status": "在用",
    "media_type": "图纸",
    "revision": 1,
    # ★ 下面两个字段**刻意不受任何判据约束**：判据只检查它规定的东西。
    #   数据里有语义、判据里没条款 —— 这就是 B1/B2 两类盲区的成因。
    "check_date": "2026-03-02",
    "check_interval_hours": 24,
    # 字段标签：语料侧术语 → 本资产里的实际取值。它让「语料侧术语覆盖率审计」
    # 有东西可测（否则英文列名一个中文术语都覆盖不到，覆盖率恒为 0）。
    "field_labels": {
        "设备资产编号": "EQP-1000001",
        "设备类别": "A类",
        "设备状态": "在用",
        "责任部门": "设备管理部",
        "检修周期": "90 天",
        "安装日期": "2019-04-11",
        "投运日期": "2019-05-06",
        "点检日期": "2026-03-02",
    },
}


def _asset(**over: Any) -> dict:
    """以 _BASE 为底盘造一条资产（差量写法）。"""
    return {**_BASE, **over}


def _asset_missing(drop: list, **over: Any) -> dict:
    """造「**缺字段**」的资产：以 _BASE 为底盘后**删掉**指定键。

    为什么需要：必填类判据（REQUIRED）要验证的是"字段缺失时会不会报"。
    而 `_asset()` 是 `{**_BASE, **over}` —— **只能设值，删不掉键**。
    实测：制造业 7 条未触发判据里有 4 条是必填约束，全卡在这里。
    （政务包用的是同构的 `put_missing`，见其 assets.py。）
    """
    d = {**_BASE, **over}
    for _k in drop:
        d.pop(_k, None)
    return d


def _labels(**over: str) -> dict[str, str]:
    """只覆盖 field_labels 的快捷写法（字段标签跟着差异字段走）。"""
    return {**_BASE["field_labels"], **over}


# ===========================================================================
#  合规资产 8 条 —— 必须**全部通过**所有判据（否则合规率汇总会自相矛盾）
# ===========================================================================
COMPLIANT_ASSETS: list[tuple[str, dict]] = [
    ("设备台账登记（文本类，引用受控标准）", _asset(
        asset_id=_KNOWN_GOOD_ID, equipment_no="EQP-1000001",
        cited_standards=["GB/T 33863.1-2017", "ISO 14224:2016"],
        source_doc_ids=["DOC-MFG-001", "TBL-MFG-101"],
        file_hash="1" * 64)),

    ("点检记录（表格类，结论异常并已登记处置措施）", _asset(
        asset_id="AST-MFG-2026-0002", equipment_no="EQP-1000002",
        equipment_class="B类", work_order_no="WO-200102",
        check_result="异常", disposal="紧固地脚螺栓并复检油位",
        check_interval_hours=72, source_doc_ids=["TBL-MFG-102", "DOC-MFG-002"],
        file_hash="2" * 64,
        field_labels=_labels(设备资产编号="EQP-1000002", 设备类别="B类",
                             点检结论="异常", 处置措施="紧固地脚螺栓并复检油位"))),

    ("图纸资料（图文类，修订版次与哈希合规）", _asset(
        asset_id="AST-MFG-2026-0003", equipment_no="EQP-1000004",
        equipment_class="C类", media_type="图纸", revision=3,
        source_doc_ids=["IMG-MFG-201"], file_hash="3" * 64,
        field_labels=_labels(设备资产编号="EQP-1000004", 设备类别="C类",
                             图纸="IMG-MFG-201 总装图", 文件哈希="3" * 16))),

    ("秘密级资产（已登记解密日期）", _asset(
        asset_id="AST-MFG-2026-0004", equipment_no="EQP-1000005",
        equipment_class="C类", secrecy="秘密", declassify_date="2029-03-10",
        owner_dept="设备管理部", file_hash="4" * 64,
        field_labels=_labels(设备资产编号="EQP-1000005", 设备类别="C类",
                             密级="秘密", 解密日期="2029-03-10"))),

    ("检修工单（开工/完工时间序正确）", _asset(
        asset_id="AST-MFG-2026-0006", equipment_no="EQP-1000003",
        equipment_class="B类", work_order_no="WO-200301",
        work_start="2026-03-04", work_end="2026-03-06",
        maintainer="李工", owner_dept="检修车间",
        source_doc_ids=["DOC-MFG-003"], file_hash="6" * 64,
        field_labels=_labels(设备资产编号="EQP-1000003", 设备类别="B类",
                             责任部门="检修车间", 检修人="李工",
                             开工日期="2026-03-04", 完工日期="2026-03-06"))),

    ("振动专检记录（异常已处置，引用振动标准）", _asset(
        asset_id="AST-MFG-2026-0007", equipment_no="EQP-1000005",
        equipment_class="C类", work_order_no="WO-200202",
        check_result="异常", disposal="更换轴承并做动平衡",
        check_interval_hours=168,
        cited_standards=["GB/T 6075.1-2012", "ISO 10816-3:2009"],
        source_doc_ids=["TBL-MFG-103", "DOC-MFG-002"], file_hash="7" * 64,
        field_labels=_labels(设备资产编号="EQP-1000005", 设备类别="C类",
                             点检结论="异常", 处置措施="更换轴承并做动平衡",
                             振动="GB/T 6075.1-2012"))),

    ("影像资料（含标注说明，密级内部）", _asset(
        asset_id="AST-MFG-2026-0008", equipment_no="EQP-1000002",
        equipment_class="B类", media_type="影像",
        annotation_note="阀块结合面渗油，拍摄角度已标注",
        source_doc_ids=["IMG-MFG-202", "DOC-MFG-004"], file_hash="8" * 64,
        field_labels=_labels(设备资产编号="EQP-1000002", 设备类别="B类",
                             影像资料="IMG-MFG-202 泄漏影像",
                             标注人="点检站-李工", 标注部位="阀块结合面"))),

    ("设备检修周期与类别匹配（C类 300 天，与语料一致）", _asset(
        asset_id="AST-MFG-2026-0009", equipment_no="EQP-1000005",
        equipment_class="C类", maintenance_cycle_days=300,
        source_doc_ids=["TBL-MFG-101", "DOC-MFG-001"], file_hash="9" * 64,
        field_labels=_labels(设备资产编号="EQP-1000005", 设备类别="C类",
                             检修周期="300 天（C类上限 365 天）"))),
]


# ===========================================================================
#  违规资产 12 条 —— 覆盖 8 类判据；其中 4 条是**实测**真盲区
# ===========================================================================
VIOLATING_ASSETS: list[tuple[str, dict]] = [
    # ================= B1 未来日期（★ 实测真盲区：零捕获） =================
    ("[盲区] 点检日期落在 2030 年（未来日期，格式与枚举全部合法）", _asset(
        asset_id="AST-MFG-2026-1015", equipment_no="EQP-1000005",
        equipment_class="C类", check_date="2030-12-31",
        source_doc_ids=["TBL-MFG-102"], file_hash="a" * 64,
        # 实测：格式合规、枚举合规、时间序判据不涉及 check_date → 捕获 0 条。
        field_labels=_labels(设备资产编号="EQP-1000005", 设备类别="C类",
                             点检日期="2030-12-31（未来日期）"))),

    # ================= B2 检修周期与设备类别不匹配（★ 实测真盲区） =================
    ("[盲区] A类设备检修周期 365 天，超过 A类上限 90 天（查表式约束测不到）", _asset(
        asset_id="AST-MFG-2026-1002", equipment_no="EQP-1000003",
        equipment_class="A类", maintenance_cycle_days=365,
        source_doc_ids=["DOC-MFG-001"], file_hash="b" * 64,
        # 实测：第5条 RANGE 只查 1~365 的常量区间，365 恰好合法 → 捕获 0 条。
        field_labels=_labels(设备资产编号="EQP-1000003", 设备类别="A类",
                             检修周期="365 天（A类上限 90 天）"))),

    # ================= B3 同一文件哈希登记在两个资产号（★ 实测真盲区） =================
    ("[盲区] 与 AST-MFG-2026-0001 登记了同一份文件哈希（跨资产重复）", _asset(
        asset_id="AST-MFG-2026-1016", equipment_no="EQP-1000004",
        equipment_class="C类", media_type="影像", revision=2,
        annotation_note="与主传动电机登记件重复",
        cited_standards=["GB/T 33863.1-2017", "ISO 14224:2016"],
        source_doc_ids=["DOC-MFG-001", "TBL-MFG-101"],
        file_hash="1" * 64,      # ★ 与 0001 完全相同（哈希本身格式合法）
        # 实测：**零捕获**。第17条只查"这串哈希像不像 SHA-256"，不查"它是不是别人的哈希"。
        #   判据签名 `check(asset, ctx)` 里根本没有"其他资产" → 单条判据结构上做不到。
        #   （这是 B3 的干净样本：不加任何附带违规，盲区判定才不会被别的条款搅浑。）
        field_labels=_labels(设备资产编号="EQP-1000004", 设备类别="C类",
                             文件哈希="1" * 16 + "…（与 0001 重复）"))),

    # ================= B4 点检间隔与设备类别不匹配（★ 实测真盲区） =================
    ("[盲区] A类设备点检间隔 168 小时，超过 A类上限 24 小时（第二处查表式约束）", _asset(
        asset_id="AST-MFG-2026-1017", equipment_no="EQP-1000004",
        equipment_class="A类", check_interval_hours=168,
        cited_standards=["GB/T 33863.1-2017", "ISO 14224:2016"],
        source_doc_ids=["DOC-MFG-001", "TBL-MFG-101"],
        check_result="正常",
        # 实测：**零捕获**。第7条只查 1~720 的常量区间，168 恰好合法；
        #   而"上限取决于设备类别"（A类≤24 / B类≤72 / C类≤168）表达不了。
        #   ★ 与 B2 是**同一张表的第二处**：说明查表式约束在真实语料里反复出现，
        #     不是孤例——这正是判据语言该扩张的信号（docs/21 元机制）。
        field_labels=_labels(设备资产编号="EQP-1000004", 设备类别="A类",
                             点检间隔="168 小时（A类上限 24 小时）"))),

    # ============ [可见] 已停用设备被业务单据引用（第36条正确拦截） ============
    ("[可见] 点检工单引用了已停用设备 EQP-1000007（第36条正确拦截）", _asset(
        asset_id="AST-MFG-2026-1001", equipment_no="EQP-1000007",
        equipment_class="B类", work_order_no="WO-200104",
        check_result="正常", file_hash="d" * 64,
        # ★ 这条曾被我标成 [盲区]，**实测被第36条抓住** → 猜测错了。
        #   已停用设备仍在台账白名单里，但第36条的黑名单把它单独禁掉了。
        field_labels=_labels(设备资产编号="EQP-1000007", 设备类别="B类",
                             设备状态="停用", 点检结论="正常"))),

    # ============ [未捕获] 引用已废止标准（白名单+黑名单双杀） ============
    ("[未捕获] 引用已废止标准 GB/T 19001-2008（曾以为只有黑名单能表达）", _asset(
        asset_id="AST-MFG-2026-1008", equipment_no="EQP-1000001",
        cited_standards=["GB/T 19001-2008"], file_hash="e" * 64,
        # ★ 同样曾标 [盲区]，实测被第37条（黑名单）+ 第33条（白名单）双双抓住。
        #   双杀是有意的：废止信息是**独立清单**，可以单独替换与审计。
        field_labels=_labels(设备资产编号="EQP-1000001",
                             受控标准清单="GB/T 19001-2008（已废止）"))),

    # ---------------- [可见] 引用清单外数据 + 必填为空 + 安装晚于投运 ----------------
    ("[可见] 引用未纳入受控清单的设备编号 + 责任部门为空白 + 安装晚于投运", _asset(
        asset_id="AST-MFG-2026-1004", equipment_no="EQP-9999999",
        owner_dept="   ", install_date="2026-01-10", commission_date="2022-02-01",
        file_hash="f" * 64,
        field_labels=_labels(设备资产编号="EQP-9999999", 责任部门="（空白）",
                             安装日期="2026-01-10", 投运日期="2022-02-01"),
        # 预期：第32条（白名单）+ 第19条（必填，空白串按空处理）+ 第35条（部门）
        #       + 第31条（安装/投运时间序）
        )),

    # ---------------- [可见] 受控词表越界 ----------------
    ("[可见] 设备状态「报废」不在受控词表", _asset(
        asset_id="AST-MFG-2026-1005", equipment_no="EQP-1000005",
        equipment_status="报废", retire_date=None, file_hash="0" * 63 + "1",
        field_labels=_labels(设备资产编号="EQP-1000005", 设备状态="报废"),
        # 预期：第10条（ENUM）
        )),

    # ---------------- [可见] 资料类型越界 + 秘密级缺解密日期 ----------------
    ("[可见] 资料类型「扫描件」越界 + 秘密级缺解密日期", _asset(
        asset_id="AST-MFG-2026-1018", equipment_no="EQP-1000001",
        media_type="扫描件", secrecy="秘密", declassify_date=None,
        source_doc_ids=["IMG-MFG-202", "DOC-MFG-004"], file_hash="0" * 62 + "34",
        field_labels=_labels(设备资产编号="EQP-1000001", 密级="秘密",
                             影像资料="扫描件（不在受控词表内）"),
        # 预期：第13条（ENUM）+ 第24条（CONDITIONAL）+ 第25条（CONDITIONAL）
        # 这条补齐了"同一字段同时踩 ENUM 与 CONDITIONAL"的组合覆盖。
        )),

    # ---------------- [可见] 数值越界 + 格式不符 ----------------
    ("[可见] 检修周期 900 天越界 + 质量评分越界 + 影像哈希格式错误", _asset(
        asset_id="AST-MFG-2026-1006", equipment_no="EQP-1000002",
        maintenance_cycle_days=900, file_hash="NOT-A-SHA256", quality_score=120,
        field_labels=_labels(设备资产编号="EQP-1000002",
                             检修周期="900 天", 文件哈希="NOT-A-SHA256"),
        # 预期：第5条（RANGE）+ 第6条（RANGE）+ 第17条（PATTERN）
        )),

    # ---------------- [可见] 资产标识格式 + 来源资料不在册 + 运维字段越界 ----------------
    ("[可见] 资产标识格式错误 + 来源资料不在册 + 点检间隔越界 + 检修周期越界", _asset(
        asset_id="MFG-2026-11", equipment_no="EQP-1000004",
        maintenance_cycle_days=400, check_interval_hours=1000,
        source_doc_ids=["DOC-MFG-999"], file_hash="0" * 61 + "12345",
        field_labels=_labels(设备资产编号="EQP-1000004",
                             检修周期="400 天（越界）", 来源资料="DOC-MFG-999"),
        # 预期：第14条（PATTERN）+ 第34条（REFERENCE）+ 第7条（RANGE）+ 第5条（RANGE）
        )),

    # ---------------- [可见] 时间序倒挂 + 停用缺登记 + 影像缺标注 + 秘密级缺解密 ----------------
    ("[可见] 采集/入库/归档三处时间序倒挂 + 开工晚于完工 + 停用未登记 + 影像缺标注", _asset(
        asset_id="AST-MFG-2026-1019", equipment_no="EQP-1000003",
        collect_time="2026-03-20", ingest_time="2026-03-05",
        archive_time="2026-03-01", equipment_status="停用",
        work_order_no="WO-200777", work_start="2026-03-08", work_end="2026-03-06",
        media_type="影像", annotation_note=None,
        source_doc_ids=["DOC-MFG-003", "IMG-MFG-202"], file_hash="0" * 62 + "34",
        field_labels=_labels(设备资产编号="EQP-1000003", 设备状态="停用",
                             采集日期="2026-03-20", 入库日期="2026-03-05",
                             归档日期="2026-03-01",
                             开工日期="2026-03-08", 完工日期="2026-03-06"),
        # 预期：第27/28/29条 + 第23条 + 第25条（5 条判据同时命中）
        # ★ 这一条是"与盲区形成对照"的证据：这里三个时间点**每两两都被条款点名**，
        #   所以全序被拆成两条二元判据就抓到了；而 check_date 没被任何条款点名，
        #   同样的时间序问题就全军放行（见 B1 样本 AST-MFG-2026-1015）。
        )),
    # ── 定向补样（判据活性驱动，见 docs/42 第 124~129 节）──
    ("[补样] 修订号超出范围", _asset(
        asset_id="AST-MFG-2026-1030", equipment_no="EQP-1000010",
        revision=99999, field_labels=_labels(修订号="99999"))),
    ("[补样] 设备类别不在受控词表", _asset(
        asset_id="AST-MFG-2026-1031", equipment_no="EQP-1000011",
        equipment_class="Z类", field_labels=_labels(设备类别="Z类"))),
    ("[补样] 点检结果不在受控词表", _asset(
        asset_id="AST-MFG-2026-1032", equipment_no="EQP-1000012",
        check_result="待定", field_labels=_labels(点检结果="待定"))),
    ("[补样] 密级不在受控词表", _asset(
        asset_id="AST-MFG-2026-1033", equipment_no="EQP-1000013",
        secrecy="绝密", field_labels=_labels(密级="绝密"))),
    ("[补样] 设备编号格式不合规", _asset(
        asset_id="AST-MFG-2026-1034", equipment_no="BADNO",
        field_labels=_labels(设备资产编号="BADNO"))),
    ("[补样] 工单号格式不合规", _asset(
        asset_id="AST-MFG-2026-1035", equipment_no="EQP-1000015",
        work_order_no="xxx", field_labels=_labels(工单号="xxx"))),
    # ── 组合条件 / 缺字段补样 ──
    ("[补样] 缺设备资产编号（必填）", _asset_missing(
        drop=["equipment_no"], asset_id="AST-MFG-2026-1040")),
    ("[补样] 缺设备类别（必填）", _asset_missing(
        drop=["equipment_class"], asset_id="AST-MFG-2026-1041")),
    ("[补样] 缺采集时间（必填）", _asset_missing(
        drop=["collect_time"], asset_id="AST-MFG-2026-1042")),
    ("[补样] 缺维护人（必填）", _asset_missing(
        drop=["maintainer"], asset_id="AST-MFG-2026-1043")),
    ("[补样] 点检结果为异常却缺处理说明（条件必填）", _asset_missing(
        drop=["check_note"], asset_id="AST-MFG-2026-1044", check_result="异常")),
    ("[补样] 设备在用却填了停用日期（条件禁止）", _asset(
        asset_id="AST-MFG-2026-1045", equipment_status="在用",
        retire_date="2026-06-01")),
    ("[补样] 存储地址指向未授权位置（禁止取值）", _asset(
        asset_id="AST-MFG-2026-1046", storage_uri="C:/Users/public/Desktop/设备资料")),
]


# ===========================================================================
#  统一入口：每条资产都带 _label / _why
# ===========================================================================
def all_assets() -> list[dict]:
    """返回全部资产（每条都带 `_label` 与 `_why`），顺序稳定。"""
    out: list[dict] = []
    for why, a in COMPLIANT_ASSETS:
        out.append({**a, "_label": "compliant", "_why": why})
    for why, a in VIOLATING_ASSETS:
        out.append({**a, "_label": "violating", "_why": why})
    return out


def ctx() -> dict:
    """判据运行 ctx（转发 corpus.build_ctx，保证只有一处真值来源）。"""
    return build_ctx()


def asset_text(asset: dict) -> str:
    """把资产序列化成一段文本，用于语料侧术语覆盖率审计。

    包含英文列名的取值 + `field_labels` 里的领域术语取值。
    """
    parts = [f"{k}={v}" for k, v in asset.items()
             if not k.startswith("_") and k != "field_labels"]
    labels = asset.get("field_labels") or {}
    parts += [f"{k}：{v}" for k, v in labels.items()]
    return " ".join(parts)


def _label_of(why: str) -> str:
    """从 `_why` 前缀取样本的**设计者声称**（不是事实，是待实测的猜测）。"""
    for tag in ("[盲区]", "[未捕获]", "[可见]"):
        if why.startswith(tag):
            return tag.strip("[]")
    return "未标注"


# ===========================================================================
#  盲区实测（★ 不许想当然：盲区必须用判据跑出来，不能靠设计者想象）
# ===========================================================================
def blind_spot_report(checkers: list) -> list[dict]:
    """逐条违规资产测一遍：种子判据**全部放行**的，才是真盲区。

    参数 checkers 来自 `compile_standard(CLAUSES).checkers`——
    这个函数回答的是："标准编译出来的判据库，看得见这条违规吗？"
    """
    ctx_ = ctx()
    rows: list[dict] = []
    for why, a in VIOLATING_ASSETS:
        asset = {**a, "_label": "violating", "_why": why}
        caught = [c for c in checkers if not c.check(asset, ctx_).ok]
        rows.append({
            "资产": asset["asset_id"], "说明": why, "声称": _label_of(why),
            "被捕获条款数": len(caught),
            "捕获条款": [c.clause.clause_id for c in caught],
            "真盲区": len(caught) == 0,
        })
    return rows


def cross_asset_blind_spots(verifier) -> list[dict]:
    """★ 跨资产盲区：**任何单条判据都不可能测到**的那一类。

    判据的签名是 `check(asset, ctx)` —— 它**看不到别的资产**。
    所以"两条资产登记了同一份文件哈希"这类只有在**资产集合**层面才成立的违规，
    靠单点判据永远测不到。这直接对应 docs/30 的 L3：**判据的对象要从"点"升级到"图"**。

    这里用**合成对照样本**（取一条已实测合规的资产，只改一个字段）来证明，
    而不是再往违规清单里塞条目 —— 保证清单刚好是"合规 8 + 违规 12"。
    """
    ctx_ = ctx()
    good = {**dict(COMPLIANT_ASSETS[0][1]), "_label": "compliant"}

    p1 = {**good, "asset_id": "AST-MFG-2026-1091", "equipment_no": "EQP-1000004",
          "_why": "合成：与 AST-MFG-2026-0001 登记同一份文件哈希"}
    probes = [{
        "编号": "B3", "盲区": "同一文件哈希登记在两个资产号", "样本": p1,
        "语料依据": f"DOC-MFG-004：图纸与影像资料应登记文件哈希；"
                    f"而 {COMPLIANT_ASSETS[0][1]['asset_id']} 已登记同一哈希 "
                    f"{COMPLIANT_ASSETS[0][1]['file_hash'][:12]}…（可机械核对）",
        "判定": "违规（集合层面）"}]

    for pr in probes:
        caught = [c.clause.clause_id for c in verifier.compiled.checkers
                  if not c.check(pr["样本"], ctx_).ok]
        pr["捕获条款"] = caught
        pr["真盲区"] = len(caught) == 0
    return probes


def false_positive_report(verifier) -> list[dict]:
    """★ 反向检查：**合规**资产里有没有被机械判据误报的？

    误报和盲区是同一枚硬币的两面：判据太松就漏（盲区），太紧就冤（误报）。
    这里实测出来的每一条误报，都是"机械判据缺语境"的证据。
    """
    ctx_ = ctx()
    rows: list[dict] = []
    for why, a in COMPLIANT_ASSETS:
        asset = {**a, "_label": "compliant", "_why": why}
        vs = verifier.violations(asset, ctx_)
        if vs:
            rows.append({"资产": asset["asset_id"], "用例": why,
                         "误报条款": [f.clause_id for f in vs],
                         "为什么是冤的": "台账类的合法记录被按业务单据的规则判了"})
    return rows


def blind_spot_kinds() -> list[dict]:
    """★ 4 类真实盲区的**结构性说明**（= 需要人一次性确认的『软确认』清单）。

    这是本 Domain Pack 里软确认最贵的一小块：每一类都要人一次性确认
    "这确实算违规"，系统才能拿它当锚点去扩张判据语言。
    人的工作量与**盲区数量**成正比，而不是与标准规模成正比（docs/23 结论）。
    """
    return [
        {"编号": "B1", "盲区": "未来日期（点检日期落在 2030 年）",
         "为什么测不到": "PATTERN 只查格式、ENUM 只查词表，两者都放行 2030-12-31。"
                         "『日期不得晚于今天』需要判据知道当前时间 —— 而判据被刻意做成"
                         "**不依赖时钟**的纯函数（compiler.py 设计要点 3）。"
                         "实测样本 AST-MFG-2026-1015（零捕获）。",
         "扩张方向": "引入**显式注入**的基准时间（基准来自 ctx 而非时钟，确定性不破）"},
        {"编号": "B2", "盲区": "检修周期与设备类别不匹配（查表式约束 · 第一处）",
         "为什么测不到": "上限**取决于另一个字段的取值**。RANGE 的 min/max 是编译期常量，"
                         "CONSISTENCY 的 op 只会比较两个字段，都表达不了『按类别查上限』。"
                         "实测样本 AST-MFG-2026-1002（零捕获）。",
         "扩张方向": "带查表的 RANGE（min/max 引用另一字段或一张受控表）——"
                     "这是 docs/21 元机制说的『判据语言扩张』的下一个入口"},
        {"编号": "B3", "盲区": "同一文件哈希登记在两个资产号（跨资产）",
         "为什么测不到": "判据签名是 `check(asset, ctx)` —— **它看不到别的资产**。"
                         "『同一份文件被登记两次』是集合级事实，单点判据结构上做不到。"
                         "合成对照样本 AST-MFG-2026-1091（零捕获）。",
         "扩张方向": "判据对象从『一条资产』升级到『资产集合/图』（docs/30 的 L3：点 → 图）"},
        {"编号": "B4", "盲区": "点检间隔与设备类别不匹配（查表式约束 · 第二处）",
         "为什么测不到": "与 B2 同因不同表：第7条只查 1~720 的常量区间，"
                         "而语料 DOC-MFG-002 写着 A类≤24 / B类≤72 / C类≤168 小时。"
                         "实测样本 AST-MFG-2026-1017（零捕获）。"
                         "★ 同一张表在语料里**出现两次**，说明这不是孤例，是真信号。",
         "扩张方向": "同 B2；两处一起做，才能证明扩张的是**判据语言**而不是打补丁"},
    ]


def caught_looking_blind() -> list[dict]:
    """★ 设计者以为的盲区里，**实测已被捕获**的那些（docs/23 发现 2 的复现）。

    这份清单是本包最有价值的"反面教材"：它证明"我以为的盲区"和"真实的盲区"
    是两件事。没有这条检查，设计者会把人力和确认成本花在不存在的盲区上。
    """
    return [
        {"样本": "AST-MFG-2026-1001", "曾经的猜测": "已停用设备仍被引用 → 以为白名单测不到",
         "实测": "被第36条（FORBIDDEN 黑名单）捕获",
         "结论": "猜测错在**时机**：在只有白名单的一代确实测不到，但第36条已经把黑名单补上了。"
                 "猜错的代价是——如果照这个猜测去扩张判据语言，会造出一条多余的判据。"},
        {"样本": "AST-MFG-2026-1008", "曾经的猜测": "已废止标准 → 以为只有黑名单能表达",
         "实测": "被第37条（黑名单）+ 第33条（白名单）双双捕获",
         "结论": "猜测错在**必要性**：废止标准根本不在受控清单里，白名单早就拦住了。"
                 "黑名单的真正价值是让废止信息成为可独立维护的清单，"
                 "而不是『唯一能表达它的手段』。"},
        {"样本": "AST-MFG-2026-1003（第一版里的『入库晚于归档』样本）",
         "曾经的猜测": "二元一致性判据表达不了三字段时间序 → 以为整类是盲区",
         "实测": "只要条款**点名**了那一对字段（第28条），就能被抓到",
         "结论": "猜测错在**粒度**：测不到的不是『时间序』这整类，而是"
                 "『没被任何条款点名的字段』（那才是 B1/B2 的成因）。"},
    ]


def class_cycle_synthetic() -> dict:
    """★ 盲区 B2 的**合成对照样本**：证明"查表式约束"不是边角料。

    取一条**合规**资产，只把设备类别改成 A类、检修周期改成 365 天（A类越界值），
    然后看种子判据的反应：全部放行。语料里白纸黑字写着 A类上限 90 天，
    但没有任何一条判据能引用这张表。
    """
    base = dict(COMPLIANT_ASSETS[0][1])
    base["asset_id"] = "AST-MFG-2026-1090"
    base["equipment_class"] = "A类"
    base["maintenance_cycle_days"] = 365
    ok, why = True, ""
    limit = CLASS_CYCLE_LIMIT.get(base["equipment_class"])
    if limit is not None and base["maintenance_cycle_days"] > limit:
        ok, why = False, (f"{base['equipment_class']} 的检修周期上限为 {limit} 天，"
                          f"实际登记 {base['maintenance_cycle_days']} 天")
    return {"资产": base, "语料依据": CLASS_CYCLE_SOURCE,
            "语料判定": "违规" if not ok else "合规", "语料理由": why}


def class_interval_synthetic() -> dict:
    """★ 盲区 B4 的**合成对照样本**（同一张表的第二处，见 corpus.DOC-MFG-002）。

    只把设备类别改成 A类、点检间隔改成 168 小时 —— 于是 A类设备按 C类节奏点检。
    第7条 RANGE 只认 1~720 的常量区间，168 合法 → 零捕获。
    """
    base = dict(COMPLIANT_ASSETS[0][1])
    base["asset_id"] = "AST-MFG-2026-1092"
    base["equipment_class"] = "A类"
    base["check_interval_hours"] = 168
    ok, why = True, ""
    limit = CLASS_INTERVAL_LIMIT.get(base["equipment_class"])
    if limit is not None and base["check_interval_hours"] > limit:
        ok, why = False, (f"{base['equipment_class']} 的点检间隔上限为 {limit} 小时，"
                          f"实际登记 {base['check_interval_hours']} 小时")
    return {"资产": base, "语料依据": CLASS_INTERVAL_SOURCE,
            "语料判定": "违规" if not ok else "合规", "语料理由": why}


# ---------------------------------------------------------------------------
# 再导出，方便外部只 import 本模块
# ---------------------------------------------------------------------------
__all__ = [
    "ASSETS_NOTICE", "COMPLIANT_ASSETS", "VIOLATING_ASSETS",
    "EQUIPMENT_REGISTRY", "RETIRED_EQUIPMENT", "CONTROLLED_STANDARDS",
    "all_assets", "ctx", "asset_text", "blind_spot_report", "cross_asset_blind_spots",
    "false_positive_report", "blind_spot_kinds", "caught_looking_blind",
    "class_cycle_synthetic", "class_interval_synthetic",
]
