"""evostd：标准驱动的自进化服务层（MCP Server 的业务内核）。

分层：
    standard/    标准编译器（条款 → 可执行判据）
    evolve/      自进化内核（候选/闸门/存档/审计/台账）
    evostd/      ★ 本包：把上面两者组装成一个"agent 够不着"的服务
        store.py    自持存储（判据库 + 锚点 + 台账 + 版本快照）
        anchors.py  硬锚点机械抽取器（P0 杠杆）
        service.py  std_* / evo_* / asset_* 的实现
        server.py   MCP 适配层（FastMCP，streamable-http）
        smoke.py    离线冒烟测试（不需要 MCP、不需要 Nexent）
"""
from .anchors import HardAnchor, coverage, derive_hard_anchors, verify_anchors  # noqa: F401
from .service import StandardService  # noqa: F401
from .store import Store, clause_from_dict, clause_to_dict  # noqa: F401

__all__ = ["Store", "StandardService", "HardAnchor", "derive_hard_anchors",
           "verify_anchors", "coverage", "clause_to_dict", "clause_from_dict"]
