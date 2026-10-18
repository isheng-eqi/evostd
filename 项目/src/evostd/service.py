"""服务层：std_* / evo_* / asset_* 三组能力的纯 Python 实现。

★ 这一层**不依赖 MCP、不依赖 Nexent**——MCP Server 只是它的一层薄适配。
   这样做的理由：核心逻辑必须能被离线测试，平台只是"把调用送进来"的管道。

★★ 安全设计（docs/32）：本层是 agent"够不着的地方"。
   agent 只能调用这里的函数，改不了判据库、锚点、台账。
"""
from __future__ import annotations

import json
import os
import random
import time
from typing import Any, Callable, Optional

from standard.compiler import (
    Clause, ClauseKind, Finding, StandardVerifier, _CHECKER_BY_KIND,
    compile_standard,
)

from .anchors import HardAnchor, coverage, derive_hard_anchors, verify_anchors
from .store import Store, clause_from_dict, clause_to_dict
import sys
from evostd.degrade import note as _note_degrade, SEVERITY_JUDGMENT

# ★ 当前领域包的语料模块。此前 `std_amendments` 与 `_corpus_contradiction`
#   两处**写死制造业语料** —— 政务包里修订建议与退役判据都依据错误的语料。
_CORPUS_MOD = None


def set_corpus_mod(mod) -> None:
    """由 build_service 注入当前领域包的 corpus 模块。"""
    global _CORPUS_MOD
    _CORPUS_MOD = mod


def _corpus_docs():
    """按当前领域取语料；取不到返回空列表（调用方据此弃权，而不是回落到别的领域）。"""
    if _CORPUS_MOD is None:
        return []
    try:
        return _CORPUS_MOD.all_docs()
    except Exception:
        return []
from evostd.degrade import snapshot as _dg_snapshot



def _aid_of(asset: dict, i: int = -1) -> str:
    """取资产的标识，用于溯源展示。

    ★ 不能只认 `asset_id`：制造业用它，政务用 `catalog_id`。
      写死会让换领域后所有结论都显示成 `#0`、`#1`，**溯源能力静默退化**。
      依次尝试常见标识字段，都没有才回落到序号。
    """
    for k in ("asset_id", "catalog_id", "asset_no", "id"):
        v = asset.get(k)
        if v:
            return str(v)
    return f"#{i}" if i >= 0 else "?"


class _ShimAnchor:
    """把 anchors_v2.HardAnchorV2 适配成原抽取器的返回形状（只需 .to_dict()）。

    集合级锚点可能带多条资产，这里取**被归因的那一条**作为该锚点的代表资产，
    并在 dict 里保留 `assets` 与 `level`，不丢信息。
    """
    def __init__(self, a):
        self._a = a

    def to_dict(self):
        a = self._a
        rep = a.asset
        if a.attributed_to:
            for x in a.assets:
                if x.get("asset_id") == a.attributed_to:
                    rep = x
                    break
        return {"anchor_id": a.anchor_id, "asset": rep, "label": a.label,
                "source_clause": a.source_clause, "how": a.how, "kind": "hard",
                "level": getattr(a, "level", "point"),
                "assets": a.assets, "attributed_to": a.attributed_to}



def _check_not_looser(clause) -> Optional[str]:
    """★ R4：判据的表不得比**语料原文**的上限更松。

    返回 None 表示通过；返回字符串表示驳回理由。

    为什么需要：R1/R2/R3 只判"有没有贡献、有没有误报"，
    **判不出"这条比标准原文松"**。实测：退役 -exact（7/30/90）后，
    搜索补回来的是 -wide（14/60/180）—— 缺口补上了但**判得更松**。
    """
    params = getattr(clause, "params", {}) or {}
    tk = params.get("table_key")
    if not tk:
        return None                      # 非查表类，R4 不适用
    cand = params.get("_table_override") or None
    if cand is None:
        # 候选没带覆盖表 → 它用的就是语料原文的表，不可能更松
        return None
    try:
        import importlib as _il
        from standard.table_extractor import extract_limit_tables
        # 找一张同名的语料原文表
        src = None
        for dom in ("manufacturing", "gov_catalog"):
            try:
                _c = _il.import_module(f"domains.{dom}.corpus")
            except Exception:
                continue
            for t in extract_limit_tables(_c.all_docs()):
                if t.table_key == tk:
                    src = t
                    break
            if src:
                break
        if src is None:
            return None                  # 找不到原文，弃权（不冤判）
        looser = []
        for k, v in cand.items():
            row = src.rows.get(k)
            if not row:
                continue
            cm, sm = v.get("max"), row.get("max")
            if cm is not None and sm is not None and cm > sm:
                looser.append(f"{k}: 候选上限 {cm} > 语料原文 {sm}")
        if looser:
            return ("R4 未满足：候选比**语料原文更松** → " + "；".join(looser[:3])
                    + "。补判据时不允许放宽原文的约束。")
    except Exception:
        return None
    return None

# ★ 弃权结果的缓存：键 = (state 根, 判据数, 条款数)。
#   弃权只取决于**判据库本身**，与传入哪些资产无关，所以可以跨调用复用。
#   库一变键就变，自动失效 —— 不需要手动清。
_ABSTAIN_CACHE: dict = {}


