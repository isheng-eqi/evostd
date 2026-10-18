"""进化域：判据库的选择（`clause_library`）

为什么需要这个域
----------------
在此之前，项目里的"自进化"其实是**两套互不相连的东西**：

  · `evo_*` 进化回路 —— 进化的是**合成适应度**（ontology / skill 模板），与标准无关
  · 判据库          —— 由标准编译 + 人工提案，**不参与搜索**

项目招牌是"标准驱动的自进化"，两半却没接上。本域把它们接起来：

    候选 = 一组判据（从一个机械生成的候选池里选）
    适应度 = 在**标注资产**上的辨别力 F1
    硬门 = **零误报**（R2）

候选池里**故意混入坏判据**（过宽 / 过窄），系统必须靠外部真值把它们淘汰掉——
而不是靠"看起来合理"。

与 `std_propose` 的分工
-----------------------
`std_propose` 是**单条**判据的准入裁决（人在环）；
本域是**一组**判据的搜索（无人环，靠 F1 + 硬门）。
两者用同一套外部真值，但一个做"准不准入"，一个做"选哪些"。
"""
from __future__ import annotations

import copy
import random
from typing import Optional

from evolve.core import Archive, Candidate, EvalResult
from standard.compiler import Clause, ClauseKind, compile_standard
from standard.grammar_v2 import ClauseKindV2


# ============================================================================
#  判据规格（候选的原子）
# ============================================================================
def _spec(cid, kind, text, params, family: str) -> dict:
    return {"clause_id": cid, "kind": kind, "text": text, "params": params,
            "family": family}


def build_candidate_pool(domain: str = "manufacturing") -> list[dict]:
    """★ 从一个领域的语料**机械生成**候选判据池，并**故意混入坏判据**。

    好判据：参数与语料原文一致 → 能抓到该抓的、不误伤合规的
    坏判据：参数被缩放（过宽 → 漏；过窄 → 误报）→ 必须被外部真值淘汰

    这样"选择"这件事才有内容：不是"要不要加判据"，而是
    **在看起来都合理的参数里，靠标注数据选出对的那个**。
    """
    from standard.table_extractor import extract_limit_tables
    from standard.table_extractor import KEY_FIELD_BY_DOMAIN
    pool: list[dict] = []
    # ★ 泛化：语料与 key_field 都按**领域**取，不再写死制造业。
    #   政务语料同样能抽出限额表（update_limit_days，公开级/内部级/秘密级）。
    if True:
        import importlib as _il
        _corp = _il.import_module(f"domains.{domain}.corpus")
        tables = extract_limit_tables(_corp.all_docs())
        for t in tables:
            if not t.table_key:
                continue
            kf = KEY_FIELD_BY_DOMAIN.get(domain, "equipment_class")
            # ── 好判据：原表 ──────────────────────────────
            pool.append(_spec(f"{t.field}-exact", ClauseKindV2.RANGE_BY_TABLE.value,
                              f"{t.provenance.get('name', t.field)}不得超过该设备类别的上限。",
                              {"field": t.field, "key_field": kf, "table_key": t.table_key},
                              family=t.field))
            # exact 也**自带语料原文表**
            #   否则它会输给自带表的 wide：verifier 里 ctx.update(tables) 把
            #   wide 的宽表塞进共享 ctx，**连 exact 也被喂了宽表** ——
            #   实测 exact 独测得 1.000，与 wide 同批时两者都只剩 0.667。
            pool[-1]["_table_override"] = {k: dict(v) for k, v in t.rows.items()}
            # ── 坏判据 A：**过宽**（上限×2）→ 漏掉真违规 ──
            wide = {k: {"max": int(v["max"] * 2)} for k, v in t.rows.items()}
            for k, v in t.rows.items():
                if "min" in v:
                    wide[k]["min"] = v["min"]
            pool.append(_spec(f"{t.field}-wide", ClauseKindV2.RANGE_BY_TABLE.value,
                              f"{t.provenance.get('name', t.field)}（宽）不得超过上限。",
                              {"field": t.field, "key_field": kf, "table_key": t.table_key},
                              family=t.field))
            pool[-1]["_table_override"] = wide
            # ── 坏判据 B：**过窄**（上限×0.5）→ 误伤合规 ──
            tight = {k: {"max": max(1, int(v["max"] * 0.5))} for k, v in t.rows.items()}
            pool.append(_spec(f"{t.field}-tight", ClauseKindV2.RANGE_BY_TABLE.value,
                              f"{t.provenance.get('name', t.field)}（严）不得超过上限。",
                              {"field": t.field, "key_field": kf, "table_key": t.table_key},
                              family=t.field))
            pool[-1]["_table_override"] = tight
    return pool


