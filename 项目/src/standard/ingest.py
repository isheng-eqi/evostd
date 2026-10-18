"""标准摄入管线：把**一部真实标准文本**半自动变成可执行判据库。

这是"低资源场景下领域本体半自动化构建"的操作化实现。
回答的问题是：**给我一部真实标准，多久能变成可跑的判据库？**

分层原则（与整个项目一致）
--------------------------
    能机械做的，全部机械做；
    做不了的，**如实列进人工待办清单**，绝不猜。

四步：
  ① 机械切分条款 —— 认多种真实编号形态（第N条 / N. / N、/ N.N / 一、）
  ② 机械归类条款类型 —— 按句式特征判定 12 类判据之一
  ③ 机械抽取参数 —— 字段名走别名表（人给一次），数值/词表/正则从原文抽
  ④ 编译 + 锚点 + 覆盖率 + **人工待办清单**

输出里最重要的是最后一项：**哪些条款机器搞不定、缺什么、要人补什么。**
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from standard.compiler import Clause, ClauseKind
from standard.grammar_v2 import ClauseKindV2


# ============================================================================
#  ① 切分
# ============================================================================
# 真实标准的编号形态（按优先级排列，长的先试）
_NUM_PATTERNS = [
    r"第\s*[0-9〇一二三四五六七八九十百]+\s*条",       # 第12条 / 第十二条
    r"[0-9]+(?:\.[0-9]+){1,3}",                        # 4.1.2
    r"[0-9]+(?:\.[0-9]+)?[、\.]",                      # 4. / 4、
    r"[一二三四五六七八九十]+、",                        # 一、
]


def split_clauses(text: str) -> list[dict]:
    """机械切分条款。返回 [{no, text}]，保持原文顺序与原文（不改写一个字）。"""
    lines = [l.rstrip() for l in text.split("\n")]
    # 先按行聚合：编号可能出现在行首，也可能在行中
    joined = "\n".join(lines)
    # 找出所有编号出现的位置
    marks: list[tuple[int, int, str]] = []
    for pat in _NUM_PATTERNS:
        for m in re.finditer(pat, joined):
            # 编号必须出现在行首、或前面是空白/换行（避免把正文里的数字当编号）
            pre = joined[max(0, m.start() - 2):m.start()]
            if m.start() > 0 and not re.search(r"[\s\n（(]", pre + " "):
                continue
            marks.append((m.start(), m.end(), m.group().strip()))
    if not marks:
        return [{"no": "", "text": joined.strip()}] if joined.strip() else []
    # 去重叠：同一起点取最长的编号
    marks.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    picked: list[tuple[int, int, str]] = []
    for mk in marks:
        if picked and mk[0] < picked[-1][1]:
            continue
        picked.append(mk)
    out = []
    for i, (s, e, no) in enumerate(picked):
        end = picked[i + 1][0] if i + 1 < len(picked) else len(joined)
        body = joined[e:end].strip()
        if body:
            out.append({"no": no, "text": body})
    return out


# ============================================================================
#  ② 归类（句式特征 → 判据类型）
# ============================================================================
# 每条规则：(判据类型, 触发正则, 说明)。**顺序即优先级。**
_RULES: list[tuple[Any, str, str]] = [
    (ClauseKindV2.UNIQUE_ACROSS,
     r"不得(?:重复登记|被重复登记)|同一份.{0,6}不得.{0,6}登记在两个",
     "唯一性：同一取值不得跨资产重复"),
    (ClauseKindV2.FORBID_WHEN,
     r"应为空值[，,]?\s*不得填写|不得填写[，,]?\s*应为空值",
     "条件禁止：条件成立时某字段必须为空"),
    (ClauseKindV2.NOT_FUTURE,
     r"不得(?:晚于|超过|早于)基准时间|不得为未来",
     "基准时间：取值不得晚于显式注入的基准时间"),
    (ClauseKindV2.RANGE_BY_TABLE,
     r"不得超过该.{0,6}(?:类别|分级|档|等级)的上限|按.{0,4}(?:类别|分级|档).{0,6}设上限",
     "查表区间：上限取决于另一字段（表须由语料抽出）"),
    (ClauseKind.FORBIDDEN,
     r"不得(?:引用|使用|登记).{0,10}(?:已废止|已停用|已下线|已撤销)|"
     r"(?:已废止|已停用|已下线).{0,6}不得",
     "禁止取值：命中黑名单受控集即违例"),
    (ClauseKind.REFERENCE,
     r"应(?:存在|属于|来自)于?.{0,12}(?:清单|名录|目录|台账|册)(?:中|内)|"
     r"应在.{0,12}(?:清单|名录|目录|台账)(?:中|内)",
     "跨文档引用：取值必须存在于受控清单"),
    (ClauseKind.PATTERN,
     r"应为\s*[“\"].{1,12}[”\"]\s*前缀|应为\s*\d+\s*位|应匹配|格式应为",
     "格式约束：取值必须匹配正则"),
    (ClauseKind.CONDITIONAL,
     r"为\s*[“\"].{1,10}[”\"]\s*的.{0,20}应同时登记|当.{0,12}时.{0,8}应(?:一并)?登记",
     "条件必填：条件成立时某字段必须存在"),
    (ClauseKind.ENUM,
     r"应(?:属于|为)\s*受控词表|分为\s*[^。]{2,60}[，,、][^。]{0,60}(?:之一|三类|四类)|"
     r"应填写为|应为\s*[^。]{2,10}、[^。]{2,10}、[^。]{2,10}\s*之一",
     "受控词表：取值必须来自枚举"),
    (ClauseKind.RANGE,
     r"应为\s*-?\d+(?:\.\d+)?\s*(?:至|~|-|—)\s*\d+(?:\.\d+)?\s*(?:之间)?|"
     r"应为\s*\d+\s*至\s*\d+",
     "数值范围：取值必须落在常量区间"),
    (ClauseKind.REQUIRED,
     r"应(?:当)?登记.{0,20}(?:不得为空|不能为空|必填)|不得为空",
     "必填约束：字段必须存在且非空"),
    (ClauseKind.CONSISTENCY,
     r"不得(?:晚于|早于|超过|大于|小于)|应(?:早于|晚于)|不得倒挂",
     "跨字段一致性：字段间序关系"),
]
# 归不了类的条款——这些**不是失败**，是人工待办的输入
_UNCOMPILABLE_HINT = re.compile(
    r"真实|完整|准确|及时|合理|规范|有效|必要|适当|原则上|视情况|另行|其他")


def classify(text: str) -> dict:
    """机械归类一条条款。归不了就如实返回 uncompilable + 原因。"""
    for kind, pat, why in _RULES:
        if re.search(pat, text):
            return {"kind": kind, "why": why, "matched": True}
    if _UNCOMPILABLE_HINT.search(text):
        return {"kind": None, "matched": False,
                "why": "含主观/程度词（真实/完整/及时/合理…），**只能靠人判断**"}
    return {"kind": None, "matched": False,
            "why": "句式未匹配任何已知判据类型（可能是新类型，或需要改写条款）"}


# ============================================================================
#  ③ 抽取参数（字段名走别名表；数值/词表/正则可从原文机械抽）
# ============================================================================
def _find_field(text: str, aliases: dict) -> Optional[str]:
    """按别名表找字段名。**别名叫不上的返回 None，不猜。**"""
    for zh, en in sorted(aliases.items(), key=lambda x: -len(x[0])):
        if zh in text:
            return en
    return None


def _find_field_last(text: str, aliases: dict) -> Optional[str]:
    """取**最靠右**的字段名匹配。

    ★ 为什么需要：条件句里常同时出现两个别名，例如
      "共享类型为「无条件共享」的目录，其共享条件应为空值"
      ——"共享类型"是条件作用的字段，"共享条件"才是被禁止的字段。
      按"最先匹配"会取错（实测把 forbid_field 抽成了 share_type，
      导致该锚点自检不一致）。**被禁止/被要求的字段总是紧邻动作词之前。**
    """
    best, best_pos = None, -1
    for zh, en in aliases.items():
        pos = text.rfind(zh)
        if pos > best_pos:
            best, best_pos = en, pos
    return best


def _extract_params(text: str, kind, aliases: dict, registry_names: set,
                    id_field: str = "asset_id") -> tuple[dict, list]:
    """从原文机械抽参数。返回 (params, 缺什么)。"""
    miss: list[str] = []
    p: dict = {}
    f = _find_field(text, aliases)
    if f is None:
        miss.append("字段名（别名表里没有对应中文名）")
    else:
        p["field"] = f

    k = kind.value if hasattr(kind, "value") else kind
    if k == ClauseKind.RANGE.value:
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:至|~|-|—)\s*(\d+(?:\.\d+)?)", text)
        if m:
            p["min"], p["max"] = float(m.group(1)), float(m.group(2))
            if p["min"].is_integer(): p["min"] = int(p["min"])
            if p["max"].is_integer(): p["max"] = int(p["max"])
        else:
            miss.append("区间数值（原文里没找到 N 至 M 的写法）")
    elif k == ClauseKind.ENUM.value:
        seg = text.split("：")[-1] if "：" in text else text
        seg = re.sub(r"[。；;]", "", seg)
        seg = re.sub(r"(?:之一|三类|四类|受控词表)", "", seg)
        allowed = [x.strip() for x in re.split(r"[、，,]", seg) if x.strip()]
        allowed = [x for x in allowed if 1 <= len(x) <= 8 and not x.endswith("的")]
        if allowed:
            p["allowed"] = allowed
        else:
            miss.append("受控词表取值（原文里没抽出枚举项）")
    elif k == ClauseKind.PATTERN.value:
        m = re.search(r"[“\"]([^”\"]{1,12})[”\"]\s*前缀", text)
        n = re.search(r"(\d+)\s*位", text)
        if m and n:
            body = r"\d" if "数字" in text else (r"[A-Z0-9]" if "大写" in text else r"\w")
            p["pattern"] = rf"{re.escape(m.group(1))}{body}{{{n.group(1)}}}"
        else:
            miss.append("格式正则（原文里的前缀/位数不完整）")
    elif k in (ClauseKind.REFERENCE.value, ClauseKind.FORBIDDEN.value):
        # 有些写法只说"不得引用已下线信息系统的**数据**"，不直接点字段名。
        # 这里**不猜**：只在文本里能对上别名表时才填，否则记进待办。
        hit = [r for r in registry_names if r in text]
        if hit:
            p["registry"] = sorted(hit)[0]
        else:
            miss.append("受控清单名（原文提到的清单不在已知清单里）")
    elif k == ClauseKind.CONSISTENCY.value:
        m = re.search(r"([\u4e00-\u9fa5]{2,8})\s*不得(晚于|早于|超过|大于|小于)\s*([\u4e00-\u9fa5]{2,8})", text)
        if m:
            lf = _find_field(m.group(1), aliases)
            rf = _find_field(m.group(3), aliases)
            if lf and rf:
                p["left"], p["right"] = lf, rf
                # ★ 正则只捕获"晚于/早于/…"，不含前面的"不得"（实测踩到 KeyError）
                p["op"] = {"晚于": "<=", "早于": ">=",
                           "超过": "<=", "大于": "<=", "小于": ">="}[m.group(2)]
            else:
                miss.append("两个字段名（别名表里至少一个对不上）")
        else:
            miss.append("参与比较的两个字段")
    elif k == ClauseKind.CONDITIONAL.value:
        m = re.search(r"为\s*[“\"]([^”\"]{1,10})[”\"]", text)
        # ★ 还要知道"条件作用在**哪个字段**上"——只抽取值不抽字段，
        #   锚点生成会以 KeyError: 'when_field' 直接崩（实测踩到）。
        wf = _find_field(text[:m.start()] if m else "", aliases)
        req = _find_field(text.split("应同时登记")[-1] if "应同时登记" in text else "", aliases)
        if not req:      # 换个说法："…应同时登记 X" 之外还有 "…应登记 X"
            req = _find_field_last(text, aliases) if "登记" in text else None
        if m and wf and req:
            p["when_field"], p["when_value"], p["require_field"] = wf, m.group(1), req
        else:
            missing_bits = []
            if not wf: missing_bits.append("条件作用的字段名")
            if not m: missing_bits.append("条件取值")
            if not req: missing_bits.append("被要求登记的字段名")
            miss.append(" 或 ".join(missing_bits) or "条件必填的三项参数")
    elif k == ClauseKindV2.FORBID_WHEN.value:
        m = re.search(r"为\s*[“\"]([^”\"]{1,10})[”\"]", text)
        wf = _find_field(text[:m.start()] if m else "", aliases)
        ff = _find_field_last(text.split("应为空值")[0], aliases)
        if m and wf and ff:
            p["when_field"], p["when_value"], p["forbid_field"] = wf, m.group(1), ff
        else:
            missing_bits = []
            if not wf: missing_bits.append("条件作用的字段名")
            if not m: missing_bits.append("条件取值")
            if not ff: missing_bits.append("被禁止的字段名")
            miss.append(" 或 ".join(missing_bits) or "条件禁止的三项参数")
    elif k == ClauseKindV2.NOT_FUTURE.value:
        p["now_key"] = "now"                      # 基准时间是注入的，不是抽的
    elif k == ClauseKindV2.UNIQUE_ACROSS.value:
        # ★ 标识字段是**领域知识**（制造业 asset_id / 政务 catalog_id），
        #   写死会让集合级判据在别的领域静默失效——实测自检报 1 处不一致。
        p["id_field"] = id_field
    return p, miss


# ============================================================================
#  ④ 主管线
# ============================================================================
@dataclass
class IngestReport:
    n_clauses: int = 0
    clauses: list = field(default_factory=list)
    uncompilable: list = field(default_factory=list)
    todo: list = field(default_factory=list)          # ★ 人工待办
    kind_stats: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {"n_clauses": self.n_clauses,
                "n_compilable": self.n_clauses - len(self.uncompilable),
                "compile_rate": round((self.n_clauses - len(self.uncompilable))
                                      / self.n_clauses, 4) if self.n_clauses else 0.0,
                "kind_stats": self.kind_stats,
                "n_todo": len(self.todo)}


def ingest(text: str, standard_name: str = "（未命名标准）",
           aliases: Optional[dict] = None,
           registry_names: Optional[set] = None,
           id_field: str = "asset_id") -> tuple[list[Clause], IngestReport]:
    """把一部标准文本半自动变成条款列表 + 报告。"""
    aliases = aliases or {}
    registry_names = registry_names or set()
    raw = split_clauses(text)
    rep = IngestReport(n_clauses=len(raw))
    out: list[Clause] = []
    for rc in raw:
        no, body = rc["no"] or f"第{len(out)+1}条", rc["text"]
        cls = classify(body)
        kind = cls["kind"]
        if kind is None:
            rep.uncompilable.append({"no": no, "text": body, "why": cls["why"]})
            rep.todo.append({"no": no, "blocked_by": "类型无法机械归类",
                             "need": cls["why"], "text": body[:80]})
            out.append(Clause(no, "", ClauseKind.REQUIRED, body, {},
                              standard=standard_name, compilable=False))
            continue
        params, miss = _extract_params(body, kind, aliases, registry_names, id_field)
        kname = kind.value if hasattr(kind, "value") else str(kind)
        rep.kind_stats[kname] = rep.kind_stats.get(kname, 0) + 1
        if miss:
            rep.uncompilable.append({"no": no, "text": body,
                                     "why": "参数抽取不全：" + "；".join(miss)})
            rep.todo.append({"no": no, "blocked_by": f"类型={kname}，参数缺失",
                             "need": "；".join(miss), "text": body[:80]})
            out.append(Clause(no, "", kind, body, params,
                              standard=standard_name, compilable=False))
            continue
        out.append(Clause(no, "", kind, body, params, standard=standard_name))
    rep.clauses = out
    return out, rep