class StandardService:
    """标准与裁决服务：本项目里 agent **够不着**的那一层。"""

    def __init__(self, store: Store, clauses: list[Clause], base_asset: dict,
                 ctx: dict, standard_name: str = "",
                 extra: list[Clause] | None = None,
                 labeled: list[dict] | None = None):
        self.store = store
        self.base_asset = base_asset
        self.ctx = ctx
        self.standard_name = standard_name
        # 播种：把标准编译成种子判据库；扩张条款走准入裁决
        self._seed(clauses, extra=extra, labeled=labeled)



    # ---------------------------------------------------------------- 主动提问
    def std_ask(self, assets: list[dict], top_k: int = 5, domain: str = "manufacturing") -> dict:
        """★ 主动提问：**该让人确认哪几条**（机械排序，不靠模型）。

        为什么需要：人的确认是整个系统里**最贵的真值来源**。
        此前的流程是"人自己发现盲区 → 标注 → 系统长出判据"——**由人驱动，系统被动**。
        本方法把它反过来：**系统算出标哪一条信息量最大，再请人确认那一条。**

        机制：**委员会分歧（query-by-committee）**，全程机械可复算。
          委员会 = 同一张语料表派生出的多个候选判据（正确 / 过宽 / 过窄）
          一条资产上委员会**分歧越大** → 它越靠近决策边界 → 标它越能定分止争
          分歧为 0（全体放行或全体判违规）→ 标它几乎不带来新信息

        输出里每条都带 `why`：**是哪几条判据在它上面吵起来了**，人不必盲信排序。

        ★ 系统只**提问**，不替人回答 —— 与"最终裁决者永远在系统之外"一致。
        """
        try:
            from evolve.domains.clause_library import build_candidate_pool, build_ask_committee
            pool = build_candidate_pool(domain)
            # ★ 追加"跨解释"委员会：覆盖未来日期 / 跨资产重复 / 词表边界三类，
            #   否则系统只能问出"查表上限"那一类盲区（实测只覆盖 4 条里的 2 条）。
            _now = self.ctx.get("now") or "2026-10-13"
            pool = list(pool) + build_ask_committee([a for a in assets if not a.get("_label")], _now)
        except Exception as e:
            return {"ok": False, "error": f"候选委员会不可用：{type(e).__name__}: {e}"}
        if not pool:
            return {"ok": True, "n_asked": 0, "questions": [],
                    "note": "该领域没有可机械派生的候选委员会，无法计算信息量"}
        from standard.grammar_v2 import make_checker
        from standard.compiler import Clause, ClauseKind

        committee = []
        for spec in pool:
            try:
                committee.append((spec["clause_id"],
                                  make_checker(Clause(spec["clause_id"], "", ClauseKind(spec["kind"]),
                                                      spec["text"], spec["params"]))))
            except Exception:
                continue
        if not committee:
            return {"ok": True, "n_asked": 0, "questions": [], "note": "委员会构造失败"}

        ctx = self._ctx_with_assets(assets)
        questions = []
        for i, a in enumerate(assets):
            if a.get("_label"):                     # 已标注的不再问
                continue
            clean = {k: v for k, v in a.items() if not k.startswith("_")}
            votes, detail = 0, []
            for cid, ck in committee:
                try:
                    f = ck.check(clean, ctx)
                except Exception:
                    continue
                v = 0 if (f is None or f.ok) else 1
                votes += v
                if v:
                    detail.append(cid)
            n = len(committee)
            # ① 委员会**内部分歧**：判违规的比例离 0.5 越近，越靠近决策边界
            rate = votes / n if n else 0.0
            internal = 1.0 - abs(rate - 0.5) * 2.0
            if votes == 0 or votes == n:
                internal = 0.0                              # 委员会全体一致 → 边界信息为零
            # ② 委员会 vs **现有判据库**的分歧：库里沉默、委员会却判违规 → 这就是盲区本身
            #    ★ 第一版只看 ①，于是"库完全沉默而委员会全体判违规"被算成分歧 0 ——
            #      而那恰恰是最该问的一条。这是度量设计错了，不是数据的问题。
            lib_fires = False
            for ck in self._lib_checkers():
                try:
                    f = ck.check(clean, ctx)
                except Exception:
                    _note_degrade("service.checker_skipped",
                                  RuntimeError("判据在 check 时抛异常（参数/字段类型不符等）"),
                                  fallback="跳过该判据 —— 这条资产上它**没判**",
                                  severity=SEVERITY_JUDGMENT, store=self.store)
                    continue
                if f is not None and not f.ok:
                    lib_fires = True
                    break
            committee_says_bad = (rate >= 0.5)
            versus_lib = 1.0 if (committee_says_bad and not lib_fires) else 0.0
            # 综合：与库的分歧优先（那是真盲区），内部边界分歧次之
            informativeness = max(versus_lib, internal * 0.6)
            why = []
            if versus_lib:
                why.append("★ 现有判据库对这条**完全沉默**，而委员会多数判其违规 —— 疑似盲区")
            if internal > 0:
                why.append("委员会内部 {}:{} 分裂，处在判据参数的分界上".format(votes, n - votes))
            if not why:
                why.append("委员会一致" + ("放行" if votes == 0 else "判违规")
                           + "且与现有库一致，标注信息量低")
            questions.append({
                "index": i, "asset_id": _aid_of(a, i),
                "informativeness": round(informativeness, 4),
                "internal_disagreement": round(internal, 4),
                "versus_library": round(versus_lib, 4),
                "library_fires": lib_fires,
                "votes_for_violation": votes, "committee_size": n,
                "split": f"{votes}:{n - votes}",
                "why": "；".join(why),
                "disagreeing_clauses": detail[:6],
            })
        questions.sort(key=lambda q: -q["informativeness"])
        top = [q for q in questions if q["informativeness"] > 0][:max(1, top_k)]
        return {
            "ok": True,
            "n_unlabeled": len(questions),
            "n_informative": sum(1 for q in questions if q["informativeness"] > 0),
            "n_asked": len(top),
            "questions": top,
            "note": ("按委员会分歧度排序：越接近决策边界的越该先问。"
                     "系统只提问，不替人回答——标不标、标成什么，由人决定。"),
        }


    def _sync_anchors(self, clauses, extra_clauses=None) -> int:
        """★ 在**完整条款集**（领域包条款 + 扩张判据）上重推硬锚点并落库。

        为什么必须是一个共用方法：此前"冷启动"和"带状态重启"各自写了一份锚点推导，
        而重启那一份被 `_seed` 的早退 `return` 整个跳过 —— 实测硬锚点 103 → 87、
        冲突 1 → 2，**而判定仍正确、自检仍报 0 不一致**。
        **两条路径做同一件事，就必须只写一份代码。**

        返回最终落库的锚点条数。
        """
        extra_clauses = list(extra_clauses or [])
        keep = {c.clause_id for c in extra_clauses}
        full = [c for c in list(clauses) + extra_clauses if c.compilable]

        all_ha = None
        try:
            from standard.anchors_v2 import derive_hard_anchors_v2
            all_ha = derive_hard_anchors_v2(full, self.base_asset, self.ctx)
        except Exception as e:
            _note_degrade("service._sync_anchors.v2", e, fallback="退回 v1 锚点抽取器",
                          severity=SEVERITY_JUDGMENT, store=self.store)
            print(f"[evostd] ★ 锚点抽取器 v2 不可用，退回 v1：{e}", file=sys.stderr)
            all_ha = derive_hard_anchors([c for c in full], self.base_asset, self.ctx)

        # 锚点集整体重建：不重建的话，上一次遗留的旧锚点会与新的混在一起，
        # 让"锚点不一致"这种问题换个形式再出现。
        self.store.reset_anchors_from_standard()
        base_d, extra_d = [], []
        for i, h in enumerate(all_ha, start=1):
            # ★ HardAnchorV2 是裸 dataclass，没有 to_dict()；要用 _ShimAnchor 包一层
            #   （它把集合级锚点折算成代表资产）。这个 AttributeError 曾被 except 吞掉。
            try:
                d = _ShimAnchor(h).to_dict()
            except Exception:
                continue
            if h.source_clause in keep:
                d["anchor_id"] = f"HA2-9{len(extra_d) + 1:02d}"      # 900 段，与基础锚点不撞号
                extra_d.append(d)
            else:
                d["anchor_id"] = f"HA2-{len(base_d) + 1:03d}"
                base_d.append(d)
        self.store.add_anchors(base_d + extra_d)
        self.store.log({"event": "sync_anchors", "n_base": len(base_d),
                        "n_extra": len(extra_d), "n_total": len(base_d) + len(extra_d)})
        return len(base_d) + len(extra_d)

    # ---------------------------------------------------------------- 准入裁决
    def _admit_clause(self, clause, compliant: list[dict], violating: list[dict],
                      base_checkers: list) -> dict:
        """★ R1/R2/R3 准入裁决，**对任意条款可复用**（`std_propose` 与播种共用同一把尺子）。

        背景：此前 `build_service` 把判据语言扩张产生的条款直接 `+` 进库里并标 adopted，
        **完全绕过了 R1/R2/R3** —— 系统在违反自己写下的准入规则，而且不报错。
        本方法把裁决抽出来，让"人工提案"和"系统自动装载"走**同一条路径**。

        外部真值用**签名样本**（合规/违规各一批），而不是候选条款自己生成的锚点——
        用自己生成的锚点判定自己是循环论证。
        """
        from standard.grammar_v2 import make_checker
        try:
            ck = make_checker(clause)
        except Exception as e:
            return {"verdict": "rejected", "reason": f"判据无法构造：{type(e).__name__}: {e}",
                    "stats": {}}

        ctx_c = self._ctx_with_assets(compliant + violating)
        false_alarm, caught, already = [], [], []
        for a in compliant:
            try:
                if not ck.check(a, ctx_c).ok:
                    false_alarm.append(_aid_of(a))
            except Exception as e:
                return {"verdict": "rejected",
                        "reason": f"执行异常（{type(e).__name__}: {e}）", "stats": {}}
        for a in violating:
            try:
                fires = not ck.check(a, ctx_c).ok
            except Exception:
                continue
            if not fires:
                continue
            if any(not c.check(a, ctx_c).ok for c in base_checkers):
                already.append(_aid_of(a))          # 基础库已能抓到 → 不算贡献
            else:
                caught.append(_aid_of(a))

        stats = {"n_compliant": len(compliant), "n_violating": len(violating),
                 "attributable_caught": len(caught), "false_alarm": len(false_alarm),
                 "explained_by_existing": len(already),
                 "caught_ids": caught[:10], "false_alarm_ids": false_alarm[:10]}
        if not compliant:
            return {"verdict": "rejected",
                    "reason": "R1 未满足：从未见过任何合规样本，不允许采纳判据", "stats": stats}
        if false_alarm:
            return {"verdict": "rejected",
                    "reason": f"R2 未满足：在合规样本 {false_alarm[:3]} 上误报", "stats": stats}
        if not caught:
            return {"verdict": "rejected",
                    "reason": ("R3 未满足：没有任何**可归因**命中"
                               "（要么抓不到违规样本，要么现有判据已经能抓到）"), "stats": stats}
        # ── ★ R4：严格度不劣化 ───────────────────────────────
        # 实测暴露：政务包里退役了 -exact 的判据（公开7/内部30/秘密90）后，
        # 系统补回来的却是 **-wide**（上限×2：14/60/180）——
        # **缺口补上了，但补得比原来松**，90~180 之间的违规会漏。
        #
        # R1/R2/R3 只问"有没有可归因命中、有没有误报"，
        # **不问"这条比语料原文松不松"**。这一条补上。
        #
        # 判据：查表区间类候选，其表**不得比语料原文的上限更松**。
        r4 = _check_not_looser(clause)
        if r4 is not None:
            return {"verdict": "rejected", "reason": r4, "stats": stats}

        return {"verdict": "adopted",
                "reason": f"R1/R2/R3/R4 全部满足：可归因命中 {len(caught)} 条，零误报，且不劣于语料",
                "stats": stats}

    # ---------------------------------------------------------------- 播种
    def _seed(self, clauses: list[Clause], extra: list[Clause] | None = None,
              labeled: list[dict] | None = None) -> None:
        """播种种子判据库；**库非空时也要校验它与当前领域包是否一致**。

        ★ 这里修的是一个**静默失效**：原实现只写 `if self.store.clauses: return`，
          不检查库里的判据是不是当前领域包的。后果是——换领域包（或某次启动时
          领域包 import 失败走了回落）之后，服务会**继续沿用旧的判据库**，
          而且健康检查正常、数字照常好看。实测踩到过两次：
          制造包启动成功，但 `asset_lineage` 报 `n_clauses: 12`
          （12 正是演示样例回落后的可编译数，制造包应是 34）。

        对策：以**领域包为准**重新播种"由标准编译而来"的判据，
        但**保留**人工确认/进化产出的判据（origin != "standard"）——那些是学习成果，不能丢。
        """
        want = [c for c in clauses if c.compilable]
        want_ids = {c.clause_id for c in want}

        if self.store.clauses:
            # ★★ 必须从 **store 记录** 取 origin：`all_clauses()` 返回的是重建的
            #    Clause 对象，**不带 origin**（实测 41 条全是 <无>）。
            #    用 getattr(c, "origin", "") 会得到空集 → "库与领域包一致"永远不成立
            #    → 每次重启都走重播分支。
            lib = self.store.all_clauses()
            have_ids = {r.spec.get("clause_id")
                        for r in self.store.clauses.values()
                        if getattr(r, "origin", "") == "standard"
                        and getattr(r, "status", "") == "adopted"}
            have_ids.discard(None)
            if have_ids == want_ids:
                # ★★ 一致 ≠ 什么都不用做！
                #    锚点必须按**完整条款集**重推（基础条款 + 已采纳的扩张判据）。
                #    这段曾经直接 `return`，于是：
                #      · 扩张判据的锚点从不重建 → 硬锚点 103 → 87
                #      · 基础锚点也没在完整集上重推 → 冲突消解看不到第45/46条 → 冲突 1 → 2
                #    而**判定仍然正确、自检仍报 0 不一致、所有报告数字看起来正常** ——
                #    第 10 次"看起来正常"。只有"带状态重启"才会暴露，
                #    而我此前每次验证前都习惯性清空状态目录，所以 20 多轮没发现。
                adopted_extra = [c for c in self.store.all_clauses()
                                 if getattr(c, "origin", "") != "standard" and c.compilable]
                n = self._sync_anchors(clauses, adopted_extra)
                print(f"[evostd] 库与领域包一致：跳过重播，仅重推锚点"
                      f"（领域包条款 + {len(adopted_extra)} 条已采纳扩张判据 → {n} 条锚点）",
                      file=sys.stderr)
                self.store.save()
                return
            missing = sorted(want_ids - have_ids)
            # ★★ 这里曾写作 `extra = sorted(have_ids - want_ids)` ——
            #    把 `_seed(clauses, extra=...)` 的**参数 extra 覆盖成了字符串列表**，
            #    于是紧随其后的准入块 `if extra:` 拿到的是 id 字符串而不是条款对象，
            #    整个准入与扩张锚点推导被静默跳过 → 硬锚点 103 → 87。
            #    **参数名不能被局部变量复用。**
            extra_ids = sorted(have_ids - want_ids)
            self.store.log({"event": "library_mismatch", "level": "WARN",
                            "domain_standard": self.standard_name,
                            "stored_standard_clauses": len(have_ids),
                            "pack_compilable_clauses": len(want_ids),
                            "missing": missing[:10], "extra": extra_ids[:10],
                            "action": "以领域包为准重播标准判据，保留非标准来源的判据"})
            print(f"[evostd] ★ 判据库与领域包不一致：库内标准判据 {len(have_ids)} 条 / "
                  f"领域包可编译 {len(want_ids)} 条 → 以领域包为准重播"
                  f"（缺 {len(missing)} 条，多 {len(extra_ids)} 条）", flush=True)
            # 清掉旧的标准判据（保留学习成果），再播当前领域包的
            self.store.drop_clauses_by_origin("standard")

        compiled = compile_standard(clauses)
        for c in clauses:
            # ★ 不可编译的条款**不能**作为判据入库——否则它检查的字段永远不存在，
            #   会把每一条资产都判成违规（冒烟测试第一次运行就抓到了这个 bug）。
            #   它们以 uncompilable 状态留档，可见、可审计、但不参与裁决。
            self.store.add_clause(
                c, status="adopted" if c.compilable else "uncompilable",
                reason=("由标准编译而来（种子判据）" if c.compilable
                        else "不可编译：只能靠人判断，留待软确认或判据语言扩张"),
                origin="standard")
        # 锚点统一由 _sync_anchors 在**完整条款集**上推导（见本方法末尾）

        # ★★ 扩张条款（判据语言产生的新判据）**必须过准入裁决**，不能直接 adopted。
        #    此前 `build_service` 把它们直接 `+` 进库并标 adopted，**完全绕过 R1/R2/R3** ——
        #    系统在违反自己写下的准入规则，而且不报错。
        #    外部真值用领域包提供的**标注样本**；用候选条款自己生成的锚点判定自己是循环论证。
        admitted, rejected = [], []
        n_hard = 0
        if extra:
            labeled = labeled or []
            comp = [{k: v for k, v in a.items() if not k.startswith("_")}
                    for a in labeled if a.get("_label") == "compliant"]
            viol = [{k: v for k, v in a.items() if not k.startswith("_")}
                    for a in labeled if a.get("_label") == "violating"]
            base_ck = list(compiled.checkers)
            for c in extra:
                if not c.compilable:
                    self.store.add_clause(c, status="uncompilable",
                                          reason="不可编译：留待人工判断",
                                          origin="grammar_v2")
                    continue
                j = self._admit_clause(c, comp, viol, base_ck)
                if j["verdict"] == "adopted":
                    self.store.add_clause(c, status="adopted", evidence=j["stats"],
                                          reason=f"【准入通过】{j['reason']}",
                                          origin="grammar_v2")
                    admitted.append(c.clause_id)
                else:
                    self.store.add_clause(c, status="rejected", evidence=j["stats"],
                                          reason=f"【准入驳回】{j['reason']}",
                                          origin="grammar_v2")
                    rejected.append({"clause_id": c.clause_id, "reason": j["reason"]})
            self.store.log({"event": "admission", "n_extra": len(extra),
                            "admitted": admitted, "rejected": rejected})
            print(f"[evostd] 扩张判据准入：提交 {len(extra)} 条 → "
                  f"采纳 {len(admitted)} / 驳回 {len(rejected)}"
                  + (f"（{admitted}）" if admitted else ""), file=sys.stderr)
            for r in rejected:
                print(f"[evostd]   ★ 驳回 {r['clause_id']}：{r['reason'][:60]}", file=sys.stderr)
            # ★ 通过的扩张判据也要产出机械锚点 —— 否则"有判据、没锚点"，
            #   按 R1 它们下一次就没有入库资格（自相矛盾）。
            #   ★★ 冷启动与带状态重启**共用同一条锚点推导路径**（_sync_anchors）——
            #      此前是两份各写一遍的代码，重启那条被 `return` 跳过了。
            n_hard = self._sync_anchors(
                clauses, [c for c in extra
                          if c.compilable and c.clause_id in set(admitted)])

        if not n_hard:
            # 没有任何扩张判据被采纳时，也要按领域包条款推一次锚点
            n_hard = self._sync_anchors(clauses, [])
        self.store.save()
        self.store.log({"event": "seed", "n_clauses": len(clauses),
                        "n_compiled": len(compiled.checkers),
                        "n_hard_anchors": n_hard,
                        "uncompilable": [c.clause_id for c in compiled.uncompilable]})

    # ---------------------------------------------------------------- 内部
    def _library(self) -> list[Clause]:
        return self.store.adopted()

    def _lib_checkers(self) -> list:
        """★ 只能通过 compile_standard 构造判据——它会正确排除不可编译条款。"""
        return compile_standard(self._library()).checkers


    def std_conflicts(self, limit: int = 20) -> dict:
        """★ 判据冲突检测：机械发现**判据库内部的矛盾**。

        判据库不是从天上掉下来的，它是从标准文本编译来的。标准文本自身可能不自洽
        （实测踩到：第5条「检修周期 1~365 天」与第45条「A类 ≤ 90 天」在
        `A类 + 365天` 这一点上直接冲突）。

        检测方法：对每条硬锚点，跑**全部**判据。
          · 锚点对**它自己来源的那条**条款是合规的，
            却对**另一条**条款违例  →  这是一处冲突。
        两个结论都是机械推导出来的，所以冲突一旦存在就一定暴露。

        冲突**不由系统自行裁决**——它交给人在 `std_confirm` 里定夺，
        因为"哪条标准说话算数"是领域权威问题，不是算法问题。
        """
        clauses = {c.clause_id: c for c in self.store.all_clauses()}
        checkers = self._lib_checkers()
        anchors = [a for a in self.store.anchors if a.get("label") == "compliant"]
        out = []
        for a in anchors:
            own = a.get("source_clause")
            asset = a.get("asset") or {}
            cctx = self._ctx_with_assets([asset])
            others = []
            for ck in checkers:
                cid = ck.clause.clause_id
                if cid == own:
                    continue
                try:
                    f = ck.check(asset, cctx)
                except Exception:
                    continue
                if f is not None and not f.ok:
                    others.append({"clause_id": cid, "message": f.message[:90],
                                   "kind": getattr(ck.clause.kind, "value", str(ck.clause.kind))})
            if others:
                # ★ 必须区分两类冲突，否则会把"锚点不完整"说成"标准自相矛盾"。
                #   类A 真冲突：对方条款是**独立约束**（数值范围/查表区间/受控词表…），
                #              两条条款对同一取值给出相反结论 → 标准自身不自洽。
                #   类B 假冲突：对方条款是**条件必填/条件依赖**，而本锚点只改了
                #              一个字段、没同步满足该条件 → 是锚点不完整，不是标准矛盾。
                COND = {"条件必填", "条件依赖"}
                real = [o for o in others if o["kind"] not in COND]
                artifact = [o for o in others if o["kind"] in COND]
                out.append({"anchor": a.get("anchor_id"), "own_clause": own,
                            "own_how": a.get("how", ""),
                            "asset_keys": {k: asset.get(k) for k in
                                           ("maintenance_cycle_days", "check_interval_hours",
                                            "equipment_class", "file_hash", "equipment_status",
                                            "media_type", "secrecy")
                                           if k in asset},
                            "class": "A_标准自相矛盾" if real else "B_锚点不完整",
                            "real_conflicts": real, "anchor_artifacts": artifact,
                            "conflicts_with": others})
        real_n = sum(1 for c in out if c["class"].startswith("A"))
        return {"ok": True, "n_compliant_anchors": len(anchors),
                "n_conflicts": len(out),
                "n_real_conflicts": real_n,
                "n_anchor_artifacts": len(out) - real_n,
                "conflicts": out[:limit],
                "note": ("类A（标准自相矛盾）需人工定夺哪条说话算数；"
                         "类B（锚点不完整）是锚点生成的问题，应由抽取器补齐依赖字段。"
                         if out else "未发现判据冲突")}


    def std_amendments(self) -> dict:
        """★ 把"判据冲突"变成**可执行的标准修订建议**（带语料证据）。

        判据冲突只报"谁跟谁冲突"，但不告诉你**该改哪个字**。
        而对"编译期常量 vs 语料查表"这一类冲突，修订方向是**机械可推的**：

            第7条  点检间隔 1~720 小时          ← 编译期常量（人写进标准的）
            第46条 A类≤24 / B类≤72 / C类≤168    ← 从语料 DOC-MFG-002 机械抽出

          两条对同一字段给上限 → 常量上限必须 ≤ 表中最大值，否则**常量那条永远不可达**。
          修订建议：把第7条的上限 720 改成 168（= 表中最大值）。

        ★ 建议**只生成、不落库**：改标准是权威行为，不是算法行为。
          系统能做的是把"改哪个字、改成多少、依据是哪句语料原文"摆到人面前。
        """
        conflicts = self.std_conflicts(limit=50).get("conflicts", [])
        clauses = {c.clause_id: c for c in self.store.all_clauses()}
        # 语料溯源：查表条款的表是从哪句话抽出来的
        prov = {}
        try:
            # ★ 按**当前领域包**取语料（此前写死制造业：政务包里
            #   修订建议与退役判据都依据错误的语料）
            from standard.table_extractor import extract_limit_tables, provenance_report
            _docs = _corpus_docs()
            if not _docs:
                return {"ok": True, "n_amendments": 0, "amendments": [],
                        "note": "当前领域包未提供语料，无法给出语料证据（弃权，不回落别的领域）"}
            for r in provenance_report(extract_limit_tables(_docs)):
                if r.get("status") == "ok":
                    prov[r["table"]] = r
        except Exception:
            pass

        out = []
        for cf in conflicts:
            own = clauses.get(cf["own_clause"])
            for other in cf.get("real_conflicts") or []:
                oc = clauses.get(other["clause_id"])
                if own is None or oc is None:
                    continue
                op, oq = getattr(own, "params", {}) or {}, getattr(oc, "params", {}) or {}
                # 只处理"同一字段、一方常量区间、一方查表区间"这一类
                if not (op.get("field") and op["field"] == oq.get("field")):
                    continue
                kinds = {getattr(own.kind, "value", str(own.kind)),
                         getattr(oc.kind, "value", str(oc.kind))}
                if kinds != {"数值范围", "查表区间"}:
                    continue
                const_c = own if getattr(own.kind, "value", "") == "数值范围" else oc
                tbl_c = oc if const_c is own else own
                cp = getattr(const_c, "params", {}) or {}
                tp = getattr(tbl_c, "params", {}) or {}
                table = self.ctx.get(tp.get("table_key")) or {}
                vals = [r.get("max") for r in table.values() if isinstance(r, dict) and r.get("max") is not None]
                if not vals:
                    continue
                tmax = max(vals)
                old_max = cp.get("max")
                if old_max is None or old_max <= tmax:
                    continue                        # 常量并不比表宽，不构成这类冲突
                p2 = prov.get(tp.get("table_key")) or {}
                out.append({
                    "field": op["field"],
                    "constant_clause": {"clause_id": const_c.clause_id, "text": const_c.text,
                                        "max": old_max},
                    "table_clause": {"clause_id": tbl_c.clause_id, "text": tbl_c.text,
                                     "table_key": tp.get("table_key"), "rows": table},
                    "evidence": {"doc_id": p2.get("doc_id"), "span": p2.get("span"),
                                 "raw": p2.get("raw")},
                    "proposed_amendment": {
                        "action": "收紧常量上限，使之不宽于语料表",
                        "clause_id": const_c.clause_id,
                        "before": {"max": old_max}, "after": {"max": tmax},
                        "new_text": const_c.text.replace(str(old_max), str(tmax)),
                    },
                    "why": (f"常量上限 {old_max} 超过语料表的最大值 {tmax}，"
                            f"导致 1~{old_max} 这一段取值在**任何设备类别下都不可能合规**，"
                            f"即标准自身给出了一个不可达区间。"),
                    "status": "需人工批准（系统只建议、不落库）",
                })
        # ★★ 第二条证据路径：**误伤**（人工确认合规、却被判违规）证明判据**过严**。
        #    它与"冲突"完全独立：冲突是条款间互斥，误伤是条款与**人的判断**互斥。
        #    两条路径同时指向同一处时，证据强度远高于任何单一路径。
        hurt = []
        for a in self.store.anchors:
            if a.get("kind") != "soft" or a.get("label") != "compliant":
                continue
            asset = {k: v for k, v in (a.get("asset") or {}).items() if not k.startswith("_")}
            ctx = self._ctx_with_assets([asset])
            hits = []
            for ck in self._lib_checkers():
                try:
                    f = ck.check(asset, ctx)
                except Exception:
                    _note_degrade("service.checker_skipped",
                                  RuntimeError("判据在 check 时抛异常（参数/字段类型不符等）"),
                                  fallback="跳过该判据 —— 这条资产上它**没判**",
                                  severity=SEVERITY_JUDGMENT, store=self.store)
                    continue
                if f is not None and not f.ok:
                    cl = ck.clause
                    hits.append({"clause_id": cl.clause_id, "kind": getattr(cl.kind, "value", ""),
                                 "text": cl.text[:80],
                                 "params": {k: v for k, v in (getattr(cl, "params", {}) or {}).items()
                                            if k in ("field", "min", "max", "table_key")},
                                 "message": f.message[:70]})
            if hits:
                hurt.append({"asset_id": _aid_of(asset), "confirmed_by": a.get("how", ""),
                             "over_strict_clauses": hits})

        # 把两条路径互相标注：同一条判据同时出现在"冲突"与"误伤"里 → 双重证据
        conflict_clauses = set()
        for am in out:
            conflict_clauses.add(am["constant_clause"]["clause_id"])
            conflict_clauses.add(am["table_clause"]["clause_id"])
        for h in hurt:
            for c in h["over_strict_clauses"]:
                c["also_in_conflict_detection"] = c["clause_id"] in conflict_clauses

        both = sorted({c["clause_id"] for h in hurt for c in h["over_strict_clauses"]
                       if c["also_in_conflict_detection"]})
        return {"ok": True, "n_amendments": len(out), "amendments": out,
                "n_false_alarm_evidence": len(hurt), "false_alarm_evidence": hurt,
                "doubly_confirmed_clauses": both,
                "note": ("修订建议由**两条独立证据路径**推导："
                         "①条款间冲突（机械检测）②误伤（人工确认合规却被判违规）。"
                         "`doubly_confirmed_clauses` 里的条款被两条路径同时指向，"
                         "证据强度最高。**是否修订、如何措辞由人决定**——改标准是权威行为。")}

    def _abstentions(self, anchors: list[dict]) -> dict:
        """扫一遍判据在锚点上的执行，收集所有 `evidence.abstained` 的条目。

        这是"不静默通过"的机械保证：弃权的判据会在这里列出，附原因。
        """
        out: dict[str, list[str]] = {}
        assets = [a["asset"] for a in anchors]
        cctx = self._ctx_with_assets(assets)
        for ck in self._lib_checkers():
            cid = ck.clause.clause_id
            reasons = set()
            for a in assets:
                try:
                    f = ck.check(a, cctx)
                except Exception as exc:
                    reasons.add(f"执行异常：{type(exc).__name__}")
                    continue
                if f is not None and (f.evidence or {}).get("abstained"):
                    reasons.add(str((f.evidence or {}).get("reason", "未说明")))
            if reasons:
                out[cid] = sorted(reasons)
        return {"n_abstained_clauses": len(out), "by_clause": out,
                "note": ("弃权 = 缺少外部真值，判据主动不判（不是通过）。"
                         "补上真值后它会自动生效。") if out else "无判据弃权"}

    def _ctx_with_assets(self, assets: list[dict] | None = None) -> dict:
        """★ 把**资产集合**注入 ctx。

        集合级判据（v2 的 UNIQUE_ACROSS）需要看到整个集合才能判"跨资产"的事实；
        而 v1 的判据只吃 `(asset, ctx)`。把集合挂在 ctx 的 `_assets` 键上，
        就让两类判据能在**同一个调用路径**里工作，不必给每个调用点改签名。
        """
        c = dict(self.ctx)
        if assets is not None:
            c["_assets"] = assets
        return c

    def _verifier(self) -> StandardVerifier:
        return StandardVerifier(compile_standard(self._library()))

    def _compiled(self):
        return compile_standard(self._library())

    # ---------------------------------------------------------------- std_*

    # ── ★ 裁决台账：记录"人对一条待裁决事项做了什么决定" ────────────
    def _adj_path(self) -> str:
        import os as _os
        return _os.path.join(self.store.root, "adjudications.json")

    def _adj_load(self) -> list:
        import json as _json, os as _os
        p = self._adj_path()
        if not _os.path.exists(p):
            return []
        try:
            with open(p, encoding="utf-8") as f:
                d = _json.load(f)
            return d if isinstance(d, list) else []
        except Exception:
            return []

    def std_adjudicate(self, ref: str, decision: str, by: str = "human",
                       reason: str = "") -> dict:
        """★ 记录一次**人对待裁决事项的裁决**。

        为什么需要
        ----------
        `std_pending` 能列出"有几件事在等人"，`std_amendments` 能生成修订建议 ——
        **但没有任何机制记录"人已经裁决了"。**
        于是那几件事**会永远挂在待裁决台账上，谁也消不掉**。

        **环路缺的不是"发现问题"，也不是"生成建议"，而是"记下决定"。**

        参数
        ----
        ref      待裁决事项的编号（`std_pending` 的 items[].ref）
        decision `accepted` / `rejected` / `deferred`
        by       谁裁决的（默认 human）
        reason   裁决理由（**必填才有审计价值**，但本工具不强制）

        ★ 本工具**只记录决定，不改标准、不改判据**。
          "采纳"一条修订建议之后要如何改标准，仍是分开的动作。
        """
        import json as _json, os as _os, datetime as _dt
        decision = (decision or "").strip().lower()
        if decision not in ("accepted", "rejected", "deferred"):
            return {"ok": False, "why": f"decision 必须是 accepted/rejected/deferred，收到 {decision!r}"}
        rows = self._adj_load()
        rec = {
            "ref": ref, "decision": decision, "by": by, "reason": reason,
            "at": _dt.datetime.now().isoformat(timespec="seconds"),
        }
        rows.append(rec)
        try:
            _os.makedirs(_os.path.dirname(self._adj_path()), exist_ok=True)
            with open(self._adj_path(), "w", encoding="utf-8") as f:
                _json.dump(rows, f, ensure_ascii=False, indent=1)
        except Exception as e:
            return {"ok": False, "why": f"写入失败 {type(e).__name__}: {e}"}
        return {"ok": True, "recorded": rec, "n_adjudications": len(rows)}

    def std_adjudications(self) -> dict:
        """列出全部裁决记录（追加式，不覆盖历史）。"""
        rows = self._adj_load()
        eff = {}
        for r in rows:                      # 同一 ref 以**最后一次**为准
            eff[r["ref"]] = r
        return {"ok": True, "n_records": len(rows), "n_refs": len(eff),
                "records": rows, "effective": eff}

    def std_clause_list(self, state: Optional[str] = None) -> dict:
        return {"ok": True, "clauses": self.store.list_clauses(state),
                "stats": self.store.stats()}

    def std_verify(self, assets: list[dict]) -> dict:
        v = self._verifier()
        results = []
        # ★ 弃权按**本次调用**统计（而不是拿库级审计结果凑数）
        per_call_ab: dict = {}
        for i, a in enumerate(assets):
            ctx = self._ctx_with_assets(assets)
            findings = [f for f in v.verify(a, ctx) if f is not None]
            vs = [f for f in findings if not f.ok]
            # 弃权 = 返回 ok=True 但证据里标了 abstained。
            # 它们**不在 vs 里**，所以只看 violations 永远发现不了 —— 必须单独收集。
            for f in findings:
                ev = f.evidence or {}
                if ev.get("abstained"):
                    per_call_ab.setdefault(f.clause_id, []).append(
                        {"asset_id": _aid_of(a, i), "reason": ev.get("reason", "")})
            results.append({
                "index": i, "asset_id": _aid_of(a, i),
                "compliant": len(vs) == 0,
                "n_violations": len(vs),
                "compliance": v.compliance(a, ctx),
                # ★ 字段名严格对齐标准契约（compiler.Finding）：
                #   clause_id / source / ok / message / evidence / clause_text
                #   技能包的渲染脚本按这套键取值，改名会让它们取不到值。
                "violations": [{"clause_id": f.clause_id, "source": f.source, "ok": f.ok,
                                "message": f.message, "clause_text": f.clause_text,
                                "evidence": f.evidence}
                               for f in vs],
            })
        total_clauses = len(self._compiled().checkers)
        # ── 弃权块 ──────────────────────────────────────────────
        # ★★ `abstained` 现在是**本次调用的**弃权，不再是库级审计结果。
        #
        #   之前这里直接塞 `std_audit()["abstained"]` —— 那是"整个判据库里谁在
        #   **锚点夹具**上弃权"，与"这一次校验里哪些条款没判"是两件事。
        #   实测后果（多角度测试 A5）：单条校验一条与别条重复的资产时，
        #   集合级唯一性判据其实**无法判定**，但：
        #     · `compliant: True`
        #     · 库级弃权清单里也没有它
        #   → **逐条校验时重复检测永不生效，且没有任何提示。**
        #
        #   现在两层都给：`abstained` = 本次调用；`abstained_library` = 库级体检。
        abstained = {
            "n_abstained_clauses": len(per_call_ab),
            "by_clause": {k: sorted({x["reason"] for x in vv}) for k, vv in per_call_ab.items()},
            "by_asset_clause": per_call_ab,
            "note": ("**本次调用**里弃权的判据 —— 它们返回 ok=True 但**没有真正判定**。"
                     "`compliant: True` 只代表「被判到的条款里没有违例」，"
                     "**不等于「所有条款都判过了」**。"),
        }
        # 库级体检（缓存，变化时自动失效）
        _ab_lib = None
        try:
            # ★ 键里放**条款 id 的排序元组**，而不是数量 ——
            #   "退役第7条 + 采纳第48条"数量不变但弃权集合可能变。
            _key = (str(getattr(self.store, "root", "")),
                    len(self._compiled().checkers),
                    tuple(sorted(self.store.clauses.keys())))
            _ab_lib = _ABSTAIN_CACHE.get(_key)
            if _ab_lib is None:
                _ab_lib = (self.std_audit().get("abstained") or {})
                _ABSTAIN_CACHE[_key] = _ab_lib
        except Exception as _e:
            _ab_lib = {"_error": f"{type(_e).__name__}: {_e}"}
        return {"ok": True, "n_assets": len(assets), "n_clauses": total_clauses,
                "compliant_rate": round(sum(1 for r in results if r["compliant"])
                                        / len(results), 4) if results else None,
                "abstained": abstained,
                "abstained_library": _ab_lib,
                "skipped_note": ("`abstained` 列出**本次调用**里弃权的判据 —— 它们返回 ok=True，但**没有真正判定**。"
                                 "`compliant: True` 只说明「被判到的条款里没有违例」，**不等于「所有条款都判过了」**。"
                                 "`abstained_library` 是库级体检（整库谁在锚点夹具上弃权），两者含义不同。"),
                "results": results}

    def std_compliance(self, assets: list[dict]) -> dict:
        r = self.std_verify(assets)
        return {"ok": True, "compliant_rate": r["compliant_rate"],
                "n_assets": r["n_assets"], "n_clauses": r["n_clauses"],
                "per_asset": [{"asset_id": x["asset_id"], "compliance": x["compliance"]}
                              for x in r["results"]]}

    def std_hard_anchors(self, source_clause: Optional[str] = None,
                         label: Optional[str] = None) -> dict:
        items = self.store.anchors_by_kind("hard")
        if source_clause:
            items = [a for a in items if a["source_clause"] == source_clause]
        if label:
            items = [a for a in items if a["label"] == label]
        clauses = self._library()
        return {"ok": True, "n": len(items), "anchors": items,
                "note": "覆盖率为标准编译期指标，调用 std_audit 查看"}

    def std_propose(self, spec: dict) -> dict:
        """★ 提交一条候选判据 → 由锚点裁决。

        裁决规则（三条，全部来自实测，不是拍脑袋）：
          R1 **必须见过合规锚点**才允许采纳 —— 实测把误纳率从 0.67 压到 0.07
          R2 **零误报**：在任何合规锚点上触发即否决
          R3 **可归因命中**：必须在某条违规锚点上触发，**且现有判据库抓不到它**
             （归因对象是"当前库全部判据"，不是种子库 —— 这是 docs/31 发现 1 的修正）
        """
        try:
            kind = ClauseKind(spec["kind"])
        except Exception:
            return {"ok": False, "verdict": "rejected",
                    "reason": f"未知判据类型 {spec.get('kind')}；"
                              f"可用类型：{[k.value for k in ClauseKind]}"}
        clause = Clause(clause_id=spec.get("clause_id") or f"提案-{int(time.time())}",
                        title=spec.get("title", ""), kind=kind,
                        text=spec.get("text", ""), params=spec.get("params", {}),
                        standard=spec.get("standard", "（系统自提案）"))
        # ★ 用 make_checker 而不是直接查 _CHECKER_BY_KIND：
        #   否则**新增语言无法被提案** —— 系统能用新语言判案、却不能用新语言长大，
        #   语言就永远长不大。而"提案"正是语言扩张的唯一入口。
        from standard.grammar_v2 import make_checker
        chk = make_checker(clause)

        anchors = self.store.anchors
        n_soft = sum(1 for a in anchors if a.get("kind") == "soft")
        n_compliant = sum(1 for a in anchors if a["label"] == "compliant")
        if n_compliant == 0:
            return {"ok": True, "verdict": "rejected",
                    "reason": "R1 未满足：从未见过任何合规锚点，不允许采纳判据",
                    "stats": {"n_anchors": len(anchors), "n_compliant": 0}}

        library = self._library()
        lib_checkers = self._lib_checkers()

        caught, false_alarm, already = [], [], []
        # ★ 集合级判据需要看到"整批锚点资产"才能判跨资产事实；
        #   这里预先把集合备好，避免在循环里拼一个不存在的作用域变量（曾因 NameError 挂掉冒烟测试）。
        _anchor_assets = [x["asset"] for x in anchors]
        for a in anchors:
            fires = not chk.check(a["asset"], self._ctx_with_assets([a["asset"]])).ok
            if not fires:
                continue
            if a["label"] == "compliant":
                false_alarm.append(a["anchor_id"])
            else:
                if any(not c.check(a["asset"], self._ctx_with_assets(_anchor_assets)).ok for c in lib_checkers):
                    already.append(a["anchor_id"])       # 现有判据已能抓到 → 不算贡献
                else:
                    caught.append(a["anchor_id"])

        stats = {"n_anchors": len(anchors), "n_compliant_anchors": n_compliant,
                 "n_soft_confirms": n_soft, "attributable_caught": len(caught),
                 "false_alarm": len(false_alarm),
                 "explained_by_existing": len(already),
                 "caught_ids": caught[:10], "false_alarm_ids": false_alarm[:10]}

        if false_alarm:
            verdict, reason = "rejected", f"R2 未满足：在合规锚点 {false_alarm[:3]} 上误报"
        elif not caught:
            verdict, reason = "rejected", ("R3 未满足：没有任何**可归因**命中"
                                           "（要么抓不到违规锚点，要么现有判据已经能抓到）")
        else:
            verdict, reason = "adopted", f"R1/R2/R3 全部满足：可归因命中 {len(caught)} 条，零误报"

        if verdict == "adopted":
            self.store.add_clause(clause, status="adopted", evidence=stats,
                                  reason=reason, origin="system_proposed")
        else:
            self.store.add_clause(clause, status="rejected", evidence=stats,
                                  reason=reason, origin="system_proposed")
        self.store.save()
        self.store.log({"event": "propose", "clause": clause.clause_id,
                        "verdict": verdict, "reason": reason, "stats": stats})
        return {"ok": True, "verdict": verdict, "reason": reason, "stats": stats,
                "clause": clause_to_dict(clause)}

    def std_confirm(self, asset: dict, label: str, by: str = "human") -> dict:
        """★ 软确认：人点头。这是唯一"贵"的真值来源，必须留痕。"""
        if label not in ("compliant", "violating"):
            return {"ok": False, "error": "label 必须是 compliant / violating"}
        aid = f"SA-{len(self.store.anchors_by_kind('soft')) + 1:03d}"
        rec = {"anchor_id": aid, "label": label, "source_clause": "人工确认",
               "how": f"由 {by} 确认", "kind": "soft", "asset": asset}
        self.store.add_anchors([rec])
        self.store.save()
        self.store.log({"event": "soft_confirm", "anchor_id": aid, "label": label,
                        "by": by, "asset_id": asset.get("asset_id")})
        return {"ok": True, "anchor_id": aid, "n_soft": len(self.store.anchors_by_kind("soft"))}


    def std_answer(self, asset: dict, verdict: str, by: str = "human",
                   note: str = "") -> dict:
        """★ 闭环：**人回答问题 → 系统吸收 → 立即分型 → 给出下一步**。

        此前的缺口：`std_ask` 会提问，但人回答之后**没有运行时路径把答案吸收回去**——
        标注只能离线改数据文件。提问了却不接收答案，环就是断的。

        吸收后立刻用**当前判据库**重判这条资产，分成四种情形：

          verdict=violating 且 库放行   → **盲区**（判据不够）→ 应提炼新判据
          verdict=violating 且 库判违规 → 一致，人的确认只是复核，无需动作
          verdict=compliant 且 库判违规 → ★ **误伤**（判据过严）→ 应审查/收紧该判据
          verdict=compliant 且 库放行   → 一致，无需动作

        ★ "盲区"与"误伤"是**同一个问题的两个方向**：判据库与人对不上。
          此前系统只会报盲区（偏松），从没报过误伤（偏严）——只朝一个方向自省。
        """
        if verdict not in ("compliant", "violating"):
            return {"ok": False, "error": "verdict 必须是 compliant / violating"}
        conf = self.std_confirm(asset, verdict, by=by)
        clean = {k: v for k, v in asset.items() if not k.startswith("_")}
        ctx = self._ctx_with_assets([clean])
        hits = []
        for ck in self._lib_checkers():
            try:
                f = ck.check(clean, ctx)
            except Exception:
                _note_degrade("service.checker_skipped",
                              RuntimeError("判据在 check 时抛异常（参数/字段类型不符等）"),
                              fallback="跳过该判据 —— 这条资产上它**没判**",
                              severity=SEVERITY_JUDGMENT, store=self.store)
                continue
            if f is not None and not f.ok:
                hits.append({"clause_id": ck.clause.clause_id, "message": f.message[:80],
                             "clause_text": f.clause_text[:60]})
        lib_says_bad = bool(hits)

        if verdict == "violating" and not lib_says_bad:
            kind, action = "blindspot", "判据不够：该条是**盲区**，应提炼新判据（clause-proposal）"
        elif verdict == "compliant" and lib_says_bad:
            kind, action = "false_alarm", "★ 判据过严：该条被现有判据**误伤**，应审查这些判据"
        else:
            kind, action = "consistent", "一致：人的确认与判据库结论相同，无需动作"

        self.store.log({"event": "std_answer", "asset_id": _aid_of(asset),
                        "verdict": verdict, "kind": kind, "by": by,
                        "hits": [h["clause_id"] for h in hits]})
        return {"ok": True,
                "anchor_id": conf.get("anchor_id"),
                "asset_id": _aid_of(asset),
                "human_verdict": verdict,
                "library_says": "violating" if lib_says_bad else "compliant",
                "relation": kind,
                "hits": hits,
                "next_action": action,
                "note": note or "答案已作为软锚点留痕，并立即参与后续裁决",
                "n_soft": conf.get("n_soft")}

    def std_feedback(self) -> dict:
        """★ 汇总"人机不一致"的两个方向：**盲区（偏松）** 与 **误伤（偏严）**。

        两者同源——判据库与人对不上。只报一个方向是**单侧自省**。
        这里把软锚点（人的确认）全部拿出来，逐条与当前库对比。
        """
        soft = [a for a in self.store.anchors if a.get("kind") == "soft"]
        blind, hurt, agree = [], [], 0
        for a in soft:
            asset = a.get("asset") or {}
            clean = {k: v for k, v in asset.items() if not k.startswith("_")}
            ctx = self._ctx_with_assets([clean])
            hits = []
            for ck in self._lib_checkers():
                try:
                    f = ck.check(clean, ctx)
                except Exception:
                    _note_degrade("service.checker_skipped",
                                  RuntimeError("判据在 check 时抛异常（参数/字段类型不符等）"),
                                  fallback="跳过该判据 —— 这条资产上它**没判**",
                                  severity=SEVERITY_JUDGMENT, store=self.store)
                    continue
                if f is not None and not f.ok:
                    hits.append(ck.clause.clause_id)
            bad = bool(hits)
            row = {"asset_id": _aid_of(asset), "human": a.get("label"),
                   "library": "violating" if bad else "compliant", "clauses": hits[:6]}
            if a.get("label") == "violating" and not bad:
                blind.append(row)
            elif a.get("label") == "compliant" and bad:
                hurt.append(row)
            else:
                agree += 1
        return {"ok": True, "n_soft_confirms": len(soft), "n_agree": agree,
                "n_blindspots": len(blind), "n_false_alarms": len(hurt),
                "blindspots": blind, "false_alarms": hurt,
                "verdict": ("★ 判据库与人的确认存在分歧"
                            if (blind or hurt) else "判据库与人的确认全部一致"),
                "note": ("盲区 = 判据偏松；误伤 = 判据偏严。"
                         "只看前者是单侧自省——这一节把两个方向都报出来。")}



    def _corpus_contradiction(self, clause_id: str, harm_row: dict | None) -> dict | None:
        """判定"误伤证据"是不是其实**与语料原文冲突**（即：人错了，不是判据错了）。

        依据：该判据的表若来自语料，就从语料重新抽一遍表，看被确认合规的那些资产
        的取值是否**确实越过语料写的上限**。是 → 标准 vs 人的冲突，不该退役判据。
        """
        if not harm_row or not harm_row.get("harmed_assets"):
            return None
        cl = next((c for c in self.store.all_clauses() if c.clause_id == clause_id), None)
        if cl is None:
            return None
        params = getattr(cl, "params", {}) or {}
        tk = params.get("table_key")
        if not tk:
            return None
        try:
        # ★ 按**当前领域包**取语料（此前写死制造业：政务包里
        #   修订建议与退役判据都依据错误的语料）
            from standard.table_extractor import extract_limit_tables
            fresh = None
            _docs2 = _corpus_docs()
            for t in extract_limit_tables(_docs2):
                if t.table_key == tk:
                    fresh = t
                    break
            if fresh is None:
                return None
        except Exception:
            return None
        bad = []
        for a in self.store.anchors:
            if a.get("kind") != "soft" or a.get("label") != "compliant":
                continue
            asset = a.get("asset") or {}
            kv, v = asset.get(params.get("key_field")), asset.get(params.get("field"))
            row_t = fresh.rows.get(kv)
            if row_t and isinstance(v, (int, float)) and row_t.get("max") is not None \
                    and v > row_t["max"]:
                bad.append({"asset_id": _aid_of(asset), "key": kv, "value": v,
                            "corpus_max": row_t["max"], "doc": fresh.provenance.get("doc_id"),
                            "raw": (fresh.provenance.get("raw") or "")[:70]})
        if not bad:
            return None
        return {"table": tk, "contradicting": bad}

    def clause_harm_ledger(self) -> dict:
        """★ 判据危害台账：每条判据在**人确认过的合规资产**上误伤了几次。

        准入看的是"这条判据好不好"，危害台账看的是"它后来有没有变坏"。
        **只有准入没有退役，判据库就会单向膨胀**，而一条总在误伤的判据
        降低系统的分辨能力——它让人不再相信系统的结论。
        """
        soft_ok = [a for a in self.store.anchors
                   if a.get("kind") == "soft" and a.get("label") == "compliant"]
        harm: dict[str, list] = {}
        for a in soft_ok:
            asset = {k: v for k, v in (a.get("asset") or {}).items() if not k.startswith("_")}
            ctx = self._ctx_with_assets([asset])
            for ck in self._lib_checkers():
                try:
                    f = ck.check(asset, ctx)
                except Exception:
                    _note_degrade("service.checker_skipped",
                                  RuntimeError("判据在 check 时抛异常（参数/字段类型不符等）"),
                                  fallback="跳过该判据 —— 这条资产上它**没判**",
                                  severity=SEVERITY_JUDGMENT, store=self.store)
                    continue
                if f is not None and not f.ok:
                    harm.setdefault(ck.clause.clause_id, []).append(_aid_of(asset))
        rows = [{"clause_id": cid, "n_harmed": len(v), "harmed_assets": v[:8],
                 "suggest": ("★ 建议退役：该判据在人工确认的合规资产上反复误伤"
                             if len(v) >= 2 else "观察：仅 1 次误伤，证据尚不充分")}
                for cid, v in sorted(harm.items(), key=lambda x: -len(x[1]))]
        return {"ok": True, "n_soft_compliant": len(soft_ok), "n_harming_clauses": len(rows),
                "ledger": rows,
                "note": ("准入（R1/R2/R3）判「该不该进」，危害台账判「该不该留」。"
                         "两者对称，缺一个判据库就会单向膨胀。")}

    def std_retire(self, clause_id: str, reason: str = "", by: str = "human",
                   force: bool = False) -> dict:
        """★ 退役一条判据。默认要求**有危害证据**才允许退役（防止随手删判据）。"""
        led = self.clause_harm_ledger()
        row = next((r for r in led["ledger"] if r["clause_id"] == clause_id), None)
        if not force and (row is None or row["n_harmed"] < 1):
            return {"ok": False,
                    "error": (f"判据 {clause_id} 没有危害记录。"
                              "退役需要证据：它在**人确认过的合规资产**上误伤过。"),
                    "hint": "确有理由请传 force=True 并写明 reason"}
        # ★★ 退役前必须分清「判据错了」还是「人错了」。
        #    实测踩到：人工确认"C类 200 小时合规"，而语料原文写着"C类不超过 168" ——
        #    这时的冲突是**标准与人的冲突**，不是判据的错。
        #    若直接采信人的确认退役判据，就会抹掉真阳性（实测 1017 从"违例"变成"放行"）。
        #    判定：若该判据的表来自语料，而被"误伤"的资产取值**确实违反语料原文**，
        #         则拒绝退役，改走修订流程。
        corpus_conflict = self._corpus_contradiction(clause_id, row)
        if corpus_conflict and not force:
            return {"ok": False, "relation": "standard_vs_human",
                    "error": ("这**不是判据的错**：被确认合规的资产取值与语料原文冲突。"
                              "退役会抹掉真阳性。"),
                    "detail": corpus_conflict,
                    "next_action": "走标准修订（std_amendments）：要么改语料/标准，要么修正确认",
                    "hint": "若确认语料/标准有问题，请先修订原文；确要强退请传 force=True"}

        # soft 锚点上的证据先留存
        r = self.store.retire_clause(clause_id, reason=reason or "有危害证据", by=by)
        if not r.get("ok"):
            return r
        self.store.save()
        return {**r, "harm_evidence": row,
                "library_size_after": len(self.store.list_clauses("adopted")),
                "note": "退役不是删除：记录保留、可审计、可复活（store.revive_clause）"}

    def std_audit(self) -> dict:
        """对抗探针 + 判据库健康度 + 硬锚点自检。"""
        clauses = self._library()
        compiled = compile_standard(clauses)
        v = StandardVerifier(compiled)

        # 探针 1：全选型（永远报违规）
        class _AlwaysViolate:
            clause = Clause("探针-恒真", "", ClauseKind.REQUIRED, "永远报违规", {})

            def check(self, asset, ctx):
                return Finding(self.clause.clause_id, self.clause.source, False,
                               "永远报违规", {}, self.clause.text)

        class _NeverViolate:
            clause = Clause("探针-恒假", "", ClauseKind.REQUIRED, "永远放行", {})

            def check(self, asset, ctx):
                return Finding(self.clause.clause_id, self.clause.source, True, "", {},
                               self.clause.text)

        anchors = self.store.anchors
        comp = [a for a in anchors if a["label"] == "compliant"]
        viol = [a for a in anchors if a["label"] == "violating"]

        def probe(chk) -> dict:
            fa = sum(1 for a in comp if not chk.check(a["asset"], self._ctx_with_assets([x["asset"] for x in comp + viol])).ok)
            ca = sum(1 for a in viol if not chk.check(a["asset"], self._ctx_with_assets([x["asset"] for x in comp + viol])).ok)
            return {"caught": ca, "false_alarm": fa}

        # ★ 直接把 store 里的**原始锚点 dict** 交给 verify_anchors，不要重建成
        #   HardAnchor：那会丢掉 `assets` / `attributed_to` 两个字段，
        #   集合级锚点因此无法归因，自检会误报"不一致"（实测踩到 1 条）。
        hal = list(self.store.anchors_by_kind("hard"))
        vcheck = verify_anchors(hal, clauses, self.ctx)

        # 塌缩检测：判据库对不同资产的分数是否有区分度
        scores = [v.compliance(a["asset"], self._ctx_with_assets([x["asset"] for x in anchors])).ok if hasattr(v.compliance(a["asset"], self._ctx_with_assets([x["asset"] for x in anchors])), "ok") else v.compliance(a["asset"], self._ctx_with_assets([x["asset"] for x in anchors])) for a in anchors]
        spread = (max(scores) - min(scores)) if scores else 0.0

        return {
            "ok": True,
            "library": {"n_adopted": len(clauses),
                        "n_proposed_rejected": len(self.store.list_clauses("rejected")),
                        "types": sorted({c.kind.value for c in clauses})},
            "anchor_selfcheck": vcheck,
            "probes": {
                "恒真判据": {**probe(_AlwaysViolate()), "verdict": "应被拒（误报过多）"},
                "恒假判据": {**probe(_NeverViolate()), "verdict": "应被拒（抓不到任何东西）"},
            },
            "discrimination": {"n": len(scores), "min": round(min(scores), 4) if scores else None,
                               "max": round(max(scores), 4) if scores else None,
                               "spread": round(spread, 4),
                               "verdict": "COLLAPSED" if spread < 1e-6 else "ok"},
            "coverage": coverage(self.store.all_clauses(), hal),
            # ★ 弃权清单：判据因为**缺少外部真值**（如未注入基准时间）而没有真正行使。
            #   弃权必须可见——否则"这条查过了、通过了"和"这条根本没查"长得一模一样。
            "abstained": self._abstentions(anchors),
            # ★ 降级清单：凡是走了回落路径的（领域包没装配上、v2 没装载成…）都在这里。
            #   与"弃权清单"同一条纪律：**"正常算出来了"和"悄悄退到别的路径算出来的"必须能分开看。**
            "degradations": _dg_snapshot(),
            # ★ 危害台账：准入判"该不该进"，它判"该不该留"。两者对称。
            "clause_harm": self.clause_harm_ledger(),
        }

    def std_ledger(self, limit: int = 50) -> dict:
        return {"ok": True, "events": self.store.ledger(limit)}

    # ---------------------------------------------------------------- asset_*
    def asset_blindspots(self, assets: list[dict]) -> dict:
        """★ 语料/数据侧盲区：被标为违规、但现有判据库**全部放行**的资产。

        这是驱动"判据提案"的输入 —— 没有它，系统不知道该往哪儿长。

        ★★ 必须区分两种**看起来一样、修法完全不同**的盲区：

          A 型 · `no_clause_covers`  判据库里**没有任何条款能表达这件事**
                                    → 需要**判据语言扩张**（贵，要动语言）

          B 型 · `abstained_masking` 有判据，但那条**弃权**了
                                    （缺外部真值：未注入基准时间 / 受控表缺行 / 无资产集合）
                                    → **补上真值即刻生效**（便宜，立即可修）

        不区分的话，B 型会被当成"语言不够用"，于是去扩张语言——
        而真正缺的只是**一个外部真值**。实测：未注入 `EVOSTD_NOW` 时，
        "未来日期"那条资产会被报成盲区，而它其实只需要注入基准时间。
        """
        lib_checkers = self._lib_checkers()
        ctx = self._ctx_with_assets(assets)          # 集合级判据需要看到整批
        blind, n_abst, n_nocov = [], 0, 0
        for i, a in enumerate(assets):
            if a.get("_label") != "violating":
                continue
            findings = [c.check(a, ctx) for c in lib_checkers]
            if any(f is not None and not f.ok for f in findings):
                continue                             # 被抓到了，不是盲区
            # 没抓到 → 看是不是**弃权**造成的
            abstained = [{"clause_id": c.clause.clause_id,
                          "kind": getattr(c.clause.kind, "value", str(c.clause.kind)),
                          "reason": (f.evidence or {}).get("reason", "未说明")}
                         for c, f in zip(lib_checkers, findings)
                         if f is not None and (f.evidence or {}).get("abstained")]
            cause = "abstained_masking" if abstained else "no_clause_covers"
            if abstained:
                n_abst += 1
            else:
                n_nocov += 1
            blind.append({
                "index": i, "asset_id": _aid_of(a, i), "why": a.get("_why", ""),
                "cause": cause,
                "abstained_clauses": abstained,
                "actionable": ("补外部真值即刻生效" if abstained
                               else "需判据语言扩张或新增条款"),
                "asset": a,
            })
        blind.sort(key=lambda x: x["cause"])          # B 型（易修）排前面
        return {"ok": True, "n_assets": len(assets), "n_blindspots": len(blind),
                "n_abstained_masking": n_abst, "n_no_clause_covers": n_nocov,
                "blindspots": blind[:20],
                "note": ("B 型（abstained_masking）只需补外部真值；"
                         "A 型（no_clause_covers）才需要动判据语言。"
                         "两者在'判据全放行'这一点上长得一样，但修法完全不同。")}

    def asset_lineage(self, asset: dict) -> dict:
        """决策链路溯源：这条资产被哪些条款判过、结论是什么。"""
        v = self._verifier()
        fs = v.verify(asset, self._ctx_with_assets([asset]))
        return {"ok": True, "asset_id": asset.get("asset_id"),
                "chain": [{"clause_id": f.clause_id, "source": f.source, "ok": f.ok,
                           "message": f.message, "clause_text": f.clause_text}
                          for f in fs],
                "n_clauses": len(fs),
                "compliant": all(f.ok for f in fs)}