# ============================================================================
#  验证器：在标注资产上量辨别力；**零误报是硬门**
# ============================================================================
class ClauseLibraryVerifier:
    """★ 外部、确定性、可 held-out 的验证器。

    它不读候选的"理由"，只看**在标注数据上的表现**：
        F1 高 且 误报 == 0  → 好
        误报 > 0            → 直接判 0 分（R2 硬门）
    """
    name = "labeled_assets_f1"
    deterministic = True

    def __init__(self, assets: list[dict], base_clauses: list[Clause],
                 ctx: dict, domain: str = "manufacturing"):
        self.assets = assets
        self.base = base_clauses
        self.ctx = ctx
        self.domain = domain

    # ---- 把候选 payload 编译成可跑判据 ----
    def _compile(self, specs: list[dict]):
        clauses: list[Clause] = []
        tables: dict = {}
        for s in specs:
            cid = s["clause_id"]
            p = dict(s["params"])
            if s.get("_table_override"):
                tables[p["table_key"]] = s["_table_override"]
            clauses.append(Clause(cid, "", ClauseKind(s["kind"]), s["text"], p))
        allc = list(self.base) + clauses
        compiled = compile_standard(allc)
        ck = list(compiled.checkers)
        # 集合级判据要看到整批资产
        ctx = dict(self.ctx)
        ctx.update(tables)
        ctx["_assets"] = [{k: v for k, v in a.items() if not k.startswith("_")}
                          for a in self.assets]
        return ck, ctx

    def evaluate(self, cand: Candidate) -> EvalResult:
        specs = cand.payload.get("specs", [])
        try:
            ck, ctx = self._compile(specs)
        except Exception as e:
            return EvalResult(valid=False, metrics={"error": f"{type(e).__name__}: {e}"})

        tp = fp = fn = tn = 0
        for a in self.assets:
            lab = a.get("_label")
            clean = {k: v for k, v in a.items() if not k.startswith("_")}
            fired = False
            for c in ck:
                try:
                    f = c.check(clean, ctx)
                except Exception:
                    continue
                if f is not None and not f.ok:
                    # 只看**本候选新增的判据**是否触发（基础库是既定的）
                    if c.clause.clause_id in {s["clause_id"] for s in specs}:
                        fired = True
                        break
            if lab == "violating":
                tp += fired
                fn += (not fired)
            else:
                fp += fired
                tn += (not fired)

        prec = tp / (tp + fp) if (tp + fp) else 1.0
        rec = tp / (tp + fn) if (tp + fn) else 1.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        # ★ R2 硬门：只要有一条合规资产被误伤，这个候选就不合格
        score = 0.0 if fp > 0 else f1
        return EvalResult(valid=True, score=score,
                          metrics={"f1": round(f1, 4), "precision": round(prec, 4),
                                   "recall": round(rec, 4), "tp": tp, "fp": fp,
                                   "fn": fn, "tn": tn,
                                   "n_specs": len(specs),
                                   "gated_by_R2": fp > 0},
                          artifacts={"spec_ids": [s["clause_id"] for s in specs]})


