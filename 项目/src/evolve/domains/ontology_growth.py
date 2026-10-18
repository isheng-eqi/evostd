"""演示用 Domain Pack：本体生长（不依赖任何真实行业，纯合成数据）。

为什么先做这个：内核能不能跑出"代际上升曲线"，与选哪个行业无关。
先用一个"人造但可判定"的领域把回路验证通过，再插行业——这就是"内核先行，行业后插"。

它对应赛题要求②的真实含义：
    语料里有一批"应该被本体覆盖的概念"，但**没有标注**。
    系统的任务是**进化出一套抽取规则**，把概念从语料里挑出来。
    验证器（外部、确定性）用隐藏标注算 P/R/F1 —— 规则看不到标注，只看到特征。
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from ..core import Archive, Candidate, EvalResult
from ..verifiers import LabeledItem, RuleSetVerifier

FEATURES = ["def_count", "title_count", "freq", "suffix_id"]
OPS = [">=", "<=", "=="]


# ---------------------------------------------------------------------------
#  合成语料：特征可见，标注隐藏
# ---------------------------------------------------------------------------
def make_corpus(n_pos: int, n_neg: int, rng: random.Random, tag: str) -> list[LabeledItem]:
    """真概念由一条**隐藏规则**定义：
           label = (def_count >= 2) or (title_count >= 1 and freq >= 8)
       注意：任何单条规则都无法完美复现它 → 搜索空间里真的存在"要加第二条规则"的台阶。"""
    items: list[LabeledItem] = []

    for i in range(n_pos):
        # 三种正例形态，逼着搜索去找"或"的组合
        shape = rng.random()
        if shape < 0.40:                       # 靠定义句式
            def_count = rng.randint(2, 8)
            title_count = 0
            freq = rng.randint(3, 30)
        elif shape < 0.75:                     # 靠条款标题 + 高频
            def_count = rng.randint(0, 1)
            title_count = rng.randint(1, 4)
            freq = rng.randint(8, 60)
        else:                                  # 两个证据都有
            def_count = rng.randint(2, 6)
            title_count = rng.randint(1, 3)
            freq = rng.randint(5, 40)
        items.append(LabeledItem(
            item_id=f"{tag}-P{i:04d}",
            features={"def_count": def_count, "title_count": title_count,
                      "freq": freq, "suffix_id": rng.randint(0, 1)},
            label=True))

    for i in range(n_neg):
        # 负例：高频但无定义句式和标题 → 高频规则会踩坑
        def_count = rng.randint(0, 1)
        title_count = 0
        # 一半负例给高 freq，构造"用 freq 单规则会大量误选"的陷阱
        freq = rng.randint(20, 80) if rng.random() < 0.5 else rng.randint(1, 12)
        items.append(LabeledItem(
            item_id=f"{tag}-N{i:04d}",
            features={"def_count": def_count, "title_count": title_count,
                      "freq": freq, "suffix_id": rng.randint(0, 1)},
            label=False))

    rng.shuffle(items)
    return items


# ---------------------------------------------------------------------------
#  Domain Pack
# ---------------------------------------------------------------------------
class OntologyGrowthDomain:
    """本体生长领域包。"""

    name = "ontology-growth"
    kind = "extraction_rule"

    def __init__(self, seed: int = 7):
        r = random.Random(seed)
        self.evolve_items = make_corpus(120, 120, r, "E")     # 进化集（可见）
        self.heldout_items = make_corpus(60, 60, r, "H")      # 保留集（只在审计时读）
        # 领域自持一个进化集验证器，用于"扣住/施加"的配对对照
        self.contrast_verifier = RuleSetVerifier(self.evolve_items, split_name="evolve")

    # ---- 种子：一条显然不够用的规则 ----
    def seed(self) -> Candidate:
        return Candidate(
            cid="seed-0000", kind=self.kind, gen=0,
            payload={"rules": [{"feature": "freq", "op": ">=", "value": 30}]},
            note="种子：只用频次阈值（故意留坑，高频负例会误选）",
        )

    # ---- 变异算子 ----
    # 说明：真实系统里这一步由 LLM 读"失败诊断"后提出改写建议。
    # 为了保证本 demo **不依赖任何模型**，这里用"候选规则池 + 贪心选择"当 LLM 的替身。
    # 保留 20% 纯随机变异，目的是让闸门能同时看到"有用的规则"和"没用的规则"。
    def proposal_pool(self) -> list[dict]:
        pool = []
        for f in FEATURES:
            for v in sorted({it.features[f] for it in self.evolve_items}):
                pool.append({"feature": f, "op": ">=", "value": v})
        return pool

    def _score(self, rules: list[dict]) -> float:
        res = self.contrast_verifier.evaluate(
            Candidate(cid="_tmp", kind=self.kind, payload={"rules": rules}))
        return res.score if res.valid else -1.0

    def mutator(self, parent: Candidate, archive: Archive, gen: int,
                rng: random.Random) -> Candidate | None:
        rules = [dict(r) for r in parent.payload.get("rules", [])]
        roll = rng.random()
        note = ""

        if roll < 0.60:
            # 贪心扩张：从候选池里挑一条"加进去最涨分"的规则（模拟 LLM 提出有效改写）
            if len(rules) >= 4:
                return None
            have = {_rule_key(r) for r in rules}
            base = self._score(rules)
            pool = [r for r in self.proposal_pool() if _rule_key(r) not in have]
            rng.shuffle(pool)
            best_rule, best_score = None, base
            for r in pool[:60]:
                s = self._score(rules + [r])
                if s > best_score + 1e-9:
                    best_rule, best_score = r, s
            if best_rule is None:
                return None
            rules.append(best_rule)
            note = f"greedy_add {_rule_key(best_rule)} ({base:.4f}→{best_score:.4f})"

        elif roll < 0.75:
            # 随机新增（探索）：制造"可能没用"的规则，交给闸门裁决
            if len(rules) >= 4:
                return None
            rules.append({"feature": rng.choice(FEATURES), "op": ">=",
                          "value": rng.randint(1, 12)})
            note = "random_add"

        elif roll < 0.90 and len(rules) > 1:
            # 剪枝：删掉一条后若不掉分，就删
            i = rng.randrange(len(rules))
            trial = [r for j, r in enumerate(rules) if j != i]
            if self._score(trial) >= self._score(rules) - 1e-9:
                rules = trial
                note = f"prune {_rule_key(rules[i]) if i < len(rules) else '?'}"
            else:
                return None

        else:
            # 调阈值（局部爬坡）
            if not rules:
                return None
            r = rng.choice(rules)
            r["value"] = max(0, r["value"] + rng.choice([-4, -2, -1, 1, 2, 4]))
            note = "perturb"

        if not rules:
            return None
        return Candidate(cid=f"c-{gen:04d}-{rng.randrange(10**6):06d}", kind=self.kind,
                         payload={"rules": rules}, parent=parent.cid, gen=gen, note=note)

    # ---- 对照信号：对每条"本次新增的规则"做真实的扣住/施加消融 ----
    def contrast_signals(self, parent: Candidate, child: Candidate,
                         res: EvalResult, rng: random.Random) -> dict[str, bool]:
        parent_keys = {_rule_key(r) for r in parent.payload.get("rules", [])}
        out: dict[str, bool] = {}
        for r in child.payload.get("rules", []):
            key = _rule_key(r)
            if key in parent_keys:
                continue                     # 只看本次新引入的规则
            with_it = self.contrast_verifier.evaluate(child)
            without = self.contrast_verifier.evaluate(
                Candidate(cid="probe", kind=self.kind,
                          payload={"rules": [x for x in child.payload["rules"]
                                             if _rule_key(x) != key]}))
            if not (with_it.valid and without.valid):
                continue
            out[key] = with_it.score >= without.score     # 施加是否不劣于扣住
        return out

    # ---- 对抗探针：这些候选必须被验证器压下去 ----
    def probes(self) -> list[tuple[str, Candidate]]:
        return [
            ("select-all", Candidate("probe-all", self.kind,
                                     {"rules": [{"feature": "freq", "op": ">=", "value": 0}]})),
            ("suffix-only", Candidate("probe-suffix", self.kind,
                                      {"rules": [{"feature": "suffix_id", "op": "==", "value": 1}]})),
            ("high-freq-only", Candidate("probe-hf", self.kind,
                                         {"rules": [{"feature": "freq", "op": ">=", "value": 20}]})),
        ]


def _rule_key(r: dict) -> str:
    return f"{r['feature']}{r['op']}{r['value']}"
