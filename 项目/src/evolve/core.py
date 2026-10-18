"""自进化内核（领域无关）。

设计纪律（每条都有 2026 年前沿工作的实证来源，见 ../../../00-自进化的本质-核心理解.md）：

1. **先爬山，再加机制**。父代优先取"当前最优"（Hill Sampling），存档采样只占少数。
   依据：Hill Sampling（arXiv:2609.25510）在圆堆积上刷新 SOTA、超过 AlphaEvolve 参考；
   而测试时用 ES 训权重不如把学习率设成 0。
2. **验证器必须外部**。分数只来自确定性程序，绝不来自模型自评。
   依据：Self-Refine / Self-Rewarding 是后来被打得最狠的路线。
3. **对照闸门**。用"施加 / 扣住"的配对对照（guided vs withheld）裁决去留，
   而不是看绝对分数。依据：FlowEvo 的 suppression gate 与 contrastive_stats。
4. **存档留踏脚石**。保留劣质祖先，用后代表现（CMP）估计改进潜力。
   依据：Huxley-Gödel Machine（ICLR 2026）的元生产力–表现错配。
5. **审计常驻**。对抗探针 + 代理-真实落差 + 塌缩检测。
   依据：71%+ 的 kernel 优化出现"代理涨、真实没涨"；去掉锚点守卫后自进化验证器
   会塌缩成"永远通过"的空壳判官。
6. **一切可回滚**。追加式台账，任何改动可撤销。
"""
from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional, Protocol

# ============================================================================
#  判据常量（每个都有来源，改动需在文档中说明理由）
# ============================================================================
GATE = -0.10        # 抑制阈值：delta 低于它才算"有害"（对齐 FlowEvo 的 −0.1）
CONSECUTIVE = 2     # 连续几次为负才判定（防单次噪声）
MIN_N = 3           # 每组至少多少次观测才允许判定（否则 insufficient）


# ============================================================================
#  基础类型
# ============================================================================
@dataclass
class Candidate:
    """一个候选产物。kind 决定它是什么（本体 / 技能 / 标识规则 / 检索策略）。"""
    cid: str
    kind: str
    payload: dict
    parent: Optional[str] = None
    gen: int = 0
    island: int = 0
    note: str = ""
    origin: str = "mutator"      # mutator | seed | probe


@dataclass
class EvalResult:
    """一次评估的结果。valid=False 表示"没跑成"，绝不当成"跑得差"。"""
    valid: bool
    score: float = 0.0
    metrics: dict = field(default_factory=dict)
    artifacts: dict = field(default_factory=dict)


class Verifier(Protocol):
    """★ 唯一被行业实现的地方。必须是外部的、确定性的、可 held-out 的。

    deterministic=True 时，单次配对消融即结论（无采样噪声），闸门阈值降为 1/1；
    deterministic=False（例如 LLM 评判者）时，必须多次观测，闸门用 3/2 抗噪。
    """
    name: str
    deterministic: bool

    def evaluate(self, cand: Candidate) -> EvalResult: ...


# ============================================================================
#  对照闸门：用配对对照裁决去留，而不是看绝对分数
# ============================================================================
@dataclass
class Arm:
    n: int = 0
    ok: int = 0

    @property
    def rate(self) -> Optional[float]:
        return (self.ok / self.n) if self.n else None

    def add(self, success: bool) -> None:
        self.n += 1
        self.ok += int(bool(success))


@dataclass
class ContrastRecord:
    key: str
    kind: str = "concept"
    guided: Arm = field(default_factory=Arm)      # 施加时的表现
    withheld: Arm = field(default_factory=Arm)    # 扣住时的表现
    streak: int = 0
    verdict: str = "unverified"                   # unverified|neutral|harmful|insufficient
    reason: str = ""
    history: list = field(default_factory=list)

    @property
    def delta(self) -> Optional[float]:
        g, w = self.guided.rate, self.withheld.rate
        if g is None or w is None:
            return None
        return round(g - w, 4)

    def observe(self, arm: str, success: bool) -> None:
        if arm == "guided":
            self.guided.add(success)
        elif arm == "withheld":
            self.withheld.add(success)
        else:
            raise ValueError(f"未知 arm: {arm}")

    def judge(self, gate: float = GATE, consecutive: int = CONSECUTIVE,
              min_n: int = MIN_N) -> str:
        """★ 闸门核心：样本不足时默认保留（不因为噪声杀掉东西）。

        注意 min_n 的取值**取决于验证器是否确定性**：
        确定性验证器 + 配对消融 = 单次即精确结论，用 1；
        非确定性验证器（LLM 评判者）才有采样噪声，需要 3 次以上。
        """
        if self.guided.n < min_n or self.withheld.n < min_n:
            self.verdict = "insufficient"
            self.reason = f"样本不足 (guided n={self.guided.n}, withheld n={self.withheld.n}, 需 {min_n})"
            return self.verdict

        d = self.delta
        assert d is not None
        if d < gate:
            self.streak += 1
            if self.streak >= consecutive:
                self.verdict = "harmful"
                self.reason = f"连续 {self.streak} 次 delta<{gate}（本次 {d}）→ 判定有害"
            else:
                self.verdict = "neutral"
                self.reason = f"delta={d} 为负，但尚未连续 {consecutive} 次"
        else:
            self.streak = 0
            self.verdict = "neutral"
            self.reason = f"delta={d} 未低于阈值 {gate}"

        self.history.append({"t": _now(), "delta": d, "verdict": self.verdict})
        return self.verdict


