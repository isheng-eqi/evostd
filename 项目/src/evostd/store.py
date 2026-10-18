"""自持存储：判据库 + 锚点 + 台账 + 版本快照。

为什么不用 Nexent 平台的表（来自 docs/02、03 的实测）：
  · 平台 skill 表**没有 version 字段**，`version_no` 冻结的是"绑定关系"而不是"内容"
  · 平台的证照类字段（authorization_token / custom_headers）**明文入库**
  · 而我们的判据库需要**内容级版本 + 可回滚 + 可审计**

所以：判据库、锚点、台账全部自持，落在 MCP Server 自己的目录里。
平台侧只负责"把调用送到这里"。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from standard.compiler import Clause, ClauseKind


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def clause_to_dict(c: Clause) -> dict:
    return {"clause_id": c.clause_id, "title": c.title, "kind": c.kind.value,
            "text": c.text, "params": c.params, "standard": c.standard,
            "compilable": c.compilable}


def clause_from_dict(d: dict) -> Clause:
    return Clause(clause_id=d["clause_id"], title=d.get("title", ""),
                  kind=ClauseKind(d["kind"]), text=d.get("text", ""),
                  params=d.get("params", {}), standard=d.get("standard", ""),
                  compilable=d.get("compilable", True))


@dataclass
class ClauseRecord:
    spec: dict
    status: str = "adopted"          # adopted | rejected | proposed
    evidence: dict = field(default_factory=dict)
    reason: str = ""
    added_at: str = ""
    origin: str = "standard"         # standard | system_proposed


class Store:
    """一个目录 = 一份自持状态。可快照、可回滚、可审计。"""

    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.clauses_path = os.path.join(root, "clauses.json")
        self.anchors_path = os.path.join(root, "anchors.json")
        self.ledger_path = os.path.join(root, "ledger.jsonl")
        self.snap_dir = os.path.join(root, "snapshots")
        os.makedirs(self.snap_dir, exist_ok=True)

        self.clauses: dict[str, ClauseRecord] = {}
        self.anchors: list[dict] = []
        self._load()

    # ---- 持久化 ----
    def _load(self) -> None:
        if os.path.exists(self.clauses_path):
            raw = json.load(open(self.clauses_path, encoding="utf-8"))
            self.clauses = {k: ClauseRecord(**v) for k, v in raw.items()}
        if os.path.exists(self.anchors_path):
            self.anchors = json.load(open(self.anchors_path, encoding="utf-8"))

    def save(self) -> None:
        json.dump({k: asdict(v) for k, v in self.clauses.items()},
                  open(self.clauses_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        json.dump(self.anchors, open(self.anchors_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)

    # ---- 台账（追加式，永不修改历史）----
    def log(self, event: dict) -> None:
        ev = dict(event)
        ev.setdefault("t", _now())
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    def ledger(self, limit: int = 50) -> list[dict]:
        if not os.path.exists(self.ledger_path):
            return []
        lines = open(self.ledger_path, encoding="utf-8").read().strip().splitlines()
        return [json.loads(x) for x in lines[-limit:]]

    # ---- 判据库 ----
    def add_clause(self, clause: Clause, status: str = "adopted",
                   evidence: Optional[dict] = None, reason: str = "",
                   origin: str = "standard") -> ClauseRecord:
        rec = ClauseRecord(spec=clause_to_dict(clause), status=status,
                           evidence=evidence or {}, reason=reason,
                           added_at=_now(), origin=origin)
        self.clauses[clause.clause_id] = rec
        return rec

    def retire_clause(self, clause_id: str, reason: str = "", by: str = "human") -> dict:
        """★ 退役一条判据：**准入的对称操作**。

        系统此前只有准入（R1/R2/R3），没有退役——一条判据一旦入库就永远在，
        即使它后来不断误伤**人确认过的合规资产**。
        而一条总在误伤的判据**降低**系统的分辨能力，不是提高。

        退役 = 把 status 改成 `retired`（`adopted()` 因此不再返回它），
        **记录保留**，可审计、可复活。与"删除"不同：删除会让历史断掉。
        """
        rec = self.clauses.get(clause_id)
        if rec is None:
            return {"ok": False, "error": f"库中没有判据 {clause_id}"}
        old = rec.status
        rec.status = "retired"
        rec.reason = (f"【退役】{reason}（原状态 {old}，由 {by} 决定）")
        self.log({"event": "retire_clause", "clause_id": clause_id,
                  "from": old, "reason": reason, "by": by})
        self.save()
        return {"ok": True, "clause_id": clause_id, "from": old, "to": "retired"}

    def revive_clause(self, clause_id: str, reason: str = "", by: str = "human") -> dict:
        """复活性命：退役不是永久判决，证据变了可以复活。"""
        rec = self.clauses.get(clause_id)
        if rec is None:
            return {"ok": False, "error": f"库中没有判据 {clause_id}"}
        rec.status = "adopted"
        rec.reason = f"【复活】{reason}（由 {by} 决定）"
        self.log({"event": "revive_clause", "clause_id": clause_id, "by": by})
        self.save()
        return {"ok": True, "clause_id": clause_id, "to": "adopted"}

    def adopted(self) -> list[Clause]:
        return [clause_from_dict(r.spec) for r in self.clauses.values()
                if r.status == "adopted"]

    def list_clauses(self, state: Optional[str] = None) -> list[dict]:
        out = []
        for cid, r in sorted(self.clauses.items()):
            if state and r.status != state:
                continue
            out.append({"clause_id": cid, "status": r.status, "origin": r.origin,
                        "title": r.spec.get("title", ""), "kind": r.spec.get("kind"),
                        "text": r.spec.get("text", ""), "reason": r.reason,
                        "evidence": r.evidence, "added_at": r.added_at})
        return out

    # ---- 锚点 ----
    def add_anchors(self, anchors: list[dict]) -> int:
        # 去重键必须带上资产内容：否则两条内容不同的人工确认会被误判为重复
        have = {(a["source_clause"], a["how"],
                 json.dumps(a.get("asset", {}), sort_keys=True, ensure_ascii=False))
                for a in self.anchors}
        added = 0
        for a in anchors:
            key = (a["source_clause"], a["how"],
                   json.dumps(a.get("asset", {}), sort_keys=True, ensure_ascii=False))
            if key not in have:
                self.anchors.append(a)
                have.add(key)
                added += 1
        return added

    def drop_clauses_by_origin(self, origin: str) -> int:
        """删除指定来源的判据（用于"以领域包为准"重播标准判据）。

        ★ 只删 origin == origin 的，**保留**人工确认/进化产出的判据——那是学习成果。
        """
        drop = [k for k, r in self.clauses.items()
                if getattr(r, "origin", "") == origin]
        for k in drop:
            del self.clauses[k]
        return len(drop)

    def reset_anchors_from_standard(self) -> int:
        """清掉机械抽取的硬锚点（它们会随领域包重新抽取）；保留人工确认的软锚点。"""
        keep = [a for a in self.anchors if a.get("kind") != "hard"]
        n = len(self.anchors) - len(keep)
        self.anchors = keep
        return n

    def all_clauses(self) -> list[Clause]:
        """全部条款（含不可编译的留档），用于覆盖率统计。"""
        return [clause_from_dict(r.spec) for r in self.clauses.values()
                if r.status in ("adopted", "uncompilable")]

    def anchors_by_kind(self, kind: Optional[str] = None) -> list[dict]:
        return [a for a in self.anchors if kind is None or a.get("kind") == kind]

    # ---- 版本快照 / 回滚（内容级，平台做不到）----
    def snapshot(self, label: str) -> dict:
        sid = f"SNAP-{int(time.time())}"
        payload = {"clauses": {k: asdict(v) for k, v in self.clauses.items()},
                   "anchors": self.anchors, "label": label, "t": _now()}
        json.dump(payload, open(os.path.join(self.snap_dir, sid + ".json"), "w",
                                encoding="utf-8"), ensure_ascii=False, indent=2)
        self.log({"event": "snapshot", "id": sid, "label": label,
                  "n_clauses": len(self.clauses)})
        return {"snapshot_id": sid, "label": label, "n_clauses": len(self.clauses)}

    def rollback(self, snapshot_id: str) -> dict:
        p = os.path.join(self.snap_dir, snapshot_id + ".json")
        if not os.path.exists(p):
            return {"ok": False, "error": f"快照不存在: {snapshot_id}"}
        payload = json.load(open(p, encoding="utf-8"))
        self.clauses = {k: ClauseRecord(**v) for k, v in payload["clauses"].items()}
        self.anchors = payload["anchors"]
        self.save()
        self.log({"event": "rollback", "to": snapshot_id,
                  "n_clauses": len(self.clauses)})
        return {"ok": True, "snapshot_id": snapshot_id, "label": payload.get("label"),
                "n_clauses": len(self.clauses)}

    def snapshots(self) -> list[dict]:
        out = []
        for fn in sorted(os.listdir(self.snap_dir)):
            if fn.endswith(".json"):
                d = json.load(open(os.path.join(self.snap_dir, fn), encoding="utf-8"))
                out.append({"snapshot_id": fn[:-5], "label": d.get("label"),
                            "t": d.get("t"), "n_clauses": len(d.get("clauses", {}))})
        return out

    # ---- 概览 ----
    def stats(self) -> dict:
        st: dict[str, int] = {}
        for r in self.clauses.values():
            st[r.status] = st.get(r.status, 0) + 1
        return {"clauses": len(self.clauses), "by_status": st,
                "anchors": len(self.anchors),
                "anchors_hard": len(self.anchors_by_kind("hard")),
                "anchors_soft": len(self.anchors_by_kind("soft")),
                "snapshots": len(self.snapshots()),
                "root": self.root}
