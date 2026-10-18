"""并行标准体系（演示样例）：与 `standard_industrial.py` 并列的第二部标准。

★ 本文为演示编写的样例标准，非真实标准，不得作为规范引用。

存在的目的
----------
赛题要求②含"**标准体系对齐**"——单一标准无法证明这件事。
本文件提供**第二部标准**，与主标准在**同一批字段**上给出约束，用于演示：

    · 两部标准都覆盖、且约束**完全一致** → 可互认
    · 一部比另一部**更严**                → 更严者可替代更宽者（单向包含）
    · 一部比另一部**更宽**                → 同上，方向相反
    · 两部**取值域不相交**                → ★ 真冲突，必须由标准化组织裁决
    · 只有一部覆盖                        → 各自独占，不构成对齐问题

**这些关系全部由 `tools/cross_standard.py` 机械算出，不靠人工比对。**

★ 重要：本标准**不会被装进运行中的判据库**。
  主库的条款数 / 锚点数保持不变（38 / 103），
  本文件只在做"跨标准对齐"时被单独编译。
"""
from __future__ import annotations

from standard.compiler import Clause, ClauseKind

STANDARD_NAME = "设备数据资产管理规范（演示样例·并行体系）"
STANDARD_NO = "Q/MFG-DEMO-2026-B"          # 虚构编号
STANDARD_NOTICE = "★ 本文为演示编写的样例标准，非真实标准，不得作为规范引用。"


CLAUSES: list[Clause] = [
    # ============ 与主标准重叠、且**更严** ============
    Clause("第1条", "检修周期上限（并行体系）", ClauseKind.RANGE,
           "设备数据资产登记的检修周期应为 1 至 180 个自然日之间的整数。",
           {"field": "maintenance_cycle_days", "min": 1, "max": 180},
           standard=STANDARD_NAME),

    Clause("第2条", "数据质量评分下界（并行体系）", ClauseKind.RANGE,
           "设备数据资产的数据质量评分应为 60 至 100 之间的数值。",
           {"field": "quality_score", "min": 60, "max": 100},
           standard=STANDARD_NAME),

    Clause("第3条", "图纸修订版次（并行体系）", ClauseKind.RANGE,
           "图纸资料的修订版次应为 0 至 49 之间的整数。",
           {"field": "revision", "min": 0, "max": 49},
           standard=STANDARD_NAME),

    # ============ 与主标准重叠、且**完全一致** ============
    Clause("第4条", "点检间隔区间（并行体系）", ClauseKind.RANGE,
           "点检记录的应检间隔应为 1 至 720 小时之间的整数。",
           {"field": "check_interval_hours", "min": 1, "max": 720},
           standard=STANDARD_NAME),

    Clause("第5条", "设备状态受控（并行体系）", ClauseKind.ENUM,
           "设备状态应属于受控词表：在用、备用、检修、停用。",
           {"field": "equipment_status",
            "allowed": ["在用", "备用", "检修", "停用"]},
           standard=STANDARD_NAME),

    Clause("第6条", "密级受控（并行体系）", ClauseKind.ENUM,
           "数据资产密级应属于受控词表：公开、内部、秘密。",
           {"field": "secrecy", "allowed": ["公开", "内部", "秘密"]},
           standard=STANDARD_NAME),

    # ============ 与主标准重叠、但**更宽** ============
    Clause("第7条", "设备类别受控（并行体系，含 D类）", ClauseKind.ENUM,
           "设备类别应属于受控词表：A类、B类、C类、D类。",
           {"field": "equipment_class",
            "allowed": ["A类", "B类", "C类", "D类"]},
           standard=STANDARD_NAME),

    # ============ ★ 与主标准**真冲突**：取值域不相交 ============
    Clause("第8条", "点检结论受控（并行体系，二值口径）", ClauseKind.ENUM,
           "点检结论应属于受控词表：合格、不合格。",
           {"field": "check_result", "allowed": ["合格", "不合格"]},
           standard=STANDARD_NAME),

    # ============ 只有本体系覆盖（主标准没有） ============
    Clause("第9条", "告警阈值必填（并行体系独占）", ClauseKind.REQUIRED,
           "设备数据资产必须登记告警阈值。",
           {"field": "alarm_threshold"},
           standard=STANDARD_NAME),

    Clause("第10条", "告警阈值区间（并行体系独占）", ClauseKind.RANGE,
           "告警阈值应为 0 至 10000 之间的数值。",
           {"field": "alarm_threshold", "min": 0, "max": 10000},
           standard=STANDARD_NAME),
]
