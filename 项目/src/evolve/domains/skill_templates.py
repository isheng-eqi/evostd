"""演示用 Domain Pack：Skill 工作流模板的进化（合成任务，无任何模型参与打分）。

对应赛题要求④："提炼通用业务逻辑，沉淀可复用、可快速部署的 Skill 工作流模板"。

它把"沉淀模板"变成一个**可优化的问题**：
    · 模板库太窄 → 大量任务解不了（solve_rate 低）
    · 模板库太肥（一个大而全的模板）→ 每一步都多跑无用步骤（extra_steps 惩罚）
    · 模板库太散 → 一堆用不上的模板（n_templates 惩罚）
    → 最优解是"**够通用、但不臃肿**"的一小组模板。这三者互相拉扯，才有进化的空间。

进化信号来自**真实失败诊断**（哪个任务失败了、缺哪一步），不是模型自评。
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from ..core import Archive, Candidate, EvalResult

# 六种原子能力（真实系统里就是六个 MCP 工具 / 六个 Skill 步骤）
OPS = ["检索条款", "抽取字段", "校验一致性", "计算指标", "生成结论", "引用溯源"]
CATEGORIES = ["合规校验", "数值核算", "证据溯源"]


@dataclass
class SkillTask:
    tid: str
    category: str
    required_ops: list[str]
    split: str = "evolve"


def make_tasks(n_per_cat: int, rng: random.Random, split: str) -> list[SkillTask]:
    """三类任务各自的"正确步骤组合"不同，且同一类内部也有变化——
    所以既不能一个通用模板全包（会臃肿），也不能一类一个死模板（会漏）。"""
    profiles = {
        "合规校验": [["检索条款", "抽取字段", "校验一致性", "生成结论"],
                     ["检索条款", "抽取字段", "校验一致性", "引用溯源", "生成结论"]],
        "数值核算": [["抽取字段", "计算指标", "生成结论"],
                     ["检索条款", "抽取字段", "计算指标", "校验一致性", "生成结论"]],
        "证据溯源": [["检索条款", "引用溯源", "生成结论"],
                     ["检索条款", "抽取字段", "引用溯源", "生成结论"]],
    }
    out = []
    for cat in CATEGORIES:
        for i in range(n_per_cat):
            out.append(SkillTask(tid=f"{split}-{cat}-{i:03d}", category=cat,
                                 required_ops=rng.choice(profiles[cat]), split=split))
    rng.shuffle(out)
    return out


class SkillTemplateDomain:
    """Skill 工作流模板进化领域包。"""

    name = "skill-template-evolution"
    kind = "skill_template"

    EXTRA_STEP_PENALTY = 0.03     # 每多跑一步无用步骤的惩罚
    TEMPLATE_COUNT_PENALTY = 0.01  # 每多沉淀一个模板的惩罚（防止堆模板凑分）

    def __init__(self, seed: int = 11):
        r = random.Random(seed)
        self.evolve_tasks = make_tasks(20, r, "E")
        self.heldout_tasks = make_tasks(12, r, "H")

    # ---- 执行器（确定性）：给定模板库，算解出率与平均冗余步数 ----
    def _execute(self, library: list[dict], tasks: list[SkillTask]) -> dict:
        solved, extra_total, failed = 0, 0, []
        for t in tasks:
            # 选模板：类别精确匹配优先，其次 when=any
            exact = [tpl for tpl in library if tpl.get("when") == t.category]
            generic = [tpl for tpl in library if tpl.get("when") == "*"]
            chosen = (exact or generic or [None])[0]
            if chosen is None:
                failed.append({"task": t.tid, "category": t.category,
                               "missing_ops": list(t.required_ops), "why": "无匹配模板"})
                continue
            steps = set(chosen.get("steps", []))
            missing = [o for o in t.required_ops if o not in steps]
            if missing:
                failed.append({"task": t.tid, "category": t.category,
                               "missing_ops": missing, "why": "模板缺步骤"})
                continue
            solved += 1
            extra_total += max(0, len(steps) - len(t.required_ops))
        n = len(tasks)
        return {
            "solve_rate": solved / n if n else 0.0,
            "mean_extra_steps": (extra_total / n) if n else 0.0,
            "failed": failed,
        }

    def _score_from(self, library: list[dict], tasks: list[SkillTask]) -> float:
        m = self._execute(library, tasks)
        used = set()
        for t in tasks:
            for tpl in library:
                if tpl.get("when") in (t.category, "*"):
                    used.add(tpl.get("name"))
                    break
        unused = len([tpl for tpl in library if tpl.get("name") not in used])
        return round(m["solve_rate"]
                     - self.EXTRA_STEP_PENALTY * m["mean_extra_steps"]
                     - self.TEMPLATE_COUNT_PENALTY * unused, 6)

    # ---- 种子：一个又窄又缺步骤的模板 ----
    def seed(self) -> Candidate:
        return Candidate(
            cid="seed-skill-0000", kind=self.kind, gen=0,
            payload={"library": [{"name": "tpl-basic", "when": "合规校验",
                                  "steps": ["检索条款", "生成结论"]}]},
            note="种子：只覆盖一类、且缺关键步骤",
        )

    # ---- 变异算子（LLM 替身）：从失败诊断里提炼模板 ----
    def mutator(self, parent: Candidate, archive: Archive, gen: int,
                rng: random.Random) -> Candidate | None:
        library = [dict(t, steps=list(t.get("steps", []))) for t in parent.payload.get("library", [])]
        parent_res = archive.results.get(parent.cid)
        failed = list((parent_res.artifacts or {}).get("failed", [])) if parent_res else []
        roll = rng.random()
        note = ""

        if roll < 0.70 and failed:
            # ★ 从失败里提炼：给失败最多的那一类，补一个带缺失步骤的模板
            fail = rng.choice(failed[:8])
            cat = fail["category"]
            missing = fail["missing_ops"]
            existing = next((t for t in library if t.get("when") == cat), None)
            if existing is not None:
                # 补步骤到已有模板
                new_steps = list(dict.fromkeys(existing["steps"] + missing))
                if len(new_steps) == len(existing["steps"]):
                    return None
                existing["steps"] = new_steps
                note = f"patch {existing['name']} += {missing}"
            else:
                if len(library) >= 5:
                    return None
                name = f"tpl-{cat}"
                library.append({"name": name, "when": cat, "steps": list(missing)})
                note = f"new_template {name} steps={missing}"

        elif roll < 0.85:
            # 泛化/特化：把一个类别专有模板改成通用模板（或反向）——探索"够通用"的边界
            if not library:
                return None
            t = rng.choice(library)
            if t.get("when") != "*" and rng.random() < 0.5:
                t["when"] = "*"
                note = f"generalize {t['name']}"
            else:
                t["when"] = rng.choice(CATEGORIES)
                note = f"specialize {t['name']}"

        else:
            # 精简：删掉一个用不上的模板
            if len(library) <= 1:
                return None
            i = rng.randrange(len(library))
            trial = [t for j, t in enumerate(library) if j != i]
            if self._score_from(trial, self.evolve_tasks) >= self._score_from(library, self.evolve_tasks) - 1e-9:
                library = trial
                note = f"prune tpl#{i}"
            else:
                return None

        if not library:
            return None
        return Candidate(cid=f"s-{gen:04d}-{rng.randrange(10**6):06d}", kind=self.kind,
                         payload={"library": library}, parent=parent.cid, gen=gen, note=note)

    # ---- 对照信号：对本次新增/改动的模板做扣住/施加消融 ----
    def contrast_signals(self, parent: Candidate, child: Candidate,
                         res: EvalResult, rng: random.Random) -> dict[str, bool]:
        child_lib = child.payload.get("library", [])
        parent_names = {t.get("name") for t in parent.payload.get("library", [])}
        out: dict[str, bool] = {}
        for tpl in child_lib:
            if tpl.get("name") in parent_names:
                continue                       # 只看本次新引入的模板
            with_it = self._score_from(child_lib, self.evolve_tasks)
            without = self._score_from([t for t in child_lib if t.get("name") != tpl["name"]],
                                       self.evolve_tasks)
            out[tpl["name"]] = with_it >= without
        return out

    def probes(self) -> list[tuple[str, Candidate]]:
        """必须被排到好候选后面的坏候选。"""
        return [
            ("empty-library", Candidate("probe-empty", self.kind, {"library": []})),
            ("all-in-one", Candidate("probe-aio", self.kind, {
                "library": [{"name": "tpl-omni", "when": "*", "steps": list(OPS)}]})),
            ("one-step", Candidate("probe-one", self.kind, {
                "library": [{"name": "tpl-thin", "when": "*", "steps": ["生成结论"]}]})),
        ]

    # ---- 验证器工厂 ----
    def make_verifiers(self):
        return (SkillTemplateVerifier(self, self.evolve_tasks, "evolve"),
                SkillTemplateVerifier(self, self.heldout_tasks, "heldout"))


class SkillTemplateVerifier:
    """外部、确定性：模板库在给定任务集上的得分。"""

    name = "skill-library-verifier"
    deterministic = True

    def __init__(self, domain: SkillTemplateDomain, tasks: list[SkillTask], split: str):
        self.domain = domain
        self.tasks = tasks
        self.split = split

    def evaluate(self, cand: Candidate) -> EvalResult:
        library = cand.payload.get("library", [])
        if not isinstance(library, list):
            return EvalResult(valid=False, artifacts={"error": "library 非列表"})
        if not library:
            return EvalResult(valid=False, artifacts={"error": "模板库为空"})
        for tpl in library:
            if not {"name", "when", "steps"} <= set(tpl):
                return EvalResult(valid=False, artifacts={"error": f"模板字段不全: {tpl}"})
            bad = [o for o in tpl["steps"] if o not in OPS]
            if bad:
                return EvalResult(valid=False, artifacts={"error": f"未知步骤: {bad}"})

        exec_res = self.domain._execute(library, self.tasks)
        score = self.domain._score_from(library, self.tasks)
        return EvalResult(
            valid=True, score=score,
            metrics={"solve_rate": round(exec_res["solve_rate"], 4),
                     "mean_extra_steps": round(exec_res["mean_extra_steps"], 3),
                     "n_templates": len(library), "n_failed": len(exec_res["failed"])},
            # ★ 失败诊断回流给变异算子（这是"从失败中提炼模板"的唯一信息来源）
            artifacts={"failed": exec_res["failed"][:10], "split": self.split},
        )


# 让本体领域包也用同一套工厂接口
def _attach_ontology_factory():
    from .ontology_growth import OntologyGrowthDomain
    from ..verifiers import RuleSetVerifier

    def make_verifiers(self):
        return (RuleSetVerifier(self.evolve_items, "evolve"),
                RuleSetVerifier(self.heldout_items, "heldout"))

    OntologyGrowthDomain.make_verifiers = make_verifiers  # type: ignore[attr-defined]


_attach_ontology_factory()