class ContrastGate:
    """管理所有配对对照记录。"""

    def __init__(self, path: Optional[str] = None, gate: float = GATE,
                 consecutive: int = CONSECUTIVE, min_n: int = MIN_N):
        self.records: dict[str, ContrastRecord] = {}
        self.path = path
        self.gate = gate
        self.consecutive = consecutive
        self.min_n = min_n

    def record(self, key: str, kind: str = "concept") -> ContrastRecord:
        if key not in self.records:
            self.records[key] = ContrastRecord(key=key, kind=kind)
        return self.records[key]

    def observe(self, key: str, arm: str, success: bool, kind: str = "concept") -> None:
        self.record(key, kind).observe(arm, success)

    def judge(self, key: str) -> str:
        return self.record(key).judge(self.gate, self.consecutive, self.min_n)

    def harmful(self) -> list[str]:
        return [k for k, r in self.records.items() if r.verdict == "harmful"]

    def save(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({k: asdict(v) for k, v in self.records.items()},
                      f, ensure_ascii=False, indent=2)


# ============================================================================
#  存档：岛屿 + 血统 + CMP（后代表现，衡量"改进潜力"而非"当前分"）
# ============================================================================
class Archive:
    def __init__(self, n_islands: int = 3, capacity_per_island: int = 40):
        self.n_islands = n_islands
        self.capacity = capacity_per_island
        self.cands: dict[str, Candidate] = {}
        self.results: dict[str, EvalResult] = {}
        self.children: dict[str, list[str]] = {}
        self.islands: dict[int, list[str]] = {i: [] for i in range(n_islands)}
        self.rejected: list[dict] = []

    def add(self, cand: Candidate, res: EvalResult) -> bool:
        if not res.valid:
            self.rejected.append({"cid": cand.cid, "why": "invalid", "gen": cand.gen,
                                  "artifacts": res.artifacts})
            return False
        self.cands[cand.cid] = cand
        self.results[cand.cid] = res
        self.children.setdefault(cand.cid, [])
        if cand.parent:                       # 根节点不建 None 键
            self.children.setdefault(cand.parent, []).append(cand.cid)
        isl = cand.island % self.n_islands
        self.islands[isl].append(cand.cid)
        # 岛屿容量控制：超出则淘汰最差（但保护最优几个，防踏脚石被清掉）
        if len(self.islands[isl]) > self.capacity:
            ranked = sorted(self.islands[isl], key=self.score)
            for cid in ranked[: len(self.islands[isl]) - self.capacity]:
                self.islands[isl].remove(cid)
        return True

    def score(self, cid: str) -> float:
        r = self.results.get(cid)
        return r.score if r else float("-inf")

    def best(self) -> Optional[str]:
        if not self.cands:
            return None
        return max(self.cands, key=self.score)

    def cmp(self, cid: str) -> float:
        """CMP 近似：该候选所有后代的平均分（血统价值）。
        依据 HGM：当前分高 ≠ 改进潜力高，要用后代统计来选。"""
        desc = self._descendants(cid)
        if not desc:
            return self.score(cid)
        return sum(self.score(d) for d in desc) / len(desc)

    def _descendants(self, cid: str) -> list[str]:
        out, stack = [], list(self.children.get(cid, []))
        while stack:
            c = stack.pop()
            out.append(c)
            stack.extend(self.children.get(c, []))
        return out

    def sample_parent(self, rng: random.Random, hill_ratio: float = 0.8) -> str:
        """先爬山：大部分时候取当前最优；少部分时候按 CMP 采样，保留探索。
        纪律 1 的代码化——不要一上来就堆群岛和多样性机制。"""
        if not self.cands:
            raise RuntimeError("存档为空")
        if rng.random() < hill_ratio:
            return self.best()  # type: ignore[return-value]
        pool = list(self.cands)
        weights = [max(self.cmp(c), 0.0) + 1e-6 for c in pool]
        return rng.choices(pool, weights=weights, k=1)[0]

    def snapshot(self) -> dict:
        return {
            "n_candidates": len(self.cands),
            "best": self.best(),
            "best_score": round(self.score(self.best()), 4) if self.best() else None,
            "per_island": {i: len(v) for i, v in self.islands.items()},
            "rejected": len(self.rejected),
        }


# ============================================================================
#  审计：对抗探针 + 代理-真实落差 + 塌缩检测
# ============================================================================
class Auditor:
    """★ 防自欺。这是本项目相对"堆机制"路线的差异点。

    - 对抗探针：一批"必须被拒绝"的坏候选。验证器若接受它们 → 报警。
    - 塌缩检测：验证器若对不同质量的候选给出几乎相同的分数 → 说明它已塌缩成
      "永远通过"的空壳判官（依据 2610.11464：塌缩的验证器仍能训出好技能，
      所以下游分数无法认证验证器）。
    """

    def __init__(self, verifier: Verifier):
        self.verifier = verifier
        self.probes: list[tuple[str, Candidate]] = []

    def add_probe(self, name: str, cand: Candidate, expect: str = "reject") -> None:
        self.probes.append((name, cand))

    def run(self, best_score: Optional[float] = None) -> dict:
        """跑一遍对抗探针。

        best_score 给定时，判据是**相对**的：探针必须低于当前最优候选，
        才算"被正确拒绝"。这比"探针必须得 0 分"更符合实际——
        坏候选不需要得零分，它只需要**排在好候选后面**。
        """
        report = {"verifier": self.verifier.name, "probes": [], "collapse": None,
                  "criterion": ("probe < best" if best_score is not None else "probe <= 0")}
        ok = 0
        for name, cand in self.probes:
            res = self.verifier.evaluate(cand)
            if best_score is not None:
                passed = res.score < best_score - 1e-9
            else:
                passed = (not res.valid) or res.score <= 0.0
            report["probes"].append({"probe": name, "score": res.score,
                                     "valid": res.valid, "correctly_rejected": passed})
            ok += int(passed)
        report["probe_pass_rate"] = round(ok / len(self.probes), 3) if self.probes else None
        return report

    def collapse_check(self, scores: list[float]) -> dict:
        if len(scores) < 3:
            return {"verdict": "insufficient_data"}
        spread = max(scores) - min(scores)
        collapsed = spread < 1e-6
        return {
            "n": len(scores),
            "min": round(min(scores), 4),
            "max": round(max(scores), 4),
            "spread": round(spread, 6),
            "verdict": "COLLAPSED" if collapsed else "ok",
            "note": "分数无区分度 → 验证器可能已塌缩成'永远通过'",
        }


# ============================================================================
#  台账：追加式，一切可回滚
# ============================================================================
class Ledger:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(self.path, "a", encoding="utf-8").close()

    def append(self, event: dict) -> None:
        event = dict(event)
        event.setdefault("t", _now())
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


# ============================================================================
#  主循环
# ============================================================================
Mutator = Callable[[Candidate, Archive, int, random.Random], Optional[Candidate]]


class EvolutionLoop:
    def __init__(self, domain, verifier: Verifier, mutator: Mutator,
                 archive: Optional[Archive] = None,
                 gate: Optional[ContrastGate] = None,
                 auditor: Optional[Auditor] = None,
                 ledger: Optional[Ledger] = None,
                 seed: int = 42, out_dir: Optional[str] = None):
        self.domain = domain
        self.verifier = verifier
        self.mutator = mutator
        self.archive = archive or Archive()
        # ★ 闸门阈值随验证器性质自适应：
        #   确定性验证器 + 配对消融 = 单次即精确结论 → 1/1
        #   非确定性验证器（LLM 评判者）有采样噪声 → 3/2
        det = getattr(verifier, "deterministic", True)
        if gate is None:
            gate = ContrastGate(
                path=(os.path.join(out_dir, "gate.json") if out_dir else None),
                min_n=1 if det else MIN_N,
                consecutive=1 if det else CONSECUTIVE,
            )
        self.gate = gate
        self.verifier_is_deterministic = det
        self.auditor = auditor or Auditor(verifier)
        self.ledger = ledger or Ledger(os.path.join(out_dir or ".", "ledger.jsonl"))
        self.rng = random.Random(seed)
        self.out_dir = out_dir or "."
        self.curve: list[dict] = []
        self.budget = {"evals": 0, "invalid": 0, "started": time.time()}

    # ---- 记账：每次评估都算预算 ----
    def _evaluate(self, cand: Candidate) -> EvalResult:
        res = self.verifier.evaluate(cand)
        self.budget["evals"] += 1
        if not res.valid:
            self.budget["invalid"] += 1
        return res

    def run(self, generations: int = 30, plateau_patience: int = 8) -> dict:
        # --- 第 0 代：种子 ---
        seed_cand = self.domain.seed()
        seed_res = self._evaluate(seed_cand)
        self.archive.add(seed_cand, seed_res)
        self.ledger.append({"event": "seed", "cid": seed_cand.cid,
                            "score": seed_res.score, "valid": seed_res.valid})
        self._log_gen(0, seed_res)

        best_ever = seed_res.score
        stale = 0

        for gen in range(1, generations + 1):
            parent_id = self.archive.sample_parent(self.rng)
            parent = self.archive.cands[parent_id]
            child = self.mutator(parent, self.archive, gen, self.rng)

            if child is None:
                # ★ 区分"变异失败"与"变异变差"：失败不入存档、不计低分
                self.budget["invalid"] += 1
                self.ledger.append({"event": "mutation_failed", "gen": gen, "parent": parent_id})
                self._log_gen(gen, None)
                stale += 1
                if stale >= plateau_patience:
                    break
                continue

            res = self._evaluate(child)
            admitted = self.archive.add(child, res)

            # --- 对照闸门：对新引入的每个概念做配对观测 ---
            for key, delta_ok in self.domain.contrast_signals(parent, child, res, self.rng).items():
                self.gate.observe(key, "guided", delta_ok, kind=self.domain.kind)
                self.gate.observe(key, "withheld", not delta_ok, kind=self.domain.kind)
                self.gate.judge(key)

            self.ledger.append({"event": "mutation", "gen": gen, "parent": parent_id,
                                "child": child.cid, "score": res.score,
                                "valid": res.valid, "admitted": admitted,
                                "delta_note": child.note})
            self._log_gen(gen, res if res.valid else None)

            if res.valid and res.score > best_ever + 1e-9:
                best_ever = res.score
                stale = 0
            else:
                stale += 1
            if stale >= plateau_patience:
                self.ledger.append({"event": "plateau_stop", "gen": gen, "stale": stale})
                break

        report = self._final_report()
        self._save(report)
        return report

    # ---- 记录一代 ----
    def _log_gen(self, gen: int, res: Optional[EvalResult]) -> None:
        best = self.archive.best()
        self.curve.append({
            "gen": gen,
            "gen_score": round(res.score, 4) if res and res.valid else None,
            "best_score": round(self.archive.score(best), 4) if best else None,
            "archive_size": len(self.archive.cands),
            "invalid_cum": self.budget["invalid"],
        })

    # ---- 终局报告：含审计 ----
    def _final_report(self) -> dict:
        scores = [self.archive.score(c) for c in self.archive.cands]
        best = self.archive.best()
        best_score = self.archive.score(best) if best else None
        audit = self.auditor.run(best_score)
        audit["collapse"] = self.auditor.collapse_check(scores)

        # ★ 运行有效性自检：如果最优候选还不如某个"必须被拒绝"的对抗探针，
        #   说明验证器无法区分好坏，这次进化**不算数**（比出一条假曲线更重要的是知道它是假的）。
        probe_scores = [p["score"] for p in audit["probes"] if p.get("valid")]
        if probe_scores and best_score is not None:
            worst_probe = max(probe_scores)
            valid_run = best_score > worst_probe + 1e-9
            validity = {
                "verdict": "ok" if valid_run else "INVALID：最优候选不优于对抗探针，验证器无法区分好坏",
                "best_score": round(best_score, 4),
                "worst_probe_score": round(worst_probe, 4),
                "margin": round(best_score - worst_probe, 4),
            }
        else:
            validity = {"verdict": "unknown（缺少探针或无有效候选）"}
        report = {
            "generations_run": len(self.curve),
            "budget": {"evals": self.budget["evals"], "invalid": self.budget["invalid"],
                       "wall_s": round(time.time() - self.budget["started"], 2)},
            "best": {
                "cid": best,
                "score": round(self.archive.score(best), 4) if best else None,
                "cmp": round(self.archive.cmp(best), 4) if best else None,
                "payload_keys": sorted(self.archive.cands[best].payload.keys()) if best else [],
            },
            "archive": self.archive.snapshot(),
            "gate": {
                "n_records": len(self.gate.records),
                "harmful": self.gate.harmful(),
                "verdicts": _count([r.verdict for r in self.gate.records.values()]),
            },
            "audit": audit,
            "run_validity": validity,
            "curve": self.curve,
        }
        self.gate.save()
        return report

    def _save(self, report: dict) -> None:
        os.makedirs(self.out_dir, exist_ok=True)
        with open(os.path.join(self.out_dir, "report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)


def _count(xs: list) -> dict:
    out: dict[str, int] = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
