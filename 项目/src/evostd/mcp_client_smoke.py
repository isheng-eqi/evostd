"""MCP 客户端冒烟：起服务 → 连上去 → 列工具 → 真调一个工具 → 关掉。

用 Python 3.11 venv 跑（那里同时有 fastmcp 和 mcp）：
    .venv-nexent311\\Scripts\\python.exe src\\evostd\\mcp_client_smoke.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

URL = os.environ.get("EVOSTD_URL", "http://127.0.0.1:8770/mcp")


async def main() -> int:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    print(f"[client] 连接 {URL}")
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"[client] 已握手：server={init.serverInfo.name} "
                  f"version={init.serverInfo.version}")
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"[client] 工具数 = {len(names)}")
            for t in tools.tools:
                desc = (t.description or "").strip().splitlines()[0][:52]
                print(f"    · {t.name:<20} {desc}")
            print("\n[client] 实调 std_audit：")
            r = await session.call_tool("std_audit", {})
            payload = json.loads(r.content[0].text)
            cov = payload["coverage"]
            print(f"    硬锚点覆盖率 = {cov['hard_anchor_coverage']:.4f} "
                  f"({cov['clauses_with_hard_anchors']}/{cov['clauses_compilable']})")
            print(f"    锚点自检     = {payload['anchor_selfcheck']['verdict']}")
            print(f"    对抗探针     = "
                  f"{ {k: v['verdict'][:8] for k, v in payload['probes'].items()} }")

            print("\n[client] 实调 evo_init + evo_step（验证分片绕开 60s 超时）：")
            r = await session.call_tool("evo_init", {"domain": "ontology",
                                                     "generations": 10})
            d = json.loads(r.content[0].text)
            rid = d["run_id"]
            print(f"    {rid} 种子分 = {d['seed_score']}")
            r = await session.call_tool("evo_step", {"run_id": rid, "n": 5})
            d = json.loads(r.content[0].text)
            print(f"    跑 {d['done']} 代后最优 = {d['best']}")
    print("\n[client] PASS：MCP 通道可用，工具可列可调。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