# ============================================================================
#  进化域
# ============================================================================
class ClauseLibraryDomain:
    kind = "clause_library"

    def __init__(self, verifier: ClauseLibraryVerifier, seed: int = 11):
        self.v = verifier
        self.rng = random.Random(seed)
        self.pool = build_candidate_pool(verifier.domain)

    # ---- 起点：空集（让进化自己长出判据）----
    def seed(self) -> Candidate:
        return Candidate(cid="CL-000", kind=self.kind, payload={"specs": []},
                         origin="seed", note="空判据集")

    def proposal_pool(self) -> list[dict]:
        return self.pool

    def _by_id(self, cid: str) -> Optional[dict]:
        for s in self.pool:
            if s["clause_id"] == cid:
                return s
        return None

    def mutator(self, parent: Candidate, archive: Archive, gen: int,
                rng=None, **kw) -> Candidate:
        rng = rng or self.rng
        specs = copy.deepcopy(parent.payload.get("specs", []))
        have = {s["clause_id"] for s in specs}
        # 同一 family 内互斥：换掉一个比堆叠更接近真实决策
        op = rng.choice(["add", "add", "swap", "drop"])
        if op == "add":
            cands = [s for s in self.pool if s["clause_id"] not in have]
            if cands:
                specs.append(rng.choice(cands))
        elif op == "drop" and specs:
            specs.pop(rng.randrange(len(specs)))
        elif op == "swap" and specs:
            i = rng.randrange(len(specs))
            fam = specs[i].get("family")
            alts = [s for s in self.pool
                    if s.get("family") == fam and s["clause_id"] not in have]
            if alts:
                specs[i] = rng.choice(alts)
        cid = f"CL-{gen:03d}-{rng.randrange(10000):04d}"
        return Candidate(cid=cid, kind=self.kind, payload={"specs": specs},
                         parent=parent.cid, gen=gen,
                         note="+".join(s["clause_id"].split("-")[-1] for s in specs) or "空集")

    def contrast_signals(self, parent: Candidate, child: Candidate,
                         res: EvalResult, rng) -> dict:
        """对照信号：按"候选里出现的 family"归因，供闸门判断该保留哪些编辑。"""
        sig = {}
        pf = {s.get("family") for s in parent.payload.get("specs", [])}
        cf = {s.get("family") for s in child.payload.get("specs", [])}
        for fam in (cf - pf):
            sig[f"add:{fam}"] = res.score > 0
        for fam in (pf - cf):
            sig[f"drop:{fam}"] = res.score > 0
        return sig or {"noop": True}

    def make_verifiers(self):
        """★ 返回 (进化集验证器, 留出集验证器)。

        留出集用**不同的越界偏移**合成——不是把同一批样本切一半。
        理由：真实关心的是"这套判据在新样本上还成不成立"，
        而"切一半"只能测拟合，测不出泛化。
        """
        from domains.manufacturing.assets import ctx as actx
        from domains.manufacturing import standards
        from standard.compiler import compile_standard
        from standard.grammar_v2_experiment import extracted_ctx, v2_clauses
        from evolve.domains.clause_library import ClauseLibraryVerifier

        if getattr(self, "_verifiers", None):
            return self._verifiers
        std = standards()
        base = [ck.clause for ck in compile_standard(std).checkers]
        have = {b.clause_id for b in base}
        base += [c for c in v2_clauses(getattr(std, "STANDARD_NAME", ""), self.v.domain)
                 if c.clause_id not in have]
        ctx = dict(actx()); ctx.update(extracted_ctx()); ctx["now"] = "2026-10-13"
        ev = ClauseLibraryVerifier(synthesize_labeled_assets(self.v.domain, offset=1),
                                   base, ctx, self.v.domain)
        ho = ClauseLibraryVerifier(synthesize_labeled_assets(self.v.domain, offset=2),
                                   base, ctx, self.v.domain)
        self._verifiers = (ev, ho)
        return self._verifiers

    def probes(self) -> list[tuple[str, Candidate]]:
        """对抗探针：**必须被区分开**的三个基线。"""
        return [
            ("空集（什么都不判）", Candidate("probe-empty", self.kind, {"specs": []},
                                            origin="probe")),
            ("全选（好坏都要）", Candidate("probe-all", self.kind,
                                          {"specs": list(self.pool)}, origin="probe")),
            ("只选坏判据", Candidate("probe-bad", self.kind,
                                     {"specs": [s for s in self.pool
                                                if s["clause_id"].endswith(("-wide", "-tight"))]},
                                     origin="probe")),
        ]

