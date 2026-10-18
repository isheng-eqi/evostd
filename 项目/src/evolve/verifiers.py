"""验证器：★ 唯一被行业实现的地方。

本项目对验证器的三条硬约束（写进代码，不靠自觉）：
  R1 外部：分数只由确定性程序给出，任何语言模型不得参与打分。
  R2 确定：同输入必须同输出（不依赖时钟、随机数、网络）。
  R3 可 held-out：必须有进化集与保留集两套数据，保留集只在审计时读取。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .core import Candidate, EvalResult


@dataclass
class LabeledItem:
    """一个带隐藏真值的语料条目。mutator 只能看到 features，看不到 label。"""
    item_id: str
    features: dict
    label: bool          # True = 应被本体覆盖的真概念


class RuleSetVerifier:
    """把"抽取规则集"当候选，用标注数据算 P/R/F1。

    规格：候选 payload = {"rules": [{"feature": str, "op": str, "value": number}, ...]}
    规则之间取"或"（任一条命中即判定为该规则集选中的概念）。
    """

    name = "ruleset-f1-verifier"
    deterministic = True          # 同输入同输出：闸门可用 min_n=1 的精确配对消融

    def __init__(self, items: list[LabeledItem], split_name: str = "evolve"):
        self.items = items
        self.split_name = split_name

    # ---- 规则求值（纯函数，满足 R2）----
    @staticmethod
    def _match(item: LabeledItem, rule: dict) -> bool:
        v = item.features.get(rule["feature"])
        if v is None:
            return False
        op, t = rule["op"], rule["value"]
        if op == ">=":
            return v >= t
        if op == "<=":
            return v <= t
        if op == "==":
            return v == t
        raise ValueError(f"未知算子: {op}")

    def _selects(self, cand: Candidate) -> set[str]:
        rules = cand.payload.get("rules", [])
        return {it.item_id for it in self.items
                if any(self._match(it, r) for r in rules)}

    def evaluate(self, cand: Candidate) -> EvalResult:
        rules = cand.payload.get("rules", [])
        # 无效候选（结构不合法）与低分候选必须区分开
        if not isinstance(rules, list) or not rules:
            return EvalResult(valid=False, score=0.0, metrics={},
                              artifacts={"error": "rules 为空或非法"})
        for r in rules:
            if not {"feature", "op", "value"} <= set(r):
                return EvalResult(valid=False, score=0.0, metrics={},
                                  artifacts={"error": f"规则字段不全: {r}"})

        selected = self._selects(cand)
        truth = {it.item_id for it in self.items if it.label}

        tp = len(selected & truth)
        fp = len(selected - truth)
        fn = len(truth - selected)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

        return EvalResult(
            valid=True,
            score=round(f1, 6),
            metrics={"precision": round(precision, 4), "recall": round(recall, 4),
                     "f1": round(f1, 4), "n_selected": len(selected), "n_truth": len(truth)},
            artifacts={"false_positives": sorted(selected - truth)[:12],
                       "false_negatives": sorted(truth - selected)[:12],
                       "split": self.split_name},
        )
