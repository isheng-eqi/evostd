"""从语料里机械抽出「限额表」，并由表**自动生成条款**。

为什么要做这一步
----------------
判据语言 v2 的 `RANGE_BY_TABLE` 已经能表达"上限取决于另一个字段"，
但那张表当时是**人工从语料里抄进 ctx 的**——抄错一个数字，判据就静默地错了。

本模块把这段补上：**语料 → 表 → 条款 → 判据**，全链路机械。

语料里的原句（实测形态）
------------------------
    DOC-MFG-001  检修周期 A类 不超过 90 天，B类 不超过 180 天，C类 不超过 365 天。
    DOC-MFG-002  点检间隔 A类 不超过 24 小时，B类 不超过 72 小时，C类 不超过 168 小时。

抽取的**机械部分**（不需要人）：
  · 限额项的**名称**（检修周期 / 点检间隔）—— 取自"不超过"前面的短语
  · 每一档的**键**（A类/B类/C类）与**上限数值**（90/180/365）
  · **单位**（天 / 小时）
  · **来源溯源**：哪份文档、第几个字符区间

抽取中**需要人给一次的部分**（诚实标注，不假装是自动的）：
  · 中文名 → 资产字段名 的对应（"检修周期" → `maintenance_cycle_days`）
    这是**别名表**，人给一次、全局复用；给不出别名时该限额项**如实跳过**，不猜。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from standard.compiler import Clause, ClauseKind
from standard.grammar_v2 import ClauseKindV2


# ── 人给一次的别名表（中文名 → 资产字段名）。给不出就跳过，不猜。 ──────────────
# ── 人给一次：这张表按哪个字段分档（"设备类别" vs "数据分级"）────────────────
KEY_FIELD_BY_DOMAIN: dict[str, str] = {
    "manufacturing": "equipment_class",
    "gov_catalog": "data_level",
}
KEY_FIELD_LABEL: dict[str, str] = {
    "equipment_class": "设备类别",
    "data_level": "数据分级",
}

FIELD_ALIASES: dict[str, str] = {
    "检修周期": "maintenance_cycle_days",
    "点检间隔": "check_interval_hours",
    "数据质量评分": "quality_score",
    "密级": "secrecy",
    # 政务领域（换领域时只需在这里加一行，不改任何逻辑）
    "更新时限": "update_limit_days",
}

# ── 键的形态：`A类` / `甲级` / `一级` 之类。这里只认语料实际出现的形态。 ────────
# ★ 后缀**可选**：真实标准里档位名不一定带"类/级/档"（如"紧急/普通/重大"）。
#   写成必选会让整类句式抽不出来——实测"响应时限 紧急 不低于 1 分钟"就因此漏掉。
_KEY = r"[A-Za-z\u4e00-\u9fa5]{1,4}(?:类|级|档)?"

# ★ 连接词/虚词不能当档位名。放开后缀之后，"且"、"和"这类字会被误认成键，
#   于是"不低于 1 分钟 且 不超过 5 分钟"被拆成 {"紧急":…, "且":…}（实测踩到）。
_KEY_STOP = {"且", "和", "与", "及", "或", "并", "同时", "其中", "另外", "但是",
             "若", "当", "则", "均", "都", "应", "须", "需", "其", "该", "之",
             "为", "是", "在", "对", "按", "以", "等", "各", "不超过", "不得"}

# ── ★ 比较词的同义表达（真实标准里的写法远不止"不超过"）─────────────────────
#    左列 → 上限(max)，右列 → 下限(min)。顺序即优先级（长的先匹配，避免"不超过"吃掉"超过"）。
_UPPER_WORDS = ["不得超过", "不应超过", "不应高于", "不得高于", "不超过", "应不超过",
                "最多", "至多", "上限为", "上限是", "不高于", "≤", "<="]
_LOWER_WORDS = ["不得低于", "不应低于", "不应少于", "不得少于", "不低于", "应不低于",
                "不少于", "至少", "下限为", "下限是", "≥", ">="]
_ALT = "|".join(w.replace("≤", r"\u2264").replace("≥", r"\u2265") for w in
                sorted(_UPPER_WORDS + _LOWER_WORDS, key=len, reverse=True))
_BOUND = rf"(?P<op>{_ALT})"

# ── 一条限额：`<键> <比较词> <数> <单位>`，多条用顿号/逗号分隔 ──────────────────
# 键名**可选**：支持"紧急 不低于 1 分钟 且 不超过 5 分钟"这类同一档位并列上下限的写法，
# 后半句没有键名时沿用前一个键（见下方 `or last_key`）。
_ONE = re.compile(rf"(?:(?P<key>{_KEY})\s*)?{_BOUND}\s*(?P<num>\d+(?:\.\d+)?)\s*"
                  rf"(?P<unit>[^\s，,。；;]{{1,4}})")
# ── 限额项名：`<名> ... 不超过`，名在第一个键之前 ────────────────────────────
_NAME = re.compile(rf"(?P<name>[\u4e00-\u9fa5]{{2,8}})\s*(?P<key>{_KEY})\s*{_BOUND}")


@dataclass
class LimitTable:
    """一张机械抽出来的限额表 + 它的来源。"""
    table_key: str                      # 生成的 ctx 表名
    field: str                          # 对应的资产字段
    unit: str                           # 单位（天/小时/…）
    rows: dict[str, dict]               # {"A类": {"max": 90}, ...}
    provenance: dict                    # {doc_id, span, raw, name, alias_from}
    skipped: list[str] = field(default_factory=list)   # 抽到但没别名、如实跳过的项


def _doc_iter(docs):
    for d in docs:
        yield getattr(d, "doc_id", "?"), _raw(d)


def _raw(d) -> str:
    try:
        from domains.manufacturing.corpus import doc_raw_text
        return doc_raw_text(d)
    except Exception:
        return getattr(d, "text", "") or ""


def extract_limit_tables(docs, aliases: Optional[dict] = None) -> list[LimitTable]:
    """从语料里机械抽出全部限额表。**不改语料、不猜字段。**"""
    aliases = FIELD_ALIASES if aliases is None else aliases
    out: list[LimitTable] = []
    for doc_id, text in _doc_iter(docs):
        # 以句为单位扫，避免跨句误配
        for sent in re.split(r"[。；;\n]", text):
            if not re.search(_BOUND, sent):
                continue
            m = _NAME.search(sent)
            if not m:
                continue
            name = m.group("name")
            rows: dict[str, dict] = {}
            unit = ""
            spans = []
            last_key = None
            for one in _ONE.finditer(sent):
                k = one.group("key")
                if k is not None and k in _KEY_STOP:       # 连接词不是档位名
                    k = None
                k = k or last_key                          # ★ 无键名则沿用前一个
                if k is None:
                    continue                              # 句首就没键名 → 不猜
                last_key = k
                num, u = one.group("num"), one.group("unit")
                op = one.group("op") or ""
                val = float(num) if "." in num else int(num)
                # ★ 按比较词判定这是**上限**还是**下限** —— 真标准里两者都有，
                #   原实现只会写 max，遇到"不低于"就把下限错记成上限。
                # ★ 必须用**完整比较词**判定：按前两字判会把"不得超过"误判成下限
                #   （它和"不得低于"前两字都是"不得"）——实测踩到，三张表全反了。
                _op = op.strip()
                side = "min" if _op in _LOWER_WORDS else "max"
                if k not in rows:                       # 同一句里同名键取首次
                    rows[k] = {side: val}
                    spans.append(one.span())
                    unit = unit or u
                elif side not in rows[k]:               # 同一键的上下限各记一次
                    rows[k][side] = val
                    spans.append(one.span())
                    unit = unit or u
            if len(rows) < 2 and not any(len(v) >= 2 for v in rows.values()):
                continue                                # 少于两档且无上下限，不算"表"
            field_name = aliases.get(name)
            if not field_name:
                out.append(LimitTable(table_key="", field="", unit=unit, rows=rows,
                                      provenance={"doc_id": doc_id, "raw": sent.strip()},
                                      skipped=[f"{name}（无字段别名，如实跳过）"]))
                continue
            out.append(LimitTable(
                table_key=f"{field_name}_limit",
                field=field_name, unit=unit, rows=rows,
                provenance={"doc_id": doc_id,
                            "span": [spans[0][0], spans[-1][1]] if spans else None,
                            "raw": sent.strip(),
                            "name": name, "alias_from": name}))
    return out


def clauses_from_tables(tables: list[LimitTable], standard_name: str,
                        start_no: int = 45, domain: str = "manufacturing") -> list[Clause]:
    """由抽出来的表**自动生成条款**。条款原文由表机械拼出，不是人写的。"""
    out: list[Clause] = []
    n = start_no
    for t in tables:
        if not t.table_key or len(t.rows) < 2:
            continue
        def _row_txt(k):
            r = t.rows[k]
            if "min" in r and "max" in r:
                return f"{k}{r['min']}~{r['max']}{t.unit}"
            if "min" in r:
                return f"{k}≥{r['min']}{t.unit}"
            return f"{k}≤{r['max']}{t.unit}"
        desc = "、".join(_row_txt(k) for k in t.rows)
        kf = KEY_FIELD_BY_DOMAIN.get(domain, "equipment_class")
        label = KEY_FIELD_LABEL.get(kf, kf)
        text = (f"{t.provenance.get('name', t.field)}"
                f"不得超过该{label}的上限（{desc}）。")
        out.append(Clause(
            f"第{n}条", f"{t.provenance.get('name', t.field)}按类别设上限",
            ClauseKindV2.RANGE_BY_TABLE, text,
            {"field": t.field, "key_field": kf, "table_key": t.table_key},
            standard=standard_name))
        n += 1
    return out


def ctx_from_tables(tables: list[LimitTable]) -> dict:
    """把表装进 ctx。"""
    return {t.table_key: t.rows for t in tables if t.table_key}


def provenance_report(tables: list[LimitTable]) -> list[dict]:
    """溯源清单：每一档数字来自哪份文档的哪一段。"""
    rep = []
    for t in tables:
        if not t.table_key:
            rep.append({"status": "skipped", "reason": t.skipped})
            continue
        rep.append({"status": "ok", "table": t.table_key, "field": t.field,
                    "unit": t.unit, "rows": t.rows,
                    "doc_id": t.provenance.get("doc_id"),
                    "span": t.provenance.get("span"),
                    "raw": t.provenance.get("raw")})
    return rep