def synthesize_labeled_assets(domain: str = "manufacturing",
                              offset: int = 1) -> list[dict]:
    """★ 从语料表**合成评测集**，专门造出能**区分候选参数**的样本。

    为什么必须自己造：实测发现"过宽"的坏判据（上限×2）在原始 20 条标注资产上
    与"正确"判据**表现完全一样**——因为没有一条违规值落在两者之间。
    **坏判据必须有"能被外部真值区分"的机会，否则它在数据上就是不可辨的。**

    为每一档生成三类样本（真值全部来自语料表本身，不是拍的）：
      · 合规：取该档上限   → 正确判据必须放行
      · 违规·紧邻越界：上限+1 → **只有"正确"和"过窄"能抓到，过宽会漏**
        这一条是区分 exact 与 wide 的关键
      · 违规·远越界：上限×3  → 所有判据都该抓到（过宽也抓得到）

    再加三类**边界合规样本**用于把"过窄"的判据筛掉：
      · 合规·恰好在紧判据的上限之下（但不能低于过窄判据之上）
        —— 由"过窄"判据误伤，正确判据放行
    """
    from standard.table_extractor import extract_limit_tables, KEY_FIELD_BY_DOMAIN
    out: list[dict] = []
    # ★ 泛化：语料与关键字段按**领域**取（此前写死制造业，
    #   非制造业返回空评测集 → 空集上 best_score 恒为 1.0 的**假满分**）。
    import importlib as _il
    _corp = _il.import_module(f"domains.{domain}.corpus")
    _assets = _il.import_module(f"domains.{domain}.assets")
    tables = extract_limit_tables(_corp.all_docs())
    _kf = KEY_FIELD_BY_DOMAIN.get(domain, "equipment_class")
    # ★ base 从该领域**一条真实合规资产**出发，而不是手写字段字典 ——
    #   手写必然带制造业字段，换领域就全错。
    _pool = _assets.all_assets()
    _ok = next((a for a in _pool if a.get("_label") == "compliant"), None)
    base = {k: v for k, v in (_ok or {}).items() if not k.startswith("_")} if _ok else {}
    base.setdefault("asset_id", "SYN-BASE-0001")
    base.update({
        "secrecy": "内部", "collect_time": "2026-03-01", "ingest_time": "2026-03-05",
        "archive_time": "2026-03-10", "install_date": "2019-04-11",
        "commission_date": "2019-05-06", "retire_date": None,
        "quality_score": 88.0, "storage_uri": "oss://mfg-assets/2026/equipment/x.json",
        "cited_standards": ["GB/T 33863.1-2017"], "source_doc_ids": ["DOC-MFG-001"],
        "owner_dept": "设备管理部", "maintainer": "王工",
        "equipment_no": "EQP-1000001", "equipment_status": "在用",
        "media_type": "图纸", "revision": 1, "check_date": "2026-03-02",
        "check_interval_hours": 24, "maintenance_cycle_days": 90,
    })
    n = 0
    for t in tables:
        if not t.table_key:
            continue
        for cls, row in t.rows.items():
            hi = row.get("max")
            if hi is None:
                continue
            # ★ offset 让留出集与进化集**不是同一批样本**：
            #   进化集用 hi+1 越界，留出集用 hi+offset 越界。
            #   两者都只有"正确参数"能同时通过——过宽的参数在 offset 小的时候会漏。
            for tag, val, label in (("ok", hi, "compliant"),
                                    ("near", hi + offset, "violating"),
                                    ("far", hi * 3, "violating")):
                n += 1
                a = dict(base)
                a[_kf] = cls
                a[t.field] = val
                a["asset_id"] = f"SYN-{t.field[:6]}-{cls}-{tag}-{n:03d}"
                if "catalog_id" in base:
                    a["catalog_id"] = a["asset_id"]
                a["_label"] = label
                a["_why"] = f"[合成] {t.field} 在 {cls} 档取 {val}（该档上限 {hi}）"
                out.append(a)
    return out

