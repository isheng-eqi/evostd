"""工具活性检查：**哪些工具从未被任何可执行脚本调用过**。

为什么需要
----------
`clause_liveness.py` 查的是「判据会不会触发」。
工具也有同样的问题，而且更隐蔽：**文档里提到过 ≠ 被跑过**。

实测（34 个工具）立刻抓到 4 个：

    std_grammar            只出现在 42-*.md 里
    std_parity             只出现在 42-*.md 里
    std_pending            只出现在 42-*.md 里     ← 新加的工具，一加就落进这个坑
    std_reference_time     只出现在 42-*.md 里

**四个都正常** —— 但"从未被引用"不等于"坏了"，
**只是在有人调用它之前，没有任何人知道它好不好。**

这正是"存在但未验证"的第四种形态：

| 形态 | 检查手段 |
|---|---|
| 判据"存在"≠"能触发" | `clause_liveness.py` |
| 技能"绑上了"≠"能跑" | 静态校验（结构 + 工具引用） |
| 快照"有接口"≠"能退回" | `snapshot_roundtrip.py` |
| **工具"注册了"≠"被跑过"** | **本脚本** |

用法
----
    python tools/tool_liveness.py          # 静态：列出从未被引用的工具
    python tools/tool_liveness.py --call   # 额外：把**无必需参数**的工具真的调一次

★ `--call` 只调**参数全是可选**的工具（等价于"空参数调用"），
  不碰任何需要业务入参的工具 —— 那需要各自的构造数据。
"""
from __future__ import annotations

import inspect
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")


def _tool_names(domain: str = "manufacturing"):
    os.environ.setdefault("EVOSTD_NOW", "2026-10-13")
    os.environ["EVOSTD_STATE"] = os.path.join(_SRC, "runs", "toollive")
    sys.path.insert(0, _SRC)
    import evostd.server as S                       # ★ env 设好之后再 import

    S.build_service(domain)
    return S, sorted(x.__name__ for x in S.TOOLS)


def static_scan(tools: list[str]) -> dict:
    """每个工具被多少个**可执行文件**引用（.md 不算）。"""
    refs: dict[str, list[str]] = {t: [] for t in tools}
    for dp, dn, fn in os.walk(_ROOT):
        if any(x in dp for x in ("\\runs", ".venv", "__pycache__", "\\submission")):
            continue
        for f in fn:
            if not f.endswith((".py", ".sh")):      # ★ 不把文档算成"被使用"
                continue
            p = os.path.join(dp, f)
            if os.path.basename(p) == "server.py":  # 定义处不算
                continue
            try:
                txt = open(p, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for t in tools:
                # ★ 只认**调用**（name 后面跟着 `(`），不认**提及**。
                #   否则脚本在 docstring 里列一遍工具名，就把自己的发现抹掉了 ——
                #   实测踩到：静态扫报 4 个"从未被引用"，新建本脚本后变成 1 个，
                #   **数字变了不是因为有什么变了，而是因为我"写了它们"。**
                # ★ 两种调用形式都要认：
                #   ① 直接调用     foo(...)
                #   ② 字符串派发   call("foo", ...) / getattr(S, "foo")
                #   只认 ① 会让本检查**看不见**用 ② 写的冒烟脚本 ——
                #   实测踩到：`smoke_all_tools.py` 已覆盖全部工具，本检查却仍报 15 个"没人调用"。
                if (re.search(r"\b%s\s*\(" % re.escape(t), txt)
                        or re.search(r"""["']%s["']""" % re.escape(t), txt)):
                    refs[t].append(os.path.relpath(p, _ROOT))
    never = sorted(t for t, v in refs.items() if not v)
    return {"refs": refs, "never_referenced": never}


def call_optional(S, tools: list[str]) -> dict:
    """把**无必需参数**的工具真的调一次。"""
    called, failed, skipped = [], [], []
    for t in tools:
        f = getattr(S, t, None)
        if f is None:
            failed.append({"tool": t, "why": "函数不存在"}); continue
        try:
            need = [p.name for p in inspect.signature(f).parameters.values()
                    if p.default is inspect._empty
                    and p.kind in (p.POSITIONAL_OR_KEYWORD, p.POSITIONAL_ONLY)]
        except (TypeError, ValueError):
            skipped.append({"tool": t, "why": "无法取签名"}); continue
        if need:
            skipped.append({"tool": t, "why": f"需要入参 {need}"}); continue
        try:
            r = f()
            called.append({"tool": t, "ok": (r.get("ok") if isinstance(r, dict) else None)})
        except Exception as e:
            failed.append({"tool": t, "why": f"{type(e).__name__}: {str(e)[:70]}"})
    return {"called": called, "failed": failed, "skipped": skipped}


def main() -> int:
    do_call = "--call" in sys.argv
    S, tools = _tool_names()
    st = static_scan(tools)

    print("=" * 78)
    print("工具活性检查")
    print("=" * 78)
    print(f"  工具总数 {len(tools)} | **从未被任何可执行脚本引用** {len(st['never_referenced'])}")
    for t in st["never_referenced"]:
        docs = [f for f in os.listdir(os.path.join(_ROOT, "docs"))
                if f.endswith(".md") and re.search(r"\b%s\b" % re.escape(t),
                        open(os.path.join(_ROOT, "docs", f), encoding="utf-8", errors="ignore").read())]
        where = f"（仅出现在文档：{','.join(docs[:2])}）" if docs else "（**任何地方都没提到**）"
        print(f"    {t:<24}{where}")

    rc = 2 if st["never_referenced"] else 0

    if do_call:
        r = call_optional(S, tools)
        print()
        print(f"  空参数实际调用：成功 {len(r['called'])} | 失败 {len(r['failed'])} "
              f"| 跳过（需入参）{len(r['skipped'])}")
        for c in r["called"]:
            flag = "" if c["ok"] is not False else "  ★ ok=False"
            print(f"    ✓ {c['tool']:<24}ok={c['ok']}{flag}")
        for c in r["failed"]:
            print(f"    ✗ {c['tool']:<24}{c['why']}")
        if r["failed"]:
            rc |= 4

    print()
    print("  注意：`从未被引用` ≠ `坏了` —— 也可能是没有任何脚本需要它。")
    print("        但在有人调用它之前，**没有任何人知道它好不好**。")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
