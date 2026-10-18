"""假 docstring 检查：找出**不是函数体第一条语句**的字符串字面量。

为什么需要
----------
`multi_hop` 曾经长这样（实测，见 docs/42 第 157 节）：

    def multi_hop(asset_id, max_hops=3):
        # ★ 空 id 必须报错。...
        if asset_id is None or (...):
            return {"ok": False, ...}
        \"\"\"跨文档多跳知识推理：把一条资产的证据链逐跳摊开，每跳标注来源。

        跳数契约：0 资产本身 / 1 它引用的文档 / 2 它引用的标准 ...
        \"\"\"

**那段 `\"\"\"...\"\"\"` 在 `if` 之后** —— 它前面已经有代码了，
所以它**不是 docstring，只是一条永不执行的字符串表达式**。`__doc__` 是 `None`。

**为什么它危险**
  · 读代码的人**会以为它有说明**（贴着函数开头，格式、语气、内容都对）
  · **不影响任何行为** —— 功能测试永远抓不到
  · 只有在**有人去消费 `__doc__`** 时才会暴露
    （本项目是被"程序生成工具索引"这个动作照出来的）

**这是逻辑检查覆盖不到的一类**：它属于"元数据与代码脱节"，
而不是"代码算错了"。

用法
----
    python tools/fake_docstring.py          # 扫全项目
    python tools/fake_docstring.py --fix-hint

★ 只**报位置**，不自动修 —— docstring 该放哪、写什么，是人的判断。
"""
from __future__ import annotations

import ast
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKIP = ("\\runs", ".venv", "__pycache__", "\\submission", "/runs", "/.venv", "/submission")


def scan(root: str = _ROOT) -> list[dict]:
    hits = []
    for dp, dn, fn in os.walk(root):
        if any(x in dp for x in _SKIP):
            continue
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            try:
                tree = ast.parse(open(p, encoding="utf-8").read())
            except Exception:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for i, st in enumerate(node.body):
                    if (isinstance(st, ast.Expr)
                            and isinstance(st.value, ast.Constant)
                            and isinstance(st.value.value, str)):
                        if i != 0:
                            hits.append({
                                "file": os.path.relpath(p, root),
                                "func": node.name,
                                "line": st.lineno,
                                "first": st.value.value.strip().split("\n")[0][:70],
                            })
                        break        # 每个函数只看第一个字符串语句
    return hits


def main() -> int:
    hits = scan()
    print("=" * 78)
    print("假 docstring 检查（不是函数体第一条语句的字符串字面量）")
    print("=" * 78)
    if not hits:
        print("  ✓ 0 处 —— 所有字符串说明都真的位于函数体第一行，即真的是 docstring")
        print()
        print("  说明：本检查只覆盖「字符串语句不在第一条」这一种形态。")
        print("        「docstring 写错了内容」不在检查范围内 —— 那要靠人读。")
        return 0
    print(f"  ★ {len(hits)} 处：这些文字**看起来像 docstring，实际不是**，`__doc__` 取不到")
    for h in hits:
        print(f"    {h['file']}:{h['line']}  {h['func']}()")
        print(f"        内容：{h['first']}")
    print()
    print("  修法：把该字符串移到函数体第一行（注释之后、任何语句之前）。")
    print("        若因为前面有守卫语句而无法移动，就把它改成普通注释 `#`。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