def build_ask_committee(assets: list[dict], now: str = "2026-10-13") -> list[dict]:
    """★ 为"主动提问"构造委员会：**跨越对同一件事的多种合理解释**。

    为什么不能只用表派生候选：实测那只能覆盖"查表上限"这一类盲区，
    对"未来日期""跨资产重复"完全无效 —— 于是系统问不出那两类。

    做法：对**资产里真实出现过的字段**，按值形态自动生成候选：
      · 值形如 ISO 日期  → 生成多个 `NOT_FUTURE`，基准时间取不同档
        （严格基准 vs 宽松基准 → 它们会在"未来日期"资产上分歧）
      · 值形如长十六进制串（疑似哈希/指纹）→ 生成 `UNIQUE_ACROSS`
      · 值形如受控短词表       → 生成 `ENUM`（词表取观测到的全集 vs 去尾的严格版）

    全部机械可复算；值形态判定不依赖任何领域知识。
    """
    import re as _re
    from collections import Counter
    out: list[dict] = []
    if not assets:
        return out

    iso = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
    hexish = _re.compile(r"^[0-9a-f]{16,}$")
    fields: dict[str, list] = {}
    for a in assets:
        for k, v in a.items():
            if k.startswith("_"):
                continue
            fields.setdefault(k, []).append(v)

    for f, vals in fields.items():
        nonnull = [v for v in vals if v not in (None, "", [])]
        if not nonnull:
            continue
        # ① 日期型 → 多个基准时间档（严格/宽松），它们在"未来日期"上必然分歧
        if all(isinstance(v, str) and iso.match(v) for v in nonnull):
            for tag, bound in (("strict", now), ("lenient", "2031-01-01")):
                out.append(_spec(
                    f"{f}-future-{tag}", ClauseKindV2.NOT_FUTURE.value,
                    f"{f} 不得晚于基准时间（{tag}）。",
                    {"field": f, "now_key": f"__now_{tag}"}, family=f"future:{f}"))
                out[-1]["_now_override"] = {f"__now_{tag}": bound}
            continue
        # ② 长十六进制串 → 唯一性候选（含"不约束"的对空档，保证会分歧）
        if all(isinstance(v, str) and hexish.match(v) for v in nonnull):
            out.append(_spec(f"{f}-unique", ClauseKindV2.UNIQUE_ACROSS.value,
                             f"同一份文件不得跨资产重复登记（{f}）。",
                             {"field": f, "id_field": "asset_id", "ignore_empty": True},
                             family=f"unique:{f}"))
            continue
        # ③ 短词表（取值种类少且每类重复出现）→ 严格词表 vs 观测全量词表
        uniq = sorted({str(v) for v in nonnull})
        if 2 <= len(uniq) <= 8 and len(nonnull) >= 4 and all(len(u) <= 8 for u in uniq):
            cnt = Counter(str(v) for v in vals)
            rare = {u for u in uniq if cnt[u] <= 1}       # 只出现一次的取值 → 可疑
            if rare and len(uniq) - len(rare) >= 2:
                strict = [u for u in uniq if u not in rare]
                out.append(_spec(f"{f}-enum-strict", ClauseKind.ENUM.value,
                                 f"{f} 应属于受控词表（严格）。",
                                 {"field": f, "allowed": strict}, family=f"enum:{f}"))
                out.append(_spec(f"{f}-enum-full", ClauseKind.ENUM.value,
                                 f"{f} 应属于受控词表（全量）。",
                                 {"field": f, "allowed": uniq}, family=f"enum:{f}"))
    return out
