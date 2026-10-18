"""MCP 适配层：把 evostd 的服务层暴露成 Nexent 能挂载的工具。

平台约束（全部来自实测，见 docs/02）：
  · 只支持 `sse` / `streamable-http` 两种传输，**stdio 不能直连**
  · URL 后缀决定传输类型：`/sse` → sse，其余（含 `/mcp`）→ streamable-http
  · **单次工具调用硬超时 60 秒**，超时会终止整个 Agent run
    → 所以进化回路必须**分片**：`evo_init` / `evo_step` / `evo_status` / `evo_finalize`
  · docker-compose 部署下，容器访问宿主服务必须写 `host.docker.internal`，不能写 127.0.0.1

跑法：
    python -m evostd.server                 # 默认 127.0.0.1:8765，路径 /mcp
    python -m evostd.server --port 8765
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from evostd.degrade import note as _note_degrade, SEVERITY_JUDGMENT, SEVERITY_CAPABILITY
import time
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evostd.service import StandardService  # noqa: E402
from evostd.store import Store  # noqa: E402

# ---------------------------------------------------------------------------
#  全局服务实例（一个进程一份状态）
# ---------------------------------------------------------------------------
DEFAULT_STATE_DIR = os.environ.get(
    "EVOSTD_STATE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "runs", "evostd-state"))
SERVICE: Optional[StandardService] = None
RUNS: dict[str, Any] = {}


_PACK_MOD = None      # ★ 当前领域包模块：让按领域取语料的工具不再写死制造业
_DOC_RAW = None       # 当前领域包的 doc_raw_text
_ASSETS_MOD = None    # 当前领域包的 assets 模块
_CORPUS_MOD = None    # 当前领域包的 corpus 模块（可能不存在）


def _try_mod(name: str):
    """尽力导入一个可选模块；失败返回 None（不抛）。"""
    import importlib as _il
    try:
        return _il.import_module(name)
    except Exception:
        return None


def build_service(domain: str = "manufacturing",
                  state_dir: str = DEFAULT_STATE_DIR) -> StandardService:
    """按领域包装配服务。领域包可插拔——换行业只换这一处。"""
    global SERVICE
    global _PACK_MOD, _DOC_RAW, _ASSETS_MOD, _CORPUS_MOD
    import importlib

    # ★ 政务领域包：与制造业包**同一套内核代码**，只是换了一个包目录。
    #   这一段的存在本身就是 Domain Pack 可插拔性的证据。
    extra_clauses: list = []      # 扩张条款（需过准入裁决）
    labeled_assets: list = []     # 领域包提供的标注样本（准入裁决的外部真值）
    if domain in ("gov", "gov_catalog", "government"):
        try:
            pack = importlib.import_module("domains.gov_catalog")
            _PACK_MOD = pack
            _DOC_RAW = getattr(pack, "doc_raw_text", None)
            _ASSETS_MOD = assets_mod = importlib.import_module("domains.gov_catalog.assets")
            _CORPUS_MOD = _try_mod("domains.gov_catalog.corpus")
            std_mod = importlib.import_module("domains.gov_catalog.standard_gov")
            clauses = pack.standards()
            ctx = dict(assets_mod.ctx())
            pool = assets_mod.all_assets()
            labeled_assets = pool          # ★ 标注样本 = 准入裁决的外部真值
            base = next((a for a in pool if a.get("_label") == "compliant"), None)
            if base is None:
                raise RuntimeError("政务领域包里没有合规样本")
            base = {k: v for k, v in base.items() if not k.startswith("_")}
            standard_name = std_mod.STANDARD_NAME
            print(f"[evostd] 领域包 gov_catalog 已装配："
                  f"{len(clauses)} 条条款 / {len(pool)} 条资产", file=sys.stderr)
            try:
                from standard.grammar_v2_experiment import (v2_clauses, extracted_ctx,
                                                            cross_check_tables)
                v2c = v2_clauses(standard_name, domain="gov_catalog", docs=pack.docs())
                if v2c:
                    extra_clauses = list(v2c)      # ★ 同上：走准入裁决通道
                    ctx = dict(ctx)
                    ctx.update(extracted_ctx(pack.docs()))
                    _now = os.environ.get("EVOSTD_NOW")
                    if _now:
                        ctx["now"] = _now
                        ctx["now_source"] = "环境变量 EVOSTD_NOW"
                    else:
                        ctx["now_source"] = "未注入（基准时间约束类判据将弃权）"
                    print(f"[evostd] 判据语言 v2 已装载（政务）：新增 {len(v2c)} 条"
                          f"（{', '.join(c.clause_id for c in v2c)}）", file=sys.stderr)
            except Exception as exc:
                _note_degrade("server.build_service.gov_v2_load", exc, fallback="只用 v1",
                              severity=SEVERITY_JUDGMENT)
                print(f"[evostd] ★ 政务 v2 装载失败，退回 v1（{type(exc).__name__}: {exc}）",
                      file=sys.stderr)
        except Exception as exc:
            _note_degrade("server.build_service.gov_pack", exc,
                          fallback="政务领域包整体不可用", severity=SEVERITY_JUDGMENT)
            print(f"[evostd] ★ 政务领域包装配失败：{type(exc).__name__}: {exc}", file=sys.stderr)

    elif domain in ("manufacturing", "industrial"):
        try:
            pack = importlib.import_module("domains.manufacturing")
            _PACK_MOD = pack
            _DOC_RAW = getattr(pack, "doc_raw_text", None)
            _ASSETS_MOD = assets_mod = importlib.import_module("domains.manufacturing.assets")
            _CORPUS_MOD = _try_mod("domains.manufacturing.corpus")
            std_mod = importlib.import_module("domains.manufacturing.standard_industrial")
            clauses = pack.standards()
            ctx = dict(assets_mod.ctx())
            pool = assets_mod.all_assets()
            labeled_assets = pool          # ★ 标注样本 = 准入裁决的外部真值
            base = next((a for a in pool if a.get("_label") == "compliant"), None)
            if base is None:
                raise RuntimeError("制造领域包里没有合规样本，无法作为硬锚点模板")
            base = {k: v for k, v in base.items() if not k.startswith("_")}
            standard_name = std_mod.STANDARD_NAME
            print(f"[evostd] 领域包 manufacturing 已装配："
                  f"{len(clauses)} 条条款 / {len(pool)} 条资产", file=sys.stderr)

            # ★★ 判据语言 v2 装载 —— 让"语言扩张"真正作用于服务，而不只是实验里成立。
            #    新增条款由**语料机械抽表**生成（不是手写常量）；限额表与 base 表交叉校验。
            try:
                from standard.grammar_v2_experiment import (v2_clauses, extracted_ctx,
                                                            cross_check_tables)
                xc = cross_check_tables()
                if not xc["ok"]:
                    print(f"[evostd] ★★ 限额表交叉校验**不一致**：{xc['diffs']}"
                          f" —— 机械抽取与人工抄写有出入，请先查清再用", file=sys.stderr)
                v2c = v2_clauses(standard_name)
                if v2c:
                    # ★ 扩张条款**不再直接并入 clauses**：它们走 extra 通道，
                    #   由 StandardService._seed 用标注样本做 R1/R2/R3 准入裁决。
                    extra_clauses = list(v2c)
                    ctx = dict(ctx)
                    ctx.update(extracted_ctx())
                    # 基准时间**显式注入**（不是 datetime.now()，判据仍是纯函数）。
                    # 未注入时 NOT_FUTURE 判据会弃权（计入"未行使"），不会静默通过。
                    # ★ 基准时间必须**显式注入**；未注入则留空，让判据弃权（不拿系统时钟猜）。
                    _now = os.environ.get("EVOSTD_NOW")
                    if _now:
                        ctx["now"] = _now
                        ctx["now_source"] = "环境变量 EVOSTD_NOW"
                    else:
                        ctx["now_source"] = "未注入（基准时间约束类判据将弃权）"
                    print(f"[evostd] 判据语言 v2 已装载：新增 {len(v2c)} 条"
                          f"（{', '.join(c.clause_id for c in v2c)}）；"
                          f"限额表交叉校验 {'一致' if xc['ok'] else '★不一致'}；"
                          f"基准时间 {ctx.get('now') or '未注入（判据弃权）'}", file=sys.stderr)
            except Exception as exc:
                _note_degrade("server.build_service.v2_load", exc, fallback="只用 v1 判据语言",
                              severity=SEVERITY_JUDGMENT)
                print(f"[evostd] ★ v2 装载失败，退回 v1（{type(exc).__name__}: {exc}）",
                      file=sys.stderr)
        except Exception as exc:
            _note_degrade("server.build_service.mfg_pack", exc,
                          fallback="★ 回落演示样例（判据库将不是这个领域包的）",
                          severity=SEVERITY_JUDGMENT)
            print(f"[evostd] ★★ 制造领域包装配失败，回落演示样例："
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            from standard.samples import CLAUSES, CONTROLLED_STANDARDS, GOOD_ASSET
            clauses = CLAUSES
            ctx = {"controlled_standards": CONTROLLED_STANDARDS,
                   "retired_storage": ["oss://retired/legacy-2019"],
                   "retired_assets": ["AST-2019-0007"],
                   "withdrawn_standards": ["GB/T 11111-2005"]}
            base, standard_name = dict(GOOD_ASSET), CLAUSES[0].standard
            print(f"[evostd] 领域包 manufacturing 不可用（{exc}），已回落到演示样例",
                  file=sys.stderr)
    else:
        raise ValueError(f"未知领域包: {domain}")

    store = Store(os.path.join(state_dir, domain))
    # ★ 把当前领域包的语料模块注入服务：否则 std_amendments 与
    #   _corpus_contradiction 会用错领域的语料。
    from evostd.service import set_corpus_mod as _set_corpus
    _set_corpus(_CORPUS_MOD)
    SERVICE = StandardService(store, clauses, base, ctx, standard_name=standard_name,
                              extra=extra_clauses, labeled=labeled_assets)
    return SERVICE


# ---------------------------------------------------------------------------
#  工具实现（纯函数式包装，便于离线单测）
# ---------------------------------------------------------------------------
def _svc() -> StandardService:
    if SERVICE is None:
        return build_service()
    return SERVICE


def std_clause_list(state: Optional[str] = None) -> dict:
    """列出判据库（含状态、来源、理由）。state 可选 adopted/rejected/uncompilable。"""
    return _svc().std_clause_list(state)


def std_verify(assets_json: str) -> dict:
    """校验一批资产。入参是 JSON 数组字符串；返回逐条违例（含条款引用与原文）。

    ★ 必须显式传入资产——**不做任何隐式默认**。
      校验工具不声不响去验一批调用方没指定的资产，会让"验的是什么"变得不可追溯，
      与"决策链路可溯源"直接冲突。
      若调用方不知道有哪些资产，先用 asset_list 取数。
    """
    if not (assets_json or "").strip():
        return {"ok": False,
                "error": "必须提供待校验资产（JSON 数组）。可用 asset_list 先取资产清单。",
                "hint": "先调 asset_list() 拿到资产，再把它作为 assets_json 传进来"}
    assets = json.loads(assets_json)
    return _svc().std_verify(assets)


def asset_list(label: Optional[str] = None, limit: int = 20) -> dict:
    """列出当前领域包里的资产，供智能体先取数、再交给 std_verify 校验。

    label 可选 compliant / violating。
    """
    try:
        # ★ 按**当前领域包**取资产：此前写死制造业，政务包里会列出制造业资产
        all_assets = (_ASSETS_MOD or __import__("domains.manufacturing.assets",
                      fromlist=["all_assets"])).all_assets
        raw = all_assets()
    except Exception as e:
        return {"ok": False, "error": f"领域包资产不可用: {e}"}
    items = []
    for a in raw:
        if label and a.get("_label") != label:
            continue
        # ★ id 字段按领域兜底：政务包用 catalog_id；此前只取 asset_id，
        #   政务包里每条都是 None 而 ok 仍为 True。
        _aid_v = (a.get("asset_id") or a.get("catalog_id")
                  or a.get("asset_no") or a.get("id"))
        items.append({"asset_id": _aid_v, "label": a.get("_label"),
                      "why": a.get("_why", ""),
                      "asset": {k: v for k, v in a.items() if not k.startswith("_")}})
    return {"ok": True, "n": len(items[:limit]), "total": len(items), "items": items[:limit],
            "note": "把 asset 字段直接传给 std_verify 即可校验"}


def std_compliance(assets_json: str) -> dict:
    """只返回合规率与逐条得分（适合放进循环做适应度）。"""
    return _svc().std_compliance(json.loads(assets_json))


def std_hard_anchors(source_clause: Optional[str] = None,
                     label: Optional[str] = None) -> dict:
    """返回**机械抽取**的硬锚点（零人工真值）。label 可选 compliant/violating。"""
    return _svc().std_hard_anchors(source_clause, label)


def std_propose(clause_json: str) -> dict:
    """★ 提交一条候选判据，由锚点裁决。

    clause_json 形如 {"clause_id","title","kind","text","params"}；
    kind 取值见 std_clause_list 返回的 types。返回 verdict=adopted/rejected 与三条判据的理由。
    """
    return _svc().std_propose(json.loads(clause_json))


def std_confirm(asset_json: str, label: str, by: str = "human") -> dict:
    """★ 人工确认一条锚点（唯一"贵"的真值来源）。label 必须是 compliant 或 violating。"""
    return _svc().std_confirm(json.loads(asset_json), label, by)


def std_audit() -> dict:
    """审计：对抗探针 + 锚点自检 + 区分度 + 硬锚点覆盖率。"""
    return _svc().std_audit()


def std_ledger(limit: int = 50) -> dict:
    """台账（追加式事件流，可回滚、可审计）。"""
    return _svc().std_ledger(limit)


def std_snapshot(label: str = "manual") -> dict:
    """给判据库打一个内容级版本快照（平台做不到这件事）。"""
    r = _svc().store.snapshot(label)
    _svc().store.save()
    return {"ok": True, **r}


def std_rollback(snapshot_id: str) -> dict:
    """回滚到指定快照。"""
    return _svc().store.rollback(snapshot_id)


def asset_blindspots(assets_json: str) -> dict:
    """★ 返回"被标为违规、但现有判据全部放行"的资产 —— 判据提案的靶子。"""
    return _svc().asset_blindspots(json.loads(assets_json))


def asset_lineage(asset_json: str) -> dict:
    """决策链路溯源：这条资产被哪些条款判过、结论是什么（逐条带原文）。"""
    return _svc().asset_lineage(json.loads(asset_json))


def asset_ingest(asset_json: str) -> dict:
    """资产入库：先做硬锚点自查与合规校验，零违例才准入库；返回六维资产标识卡。"""
    asset = json.loads(asset_json)
    svc = _svc()
    v = svc._verifier()
    vs = v.violations(asset, svc.ctx)
    dims = {"身份": asset.get("asset_id"), "形态": asset.get("modality", asset.get("media_type")),
            "权限": asset.get("secrecy"), "归属": asset.get("owner_dept"),
            "时序": {"collect": asset.get("collect_time"), "ingest": asset.get("ingest_time")},
            "质量": {"score": asset.get("quality_score"), "completeness": asset.get("completeness")}}
    svc.store.log({"event": "asset_ingest", "asset_id": asset.get("asset_id"),
                   "compliant": len(vs) == 0, "n_violations": len(vs)})
    return {"ok": True, "accepted": len(vs) == 0, "asset_id": asset.get("asset_id"),
            "six_dimensions": dims, "n_violations": len(vs),
            "violations": [{"clause_id": f.clause_id, "source": f.source, "message": f.message,
                            "clause_text": f.clause_text, "evidence": f.evidence} for f in vs],
            "note": "零违例才准入库；有违例请先修正后重试"}


def asset_search(query: str, top_k: int = 5) -> dict:
    """在**当前领域的**多模态语料里检索（文本/表格/图文），返回命中与来源标识。

    ★ 两处修正（此前都是"看起来正常"）：

    ① **语料写死成制造业** —— `from domains.manufacturing.corpus import ...`。
       在政务包里调用它永远搜制造业语料，于是**要么返回 0 条、要么返回制造业文档**，
       而返回的是 `{"ok": True, "n_hits": 0}` —— **看起来像"没匹配到"，
       而不是"搜错了语料库"**。现在按当前领域包取。

    ② **打分是 `text.count(q)` 子串计数** —— 中文没有分词，长度不同的文档也不可比。
       现在用**字符 n-gram 重叠 + 精确子串加权 + 长度归一**：
       中文不需要分词器也能用，而且**确定性、可复算**。

    ★ 如实标注：这仍然是**词法检索（lexical）**，不是语义检索。
      真正的向量检索要接平台的索引（`/indices/{name}`），那是另一件事。
      但词法检索至少**不会因为换了领域就静默失效**。
    """
    if _PACK_MOD is None:
        return {"ok": False, "error": "服务未装配：请先 build_service(domain)"}
    q = (query or "").strip()
    if not q:
        return {"ok": True, "query": "", "n_hits": 0, "hits": [],
                "note": "空查询"}

    def _norm(t: str) -> str:
        # 去掉空白与常见标点：中文检索里这些只会稀释 n-gram 重叠
        _DROP = set(" \t\r\n，。、；：（）()「」【】《》,.;:!?-—…·\u201c\u201d\u2018\u2019'\"")

        return "".join(ch for ch in (t or "") if ch not in _DROP)

    def _grams(t: str, n: int) -> set:
        return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}

    def _score(text: str, qq: str) -> tuple:
        tn = _norm(text)
        if not tn:
            return (0.0, 0)
        exact = tn.count(qq)
        # 2-gram 与 3-gram 重叠（对中文够用；短查询退化为 1-gram）
        ns = [1, 2, 3] if len(qq) < 3 else [2, 3]
        tot = 0.0
        for k in ns:
            gq = _grams(qq, k)
            if not gq:
                continue
            gt = _grams(tn, k)
            tot += len(gq & gt) / len(gq)
        overlap = tot / len(ns)
        # 长度归一：长文档不该因为"字多"就占优
        norm = overlap * (len(qq) / max(len(tn), len(qq))) ** 0.25 if tn else 0.0
        return (round(0.7 * norm + 0.3 * min(1.0, exact), 6), exact)

    def _raw_of(d) -> str:
        # ★ 有的领域包的 doc_raw_text 是模块函数，有的是文档对象上的**方法**；
        #   直接 getattr 会拿到 bound method，送进 _norm 就是
        #   "TypeError: 'method' object is not iterable"（实测踩到）。
        #   一律**先取值再调用**，并把非字符串兜成空串。
        if _DOC_RAW is not None:
            try:
                v = _DOC_RAW(d)
                if callable(v):
                    v = v()
            except Exception:
                v = None
            if isinstance(v, str):
                return v
        v = getattr(d, "raw_text", "")
        if callable(v):
            try:
                v = v()
            except Exception:
                v = ""
        return v if isinstance(v, str) else ""

    try:
        docs = _PACK_MOD.docs()
    except Exception as e:
        return {"ok": False, "error": f"当前领域包语料不可用: {type(e).__name__}: {e}"}

    qn = _norm(q)
    hits = []
    for d in docs:
        text = _raw_of(d)
        sc, exact = _score(text, qn)
        if sc <= 0:
            continue
        tn = _norm(text)
        i = tn.find(qn)
        if i < 0:
            # 没有精确子串时，取首个命中的 n-gram 位置做片段
            g2 = _grams(qn, 2)
            pos = next((tn.find(g) for g in g2 if tn.find(g) >= 0), 0)
            i = max(0, pos)
        hits.append({"doc_id": getattr(d, "doc_id", "?"),
                     "doc_type": type(d).__name__,
                     "score": sc, "exact_hits": exact,
                     "excerpt": tn[max(0, i - 30):i + len(qn) + 50]})
    hits.sort(key=lambda x: (-x["score"], -x["exact_hits"]))
    return {"ok": True, "query": q, "n_hits": len(hits), "hits": hits[:max(1, top_k)],
            "domain": getattr(_PACK_MOD, "__name__", "?"),
            "method": "词法检索（字符 n-gram + 精确子串 + 长度归一）",
            "note": ("★ 这是**词法**检索，不是语义检索；向量检索需接平台索引，尚未接。"
                     "语料按**当前领域包**取，不再写死制造业。")}


def evo_archive(run_id: str, top_k: int = 5) -> dict:
    """返回进化存档：最优候选、血统与后代表现（CMP 近似）。"""
    r = RUNS.get(run_id)
    if r is None:
        return {"ok": False, "error": f"未知 run_id: {run_id}"}
    loop = r["loop"]
    ranked = sorted(loop.archive.cands, key=loop.archive.score, reverse=True)[:max(1, top_k)]
    return {"ok": True, "run_id": run_id, "archive_size": len(loop.archive.cands),
            "top": [{"cid": c, "score": round(loop.archive.score(c), 4),
                     "cmp": round(loop.archive.cmp(c), 4),
                     "generation": loop.archive.cands[c].gen,
                     "parent": loop.archive.cands[c].parent,
                     "n_descendants": len(loop.archive._descendants(c)),
                     "note": loop.archive.cands[c].note} for c in ranked]}


# ---------------------------------------------------------------------------
#  evo_*：进化回路（分片，绕开 60s 硬超时）
# ---------------------------------------------------------------------------
def evo_init(domain: str = "ontology", generations: int = 20, seed: int = 42) -> dict:
    """建立一次进化运行，返回 run_id。单步秒级，不触发 60s 超时。"""
    from evolve.core import Archive, Auditor, EvolutionLoop, Ledger
    from evolve.domains.ontology_growth import OntologyGrowthDomain
    from evolve.domains.skill_templates import SkillTemplateDomain

    d = {"ontology": lambda: OntologyGrowthDomain(seed=7),
         "skill": lambda: SkillTemplateDomain(seed=11)}.get(domain)
    if d is None and domain in ("clause", "clause_library"):
        # ★ 判据库域：进化回路**直接搜索判据库**，适应度来自语料合成的评测集，
        #   零误报是硬门。这是把"进化回路"与"标准判据库"两半接起来的那个域。
        from evolve.domains.clause_library import (ClauseLibraryDomain,
                                                   ClauseLibraryVerifier,
                                                   synthesize_labeled_assets)
        # ★ 按**当前领域包**取标准与 ctx：此前写死制造业，
        #   政务包里做判据库搜索时用的是制造业标准与上下文。
        _st = _PACK_MOD.standards
        _actx = _ASSETS_MOD.ctx
        from standard.compiler import compile_standard as _cs
        from standard.grammar_v2_experiment import extracted_ctx as _ec, v2_clauses as _v2
        _std = _st()
        _base = [ck.clause for ck in _cs(_std).checkers]
        _have = {b.clause_id for b in _base}
        # ★ 领域名从**当前领域包**取：此前三处写死 "manufacturing"，
        #   于是政务包里做判据库搜索时，候选池与评测集全是制造业的
        #   （实测驳回理由是 check_interval_hours 这类制造业字段）。
        _dom = _PACK_MOD.__name__.split(".")[-1]
        _base += [c for c in _v2(getattr(_std, "STANDARD_NAME", ""), _dom)
                  if c.clause_id not in _have]
        # ★★ 补上**当前领域包**的限额表。
        #    `extracted_ctx()` 只抽制造业的两张表（maintenance_cycle_days_limit /
        #    check_interval_hours_limit），政务的 update_limit_days_limit 不在里面。
        #    后果：候选 `-exact`（依赖 ctx 里有表）**静默弃权** → 漏掉全部违规样本
        #    → 得分 0；而自带表的 `-wide` 能判 → 得分高 → 被搜索选中 → 又被 R4 驳回。
        #    整条链因此走不通，而根因只是"ctx 少了一张表"。
        _ctx = dict(_actx()); _ctx.update(_ec())
        try:
            from standard.table_extractor import extract_limit_tables as _elt
            for _t in _elt(_PACK_MOD.docs()):
                if _t.table_key:
                    _ctx[_t.table_key] = _t.rows
        except Exception:
            pass
        _now = os.environ.get("EVOSTD_NOW")
        if _now:
            _ctx["now"] = _now
        _v = ClauseLibraryVerifier(synthesize_labeled_assets(_dom, 1),
                                   _base, _ctx, _dom)
        d = lambda: ClauseLibraryDomain(_v, seed=11)      # noqa: E731
    if d is None:
        return {"ok": False,
                "error": f"未知领域包: {domain}（可选 ontology / skill / clause）"}
    dom = d()
    evolve_v, heldout_v = dom.make_verifiers()
    out_dir = os.path.join(DEFAULT_STATE_DIR, "runs", f"{domain}-{int(time.time())}")
    os.makedirs(out_dir, exist_ok=True)

    auditor = Auditor(evolve_v)
    for name, cand in dom.probes():
        auditor.add_probe(name, cand)

    loop = EvolutionLoop(domain=dom, verifier=evolve_v, mutator=dom.mutator,
                         archive=Archive(), gate=None, auditor=auditor,
                         ledger=Ledger(os.path.join(out_dir, "ledger.jsonl")),
                         seed=seed, out_dir=out_dir)
    # 第 0 代：种子
    seed_c = dom.seed()
    res = loop._evaluate(seed_c)
    loop.archive.add(seed_c, res)
    loop._log_gen(0, res)

    run_id = f"RUN-{len(RUNS) + 1:03d}"
    RUNS[run_id] = {"loop": loop, "domain": dom, "heldout": heldout_v,
                    "out_dir": out_dir, "budget": generations, "done": 0}
    # ★ 候选池为空时必须说出来：空评测集上的 best_score 恒为 1.0，
    #   那是个**假满分**，不是"搜到了完美解"（实测在政务包踩到）。
    #   调用方应据此弃权，而不是把 1.0 读成好结果。
    _pool_empty = not getattr(dom, "pool", None)
    if _pool_empty:
        print(f"[evostd] ★ {domain} 域的候选池为空 —— 本轮的 best_score 无意义",
              file=sys.stderr)
    return {"ok": True, "run_id": run_id, "domain": domain,
            "seed_score": round(res.score, 4),
            "pool_empty": _pool_empty,
            "pool_empty_note": ("该领域的候选池/评测集为空（目前只有制造业包提供）："
                                "**本轮的 best_score 无意义**，不要读成\"搜到了完美解\"。"
                                if _pool_empty else ""),
            "generations_target": generations,
            "curve": loop.curve}



def evo_apply(run_id: str, by: str = "evolution", max_adopt: int = 8) -> dict:
    """★ 把进化搜索的结果**接回判据库**：搜索提议，闸门裁决。

    此前的缺口：`clause_library` 域能搜出最优判据组，但**搜索结果无处可去**——
    实验跑完只是打印出来，不会改变系统。**搜索与入库之间断了一截。**

    做法（与"最终裁决者永远在系统之外"完全一致）：

        进化搜出的每条判据 → 走**同一个 `_admit_clause` 闸门** → 通过的才入库

    ★ 关键：**搜索再强也不能绕过准入**。
      进化只被允许"提议"，采不采纳由 R1/R2/R3 决定 —— 与人工提案同一条路径。
      这样即使搜索被某种方式欺骗（例如适应度被过拟合），闸门仍然拦得住。
    """
    r = RUNS.get(run_id)
    if r is None:
        return {"ok": False, "error": f"未知 run_id: {run_id}"}
    loop = r["loop"]
    dom = r.get("domain")
    if getattr(dom, "kind", "") != "clause_library":
        return {"ok": False,
                "error": (f"该 run 的域是「{getattr(dom, 'kind', '?')}」，"
                          "只有 clause_library 域的产物是可入库的判据"),
                "hint": "用 evo_init(domain='clause') 建立一次判据库搜索"}
    best_cid = loop.archive.best()
    if not best_cid:
        return {"ok": False, "error": "存档为空，没有可应用的候选"}
    best = loop.archive.cands[best_cid]
    specs = best.payload.get("specs", [])
    if not specs:
        return {"ok": True, "run_id": run_id, "applied": 0,
                "note": "最优候选是空判据集（搜索没找到值得加的判据），无动作"}

    svc = _svc()
    import importlib
    # ★ 按当前领域包取资产模块：此前写死制造业，政务包里 evo_apply
    #   会拿制造业标注样本来做准入裁决。
    assets_mod = _ASSETS_MOD or importlib.import_module(
        "domains.manufacturing.assets")
    labeled = assets_mod.all_assets()
    comp = [{k: v for k, v in a.items() if not k.startswith("_")}
            for a in labeled if a.get("_label") == "compliant"]
    viol = [{k: v for k, v in a.items() if not k.startswith("_")}
            for a in labeled if a.get("_label") == "violating"]
    base_ck = svc._lib_checkers()

    from standard.compiler import Clause, ClauseKind
    adopted, rejected = [], []
    for spec in specs[:max_adopt]:
        try:
            # ★ 把候选的覆盖表一起带进判据 params ——
            #   此前只传 spec["params"]，`_table_override`（spec 上的兄弟键）**丢了**，
            #   于是候选池里的 -wide / -tight 变体**入库时都退化成 exact**：
            #   **判据 id 写着 -wide，行为却是 exact**，是典型的"看起来正常"。
            #   带上之后标签与行为才一致，R4（严格度不劣化）也才有对象可判。
            _par = dict(spec["params"])
            if spec.get("_table_override"):
                _par["_table_override"] = spec["_table_override"]
            cl = Clause(spec["clause_id"], "", ClauseKind(spec["kind"]),
                        spec["text"], _par, standard="（进化搜索产出）")
        except Exception as e:
            rejected.append({"clause_id": spec.get("clause_id"),
                             "reason": f"无法构造条款：{type(e).__name__}: {e}"})
            continue
        if svc.store.clauses.get(cl.clause_id):
            rejected.append({"clause_id": cl.clause_id, "reason": "库中已存在同编号判据"})
            continue
        j = svc._admit_clause(cl, comp, viol, base_ck)
        if j["verdict"] == "adopted":
            svc.store.add_clause(cl, status="adopted", evidence=j["stats"],
                                 reason=f"【进化采纳】{j['reason']}", origin="evolution")
            adopted.append(cl.clause_id)
        else:
            svc.store.add_clause(cl, status="rejected", evidence=j["stats"],
                                 reason=f"【进化驳回】{j['reason']}", origin="evolution")
            rejected.append({"clause_id": cl.clause_id, "reason": j["reason"]})
    svc.store.save()
    svc.store.log({"event": "evo_apply", "run_id": run_id, "by": by,
                   "adopted": adopted, "rejected": [x["clause_id"] for x in rejected]})
    return {"ok": True, "run_id": run_id, "best_candidate": best_cid,
            "n_proposed": len(specs), "n_adopted": len(adopted), "n_rejected": len(rejected),
            "adopted": adopted, "rejected": rejected,
            "library_size_after": len(svc.store.list_clauses("adopted")),
            "note": ("搜索提议、闸门裁决：**进化再强也不能绕过准入**。"
                     "被驳回的会以 rejected 状态留档，可审计。")}

def evo_step(run_id: str, n: int = 3) -> dict:
    """跑 n 代（每代毫秒级）。这是"绕开 60s 超时"的关键设计。"""
    r = RUNS.get(run_id)
    if r is None:
        return {"ok": False, "error": f"未知 run_id: {run_id}"}
    loop, dom = r["loop"], r["domain"]
    import random
    rng = random.Random(1000 + r["done"])
    for _ in range(max(1, min(n, 10))):
        if r["done"] >= r["budget"]:
            break
        parent_id = loop.archive.sample_parent(rng)
        child = dom.mutator(loop.archive.cands[parent_id], loop.archive, r["done"] + 1, rng)
        r["done"] += 1
        if child is None:
            loop._log_gen(r["done"], None)
            continue
        res = loop._evaluate(child)
        loop.archive.add(child, res)
        loop._log_gen(r["done"], res if res.valid else None)
    return {"ok": True, "run_id": run_id, "done": r["done"],
            "best": round(loop.archive.score(loop.archive.best()), 4),
            "curve_tail": loop.curve[-3:]}


def evo_status(run_id: str) -> dict:
    """返回代际曲线与当前最优。"""
    r = RUNS.get(run_id)
    if r is None:
        return {"ok": False, "error": f"未知 run_id: {run_id}"}
    loop = r["loop"]
    best = loop.archive.best()
    return {"ok": True, "run_id": run_id, "done": r["done"], "budget": r["budget"],
            "best_score": round(loop.archive.score(best), 4) if best else None,
            "curve": loop.curve}


def evo_finalize(run_id: str) -> dict:
    """出最终报告（含审计与保留集落差）。这一步可能略慢，但仍是秒级。"""
    r = RUNS.get(run_id)
    if r is None:
        return {"ok": False, "error": f"未知 run_id: {run_id}"}
    loop, heldout = r["loop"], r["heldout"]
    best = loop.archive.best()
    rep = loop._final_report()
    ev = loop.verifier.evaluate(loop.archive.cands[best])
    ho = heldout.evaluate(loop.archive.cands[best])
    rep["proxy_reality_gap"] = round(ev.score - ho.score, 4)
    rep["best_detail"] = {"cid": best, "payload": loop.archive.cands[best].payload}
    rep["evolve_metrics"] = ev.metrics
    rep["heldout_metrics"] = ho.metrics
    with open(os.path.join(r["out_dir"], "report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    return {"ok": True, "run_id": run_id, "report": rep}


# ---------------------------------------------------------------------------
#  MCP 装配
# ---------------------------------------------------------------------------

def multi_hop(asset_id: str, max_hops: int = 3) -> dict:
    """跨文档多跳知识推理：把一条资产的证据链逐跳摊开，每跳标注来源。

    跳数契约：
      0 资产本身 / 1 它引用的文档(source_doc_ids) / 2 它引用的标准(cited_standards)
      / 3 命中的条款+原文(判据库) / 4 同源的其他资产(共享文档/标准/设备号)
    无法核实的跳放进 gaps，不编造。

    入参：asset_id（asset_id / catalog_id 均可）+ max_hops（默认 3）。
    """
    # ★ 空 id 必须报错。此前 multi_hop(None) 返回
    #   {"ok": True, "n_hops": 3, "hops": [...]} 而每一跳都是空的 ——
    #   结构完整、内容全空、ok 还是 True，是典型的"看起来正常"。
    #
    # ★ 上面那段说明**曾经放在这个 if 之后** —— 于是它根本不是 docstring，
    #   只是一条永不执行的字符串表达式，`multi_hop.__doc__` 是 None。
    #   **一段长得完全像 docstring 的文字，因为位置差了几行就完全失效**，
    #   而读代码的人会以为它有说明。（见 docs/42 第 157 节）
    if asset_id is None or (isinstance(asset_id, str) and not asset_id.strip()):
        return {"ok": False, "error": "请提供资产标识（asset_id / catalog_id）"}
    try:
        # ★ 按**当前领域包**取资产：此前写死制造业，政务包里会列出制造业资产
        all_assets = (_ASSETS_MOD or __import__("domains.manufacturing.assets",
                      fromlist=["all_assets"])).all_assets
    except Exception as e:
        return {"ok": False, "error": f"领域包不可用: {e}"}
    raw = all_assets()
    # ★ id 字段按领域兜底：政务包用 catalog_id。此前只取 asset_id，
    #   政务包里键全是 None，于是 multi_hop 对任何政务资产都报"未知资产"。
    by_id = {(a.get("asset_id") or a.get("catalog_id")
              or a.get("asset_no") or a.get("id")): a for a in raw}
    me = by_id.get(asset_id)
    if me is None:
        return {"ok": False, "error": f"未知资产 {asset_id}", "hint": "先用 asset_list 取清单"}
    svc = _svc()
    hops, gaps = [], []
    hops.append({"hop": 0, "kind": "资产", "value": asset_id, "source": "领域包资产",
                 "fields": sorted(k for k in me if not k.startswith("_"))})
    docs = me.get("source_doc_ids") or []
    hops.append({"hop": 1, "kind": "引用的文档", "value": docs,
                 "source": "asset.source_doc_ids", "n": len(docs)})
    if not docs:
        gaps.append("未登记来源文档")
    stds = me.get("cited_standards") or []
    hops.append({"hop": 2, "kind": "引用的标准", "value": stds,
                 "source": "asset.cited_standards", "n": len(stds)})
    if not stds:
        gaps.append("未引用任何标准")
    clean = {k: v for k, v in me.items() if not k.startswith("_")}
    vs = svc._verifier().violations(clean, svc.ctx)
    hops.append({"hop": 3, "kind": "命中的条款", "source": "判据库(条款级溯源)",
                 "n_violations": len(vs),
                 "value": [{"clause_id": f.clause_id, "clause_text": f.clause_text,
                            "message": f.message, "evidence": f.evidence} for f in vs]})
    if not vs:
        gaps.append("当前无违例(可能落在判据盲区)——可用 asset_blindspots 复查")
    if max_hops >= 4:
        neigh = []
        for a in raw:
            aid = a.get("asset_id")
            if aid == asset_id:
                continue
            sd = sorted(set(a.get("source_doc_ids") or []) & set(docs))
            ss = sorted(set(a.get("cited_standards") or []) & set(stds))
            se = a.get("equipment_no") == me.get("equipment_no")
            if sd or ss or se:
                neigh.append({"asset_id": aid, "label": a.get("_label"), "shared_docs": sd,
                              "shared_standards": ss, "same_equipment_no": se,
                              "why": a.get("_why", "")})
        hops.append({"hop": 4, "kind": "同源的其他资产", "source": "共享文档/标准/设备号",
                     "n": len(neigh), "value": neigh[:10]})
        if not neigh:
            gaps.append("图上无邻居")
    # ★ 每条边都必须带 source —— decision-trace 技能是按「边」算 provenance 的，
    #   边没有 source 会被判「无来源」，整条链的采信强度降到「弱」。
    edges = []
    for d in docs:
        edges.append({"from": asset_id, "to": d, "op": "source_doc_ids",
                      "source": "asset.source_doc_ids",
                      "provenance": {"field": "source_doc_ids", "asset_id": asset_id}})
    for st in stds:
        edges.append({"from": asset_id, "to": st, "op": "cited_standards",
                      "source": "asset.cited_standards",
                      "provenance": {"field": "cited_standards", "asset_id": asset_id}})
    for f in vs:
        edges.append({"from": asset_id, "to": f.clause_id, "op": "clause_match",
                      "source": "判据库:" + f.source,
                      "provenance": {"clause_id": f.clause_id, "clause_text": f.clause_text,
                                     "evidence": f.evidence}})
    if max_hops >= 4:
        for nb in hops[-1].get("value", []):
            for key, op in (("shared_docs", "shared_doc"), ("shared_standards", "shared_standard")):
                for v in nb.get(key) or []:
                    edges.append({"from": asset_id, "to": nb["asset_id"],
                                  "op": f"{op}: {v}", "source": f"asset.{key}",
                                  "provenance": {"field": key, "value": v,
                                                 "asset_id": nb["asset_id"]}})
            if nb.get("same_equipment_no"):
                edges.append({"from": asset_id, "to": nb["asset_id"], "op": "same_equipment_no",
                              "source": "asset.equipment_no",
                              "provenance": {"field": "equipment_no",
                                             "value": me.get("equipment_no")}})
    return {"ok": True, "asset_id": asset_id, "n_hops": min(max_hops, 4),
            "hops": hops[: max_hops + 1], "edges": edges, "gaps": gaps,
            "traceable": not gaps,
            "note": "每跳与每条边都带 source 可核对；gap 表示该跳无来源，不做推断"}



def _kinds_in_use() -> dict:
    """★ 判据库里**真实在用**的类别 → 条数。从检查器现算，不读任何写死的列表。"""
    import collections
    try:
        c = collections.Counter(getattr(ck.clause.kind, "value", "?")
                                for ck in _svc()._lib_checkers())
        return dict(sorted(c.items()))
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def _kinds_diff() -> dict:
    """★ 写死的声明列表 vs 实际在用的类别。

    差异非空 = **描述语言的工具与实际语言不一致** —— 那本身就是一条要报的缺陷。
    """
    # ★ 声明列表**从枚举派生**，不再手写。
    #   教训：这里曾经手写过一份，**也漏了 `条件禁止`** ——
    #   修 `std_grammar` 里那个列表时才发现**写死的列表一共有两个**。
    #   而 `compiler.ClauseKind` / `grammar_v2.ClauseKind` 这两个枚举
    #   **一直是完整正确的（12 类）** —— 错的从来只是手抄的那两份。
    #   → 手抄必然漂，派生不会。**枚举是唯一真源。**
    try:
        from standard.compiler import ClauseKind as _CK
        declared = [m.value for m in _CK]
    except Exception:
        declared = []
    used = [k for k in _kinds_in_use() if not k.startswith("_")]
    return {
        "declared": declared,
        "used": used,
        "used_but_not_declared": sorted(set(used) - set(declared)),
        "declared_but_unused": sorted(set(declared) - set(used)),
        "consistent": set(used) == set(declared),
    }

def std_grammar() -> dict:
    """判据语言的当前版本与演进证据：三类新判据是否真的扩大了可分辨范围。

    返回 v1/v2 对照（盲区、误报、F1、硬锚点、覆盖率），全部由实验实跑得出，
    不是常量。任何调用方都能据此复核"语言扩张是否成立"。
    """
    try:
        from standard.grammar_v2_experiment import run as _run_v2
        r = _run_v2()
    except Exception as e:
        return {"ok": False, "error": f"实验不可用: {e}"}
    v1, v2 = r["v1"], r["v2"]
    return {
        "ok": True,
        "language_versions": {
            "v1": {"n_clauses": v1["n_clauses"],
                   # ★ v1 是 9 类，不是 8 类 —— 此前漏了 `条件禁止`。
                   #   `grammar_v2.py` 里写得很清楚：
                   #     FORBID_WHEN = ClauseKind.FORBID_WHEN  # 条件禁止（第4类，由编译错误逼出）
                   #   它是**被一次编译错误逼出来的 v1 类别**，不是 v2 新增的，
                   #   而这个写死的列表一直没跟上 —— 于是"描述语言的工具"少报了一类。
                   "kinds": ["数值范围", "受控词表", "格式约束", "必填约束",
                             "条件必填", "条件禁止", "跨字段一致性",
                             "跨文档引用", "禁止取值"]},
            "v2": {"n_clauses": v2["n_clauses"],
                   "kinds_added": ["基准时间约束", "查表区间", "集合级唯一性"]},
            # ★ 上面两个列表是**写死的**，而写死的列表会漂。
            #   实测：`条件禁止`（第30条在用）既不在 v1 的 8 类里，也不在 v2 的 +3 里 ——
            #   **描述语言的那个工具少报了语言里真实在用的一个类别。**
            #   所以这里同时给出「**实际在用**」的类别与两者的差异：
            #   只要不一致，`declared_vs_used.consistent` 就是 False —— **不可能再静默少报。**
            "actually_in_use": _kinds_in_use(),
            "declared_vs_used": _kinds_diff(),
        },
        "effect": {
            "blindspots": f"{len(v1['blindspots'])} -> {len(v2['blindspots'])}",
            "false_alarms": f"{len(v1['false_alarms'])} -> {len(v2['false_alarms'])}",
            "F1": f"{v1['F1']} -> {v2['F1']}",
            "regressions": len(r["regressions"]),
        },
        "conclusion": ("三类盲区被语言扩张消除，且零误报、零退化"
                       if not v2["blindspots"] and not v2["false_alarms"] and not r["regressions"]
                       else "尚有残留"),
        "note": "扩张的是判据语言，不是逐条打补丁；新增判据同时获得了机械锚点",
    }


def std_reference_time() -> dict:
    """基准时间的当前值与**来源**（基准时间约束类判据依赖它）。

    设计要点：基准时间必须**显式注入**，判据才允许用它。
    没有注入时判据会**弃权**（计入未行使清单），而不是拿系统时钟猜一个——
    否则判据就不再是纯函数，同输入不同输出，"可复现"就没了。
    """
    svc = _svc()
    now = svc.ctx.get("now")
    src = svc.ctx.get("now_source") or ("环境变量 EVOSTD_NOW" if os.environ.get("EVOSTD_NOW") else "未注入")
    affected = [c.clause_id for c in svc.store.all_clauses()
                if getattr(c.kind, "value", c.kind) == "基准时间约束"]
    return {"ok": True, "now": now, "source": src, "injected": bool(now),
            "affects_clauses": affected,
            "note": ("未注入时这些判据会弃权（std_audit 的 abstained 清单里可见），不会静默通过"
                     if not now else "已注入，判据生效")}


def std_conflicts(limit: int = 20) -> dict:
    """机械发现判据库内部的矛盾（标准自身不自洽时会出现）。"""
    return _svc().std_conflicts(limit=limit)


def std_amendments() -> dict:
    """把判据冲突变成**可执行的标准修订建议**（含语料原文与字符区间作为证据）。

    只生成建议、不落库——改标准是权威行为，不是算法行为。
    """
    return _svc().std_amendments()


def std_degradations(static_scan: bool = True) -> dict:
    """★ 降级台账：列出所有"悄悄走了回落路径"的地方。

    设计立场：**降级不是错，静默降级才是。** 回落是必须的（领域包坏了总不能让服务起不来），
    但每次回落都必须被记录、被计数、被报告。

    `static_scan=True` 时还会**静态扫描源码**里还剩多少个"不告知的 except"——
    这是结构性风险的度量，不会因为运行时没触发就变小。
    """
    from evostd import degrade as _dg
    snap = _dg.snapshot()
    if static_scan:
        try:
            import os as _os
            snap["static"] = _dg.count_swallow_sites(_os.path.dirname(_os.path.abspath(__file__)) + _os.sep + "..")
        except Exception as e:
            snap["static"] = {"error": f"{type(e).__name__}: {e}"}
    return snap


def std_ask(assets_json: str, top_k: int = 5) -> dict:
    """★ 主动提问：这批未标注资产里，**标哪几条信息量最大**（机械排序，不靠模型）。

    人的确认是最贵的真值来源；与其等人自己发现盲区，不如系统算出最该问的那几条。
    """
    assets = json.loads(assets_json) if (assets_json or "").strip() else []
    return _svc().std_ask(assets, top_k=top_k)


def std_answer(asset_json: str, verdict: str, by: str = "human", note: str = "") -> dict:
    """★ 闭环：人对 std_ask 的问题作答 → 系统吸收 → 立即分型并给出下一步。"""
    return _svc().std_answer(json.loads(asset_json), verdict, by=by, note=note)


def std_feedback() -> dict:
    """★ 汇总"人机不一致"的两个方向：盲区（判据偏松）与误伤（判据偏严）。"""
    return _svc().std_feedback()


def std_retire(clause_id: str, reason: str = "", by: str = "human",
               force: bool = False) -> dict:
    """★ 退役一条判据（准入的对称操作）。默认要求有危害证据。"""
    return _svc().std_retire(clause_id, reason=reason, by=by, force=force)


def _capability_fingerprint(names: list) -> str:
    """能力指纹：工具名排序后取哈希前 12 位。**消费者可据此核对版本是否一致**。"""
    import hashlib
    h = hashlib.sha256("\n".join(sorted(names)).encode("utf-8")).hexdigest()
    return h[:12]


def std_pending() -> dict:
    """★ 待裁决台账：**现在有几件事在等人**。

    为什么需要
    ----------
    系统里有好几处**故意停下来等人**的地方，而且都是刻意设计（不是缺陷）：
      · `std_conflicts`    检出标准真矛盾 —— "搜索无解，需人裁决"
      · `std_amendments`   生成修订建议 —— "是否修订、如何措辞**由人决定**"
      · `std_ask`          证据不足时向人提问
      · `std_degradations` 降级事件留档

    但**没有任何一处把它们汇总起来** —— 没人问就不会有人知道。

    ★ 本工具**只汇总、不裁决**。它不修改任何东西，也不建议怎么改。
    ★ 已经用 `std_adjudicate` 裁决过的条目**会被排除**，不再计入 n_pending。
    """
    svc = _svc()
    adj = svc.std_adjudications()["effective"]

    out = {"ok": True, "items": [], "n_pending": 0, "n_by_kind": {},
           "n_adjudicated": 0, "adjudicated": []}

    def _add(kind, n, detail, action, ref):
        if not n:
            return
        if ref in adj:                      # 已裁决 → 不再挂账
            out["n_adjudicated"] += 1
            out["adjudicated"].append({"ref": ref, "kind": kind,
                                       "decision": adj[ref]["decision"],
                                       "by": adj[ref].get("by"),
                                       "reason": adj[ref].get("reason", "")})
            return
        out["items"].append({"ref": ref, "kind": kind, "n": n,
                             "detail": detail, "action": action})
        out["n_by_kind"][kind] = n
        out["n_pending"] += n

    try:
        c = std_conflicts()
        rc = c.get("conflicts") or []
        refs = [x.get("anchor") for x in rc if x.get("class") == "A_标准自相矛盾"]
        ref = "conflict:" + (",".join(str(x) for x in refs) if refs else "none")
        _add("标准真矛盾", c.get("n_real_conflicts", 0),
             f"锚点自检发现 {c.get('n_real_conflicts',0)} 处两条判据不可同时满足",
             "看 std_conflicts 的 conflicts[].real_conflicts，决定改哪一条", ref)
    except Exception as e:
        out.setdefault("errors", []).append(f"conflicts: {type(e).__name__}")

    try:
        a = std_amendments()
        amds = a.get("amendments") or []
        ref = "amendment:" + (",".join(str(x.get("field")) for x in amds) if amds else "none")
        _add("待定修订建议", a.get("n_amendments", 0),
             "由条款冲突与误伤两条独立证据路径推导出的修订建议",
             "看 std_amendments 的 amendments[]，**措辞与是否修订由人决定**", ref)
        dbl = a.get("doubly_confirmed_clauses") or []
        _add("双证据确认条款", len(dbl), f"两条证据路径同时指向：{dbl[:5]}",
             "证据强度最高的一批，优先处理", "double:" + ",".join(map(str, dbl)))
    except Exception as e:
        out.setdefault("errors", []).append(f"amendments: {type(e).__name__}")

    try:
        d = std_degradations()
        _add("降级事件", d.get("n_incidents", 0),
             "功能降级留档 —— 保留旧结果而非报错",
             "看 std_degradations 确认每一处降级是否可接受", "degradation:all")
    except Exception as e:
        out.setdefault("errors", []).append(f"degradations: {type(e).__name__}")

    try:
        au = std_audit()
        # ★ 键名是 `abstained`，不是 `abstentions` —— 我原先写错了，
        #   于是这一项**永远取到 None**，"弃权判据"从来不出现。
        #   实测：真实值是 n_abstained_clauses=2（第45条、第46条各 2 条理由）。
        #   **一个键名写错，就让一整类待裁决事项静默消失。**
        ab = (au.get("abstained") or {})
        n_ab = ab.get("n_abstained_clauses", 0) if isinstance(ab, dict) else 0
        _add("弃权判据", n_ab, "判据主动声明'这种情况我不判'",
             "看 std_audit 的 abstentions，确认弃权理由是否成立", "abstention:all")
    except Exception as e:
        out.setdefault("errors", []).append(f"audit: {type(e).__name__}")

    out["note"] = ("**本台账只汇总'在等人'的事，不代表它们是缺陷** —— 多数是刻意设计。"
                   "裁决后条目会移入 `adjudicated`，不再计入 `n_pending`。")
    out["ok"] = not out.get("errors")
    return out


def std_adjudicate(ref: str, decision: str, by: str = "human", reason: str = "") -> dict:
    """★ 记录一次**人对待裁决事项的裁决**（只记录，不改标准/判据）。

    `ref` 取自 `std_pending()` 的 `items[].ref`；
    `decision` 取 `accepted` / `rejected` / `deferred`。
    """
    return _svc().std_adjudicate(ref, decision, by, reason)


def std_adjudications() -> dict:
    """列出全部裁决记录（追加式，同一 ref 以最后一次为准）。"""
    return _svc().std_adjudications()


def std_capability() -> dict:
    """★ 声明服务的能力指纹（**权威口径**）。

    背景：实测发现平台侧缓存的工具清单停留在 21 个，而服务实际提供 31 个 ——
    **平台界面显示一切正常，智能体却少调得到三分之一的能力，且不报错。**
    这是本项目第六个"看起来正常"的问题，而它是靠**手查数据库**才发现的。

    本工具让这件事**可机械核对**：服务声明自己的权威能力清单与指纹，
    任何消费者（平台、CI、人）都可以拿自己手上那份来对账。
    """
    # ★ TOOLS 里是函数对象（FastMCP 用函数名作工具名），不是 Tool 实例
    names = sorted(getattr(t, '__name__', str(t)) for t in TOOLS)
    return {"ok": True, "n_tools": len(names), "fingerprint": _capability_fingerprint(names),
            "tools": names,
            "note": ("这是**权威口径**。若调用方（平台注册表、文档、缓存的工具清单）"
                     "的数量或指纹与此不符，说明那份副本已过期——"
                     "**过期副本不会报错，只会让智能体少调到能力**。")}


def std_parity(claimed_tools: list) -> dict:
    """★ 能力对账：把**外部声称的工具清单**与服务权威清单做差。

    用途：平台重扫后核对、CI 里卡口径、文档与实现对账。
    `claimed_tools` 传工具名数组；只传数量时请用 `std_capability()` 自行比对。
    """
    mine = sorted(getattr(t, '__name__', str(t)) for t in TOOLS)
    claimed = sorted({str(x) for x in (claimed_tools or [])})
    missing = [x for x in mine if x not in claimed]      # 服务有、对方没有 → 对方少能力
    extra = [x for x in claimed if x not in mine]        # 对方有、服务没有 → 对方多出（可能是别的 MCP）
    return {"ok": True, "authoritative": {"n_tools": len(mine),
                                          "fingerprint": _capability_fingerprint(mine)},
            "claimed": {"n_tools": len(claimed),
                        "fingerprint": _capability_fingerprint(claimed)},
            "in_sync": not missing,
            "n_missing_on_claimed_side": len(missing),
            "n_extra_on_claimed_side": len(extra),
            "missing": missing, "extra": extra,
            "verdict": ("一致" if not missing else
                        f"★ 对方清单**缺 {len(missing)} 个服务端能力**——"
                        f"它调不到这些工具，而且不会报错"),
            "next_action": ("" if not missing else
                            "在平台侧对该 MCP **禁用→启用**（重新扫描），使其重新拉取工具清单")}

TOOLS = [std_clause_list, std_verify, std_compliance, std_hard_anchors, std_propose,
         std_confirm, std_audit, std_capability, std_parity, std_degradations, std_pending, std_adjudicate, std_adjudications, std_grammar, std_conflicts, std_ask, std_answer, std_feedback, std_amendments, std_reference_time, std_ledger, std_snapshot, std_rollback, std_retire,
         asset_list, multi_hop, asset_blindspots, asset_lineage, asset_ingest, asset_search,
         evo_init, evo_step, evo_status, evo_finalize, evo_apply, evo_archive]


def build_mcp(host: str = "127.0.0.1", port: int = 8765):
    """构造 FastMCP server。未安装 fastmcp 时给出明确指引。"""
    try:
        from fastmcp import FastMCP
    except Exception as exc:  # pragma: no cover
        raise SystemExit(
            "未安装 fastmcp。请先：\n"
            "  uv venv --python 3.11 .venv-evostd\n"
            "  uv pip install fastmcp --python .venv-evostd/Scripts/python.exe\n"
            f"（原始错误：{exc}）")
    mcp = FastMCP(name="evostd", instructions=(
        "标准驱动的自进化服务。std_* 是外部真值与裁决（agent 改不了）；"
        "evo_* 是分片的进化回路；asset_* 是数据资产侧诊断。"))
    for fn in TOOLS:
        mcp.tool()(fn)
    return mcp


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--domain", default="manufacturing")
    ap.add_argument("--state", default=DEFAULT_STATE_DIR)
    args = ap.parse_args()

    svc = build_service(args.domain, args.state)
    print(f"[evostd] 服务已装配：领域={args.domain} 状态目录={args.state}", file=sys.stderr)
    print(f"[evostd] 判据库 {svc.store.stats()}", file=sys.stderr)

    mcp = build_mcp(args.host, args.port)
    # ★ transport="http"（FastMCP 2.14 的 streamable-http），URL 路径 /mcp
    #   Nexent 靠 URL 后缀判定传输类型：/sse → sse，其余（含 /mcp）→ streamable-http
    mcp.run(transport="http", host=args.host, port=args.port, path="/mcp")


if __name__ == "__main__":
    main()
