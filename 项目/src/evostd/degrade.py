"""降级台账：**让每一次"悄悄降级"都留下痕迹**。

为什么需要
----------
全仓 38 个 `except`，其中 **30 个只打一行日志或干脆不吭声**。
这不是个别 bug，是结构性风险——实测已经因此连踩三次：

  · `HardAnchorV2` 没有 `to_dict()` → `AttributeError` 被吞 → **锚点块完全没生效**
  · 扩张判据的锚点 id 撞号 → 被去重静默丢掉 → 锚点 103 → 87
  · 领域包 import 失败走回落 → 判据库沿用旧的 → **验的根本不是这个领域**

共同点：**不报错、不崩、数字照常好看，只是结果不对。**

设计立场
--------
> **降级不是错，静默降级才是。**

回落是必须的（领域包坏了总不能让服务起不来）。但每一次回落都必须：
  1. **被记录**（在哪儿、什么异常、退到了什么）
  2. **被计数**
  3. **被报告**（`std_degradations` 工具 + 审计里可见）

与"弃权必须可见"是同一条纪律的延伸：
**"这条查过了"和"这条根本没查"长得一样，所以必须显式区分。**
"""
from __future__ import annotations

import traceback
from datetime import datetime, timezone
from typing import Any, Optional

# 进程级台账（服务是单进程的；跨重启的持久化由 Store.ledger 负责）
_INCIDENTS: list[dict] = []

# 严重度：
#   judgment  —— 影响判定结果（判据没跑成 / 锚点没抽出来）★ 最危险
#   capability—— 少了一个能力（工具不可用），但判定本身还可靠
#   cosmetic  —— 只影响展示
SEVERITY_JUDGMENT = "judgment"
SEVERITY_CAPABILITY = "capability"
SEVERITY_COSMETIC = "cosmetic"


def note(where: str, exc: BaseException, *, fallback: str = "",
         severity: str = SEVERITY_CAPABILITY, store=None) -> dict:
    """记录一次降级。**必须在 except 分支里调用**，替代"只 print 一行"。"""
    rec = {
        "t": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "where": where,
        "exc": f"{type(exc).__name__}: {exc}",
        "fallback": fallback,
        "severity": severity,
        "trace": traceback.format_exc(limit=3).splitlines()[-3:],
    }
    _INCIDENTS.append(rec)
    try:
        if store is not None:
            store.log({"event": "degradation", **rec})
    except Exception:
        pass                                   # 记录失败本身不能再引发降级
    return rec


def snapshot() -> dict:
    """当前进程的降级台账。"""
    by_sev: dict[str, int] = {}
    for r in _INCIDENTS:
        by_sev[r["severity"]] = by_sev.get(r["severity"], 0) + 1
    return {
        "ok": True,
        "n_incidents": len(_INCIDENTS),
        "by_severity": by_sev,
        "n_judgment": by_sev.get(SEVERITY_JUDGMENT, 0),
        "incidents": _INCIDENTS[-30:],
        "verdict": ("★ 有影响判定结果的降级发生——当前结论不可直接采信"
                    if by_sev.get(SEVERITY_JUDGMENT)
                    else ("有非判定类降级（能力/展示），判定本身可靠"
                          if _INCIDENTS else "无降级，全部路径正常")),
        "note": ("降级不是错，静默降级才是。本清单就是「静默」的解药："
                 "凡是走了回落路径的，都会在这里出现。"),
    }


def reset() -> None:
    _INCIDENTS.clear()


def count_swallow_sites(src_root: str) -> dict:
    """静态扫描"不告知的 except"。

    ★ 度量口径修正（实测踩到自己的误报）
    ------------------------------------
    第一版只看后三行有没有 `print/note/log/raise` ——
    于是**把"返回 `{"ok": False, "error": ...}`"的那些也算成静默了**，
    实测把 18 处真静默报成了 35 处（**接近 2 倍**）。

    **用有偏差的尺子量自己，得到的就是一个"看起来合理"的错数字。**

    现在分三类报，不再合成一个含糊的总数：
      · `silent`   —— `continue` / `pass`，**既不告知也不报告失败** ★ 真正要盯的
      · `reported` —— 有返回值告知（`{"ok": False, ...}` 等），调用方能看见
      · `other`    —— 兜底分支（如 `return None`），需个案判断
    """
    import os
    import re
    total = 0
    silent: list[str] = []
    reported: list[str] = []
    other: list[str] = []
    for dirpath, _, files in os.walk(src_root):
        if "__pycache__" in dirpath or os.sep + "runs" in dirpath:
            continue
        for fn in files:
            if not fn.endswith(".py") or fn in ("degrade.py",) \
                    or fn.endswith("_experiment.py"):
                continue
            p = os.path.join(dirpath, fn)
            lines = open(p, encoding="utf-8").read().split("\n")
            for i, l in enumerate(lines):
                if not re.match(r"^\s*except\b", l):
                    continue
                total += 1
                site = f"{fn}:{i+1}"
                body = "\n".join(lines[i + 1:i + 7])
                if re.search(r"print|note\(|log\(|raise|_degrade", body):
                    continue                       # 已告知，不计入任何一类
                if re.search(r'return\s+\{[^}]*"(ok|error|message)"', body) or \
                        re.search(r"return\s+(False)\s*$", body, re.M):
                    reported.append(site)
                elif re.search(r"^\s*(continue|pass)\s*$", body, re.M):
                    silent.append(site)
                else:
                    other.append(site)
    n_sil = len(silent)
    return {"n_except": total,
            "n_silent": n_sil,
            "n_reported": len(reported),
            "n_other": len(other),
            "rate": round(n_sil / total, 4) if total else 0.0,
            "silent": silent[:20],
            "reported": reported[:12],
            "other": other[:12],
            "note": ("`n_silent` 才是真正要盯的数字：**既不告知、也不报告失败**。"
                     "早先版本把 `reported` 也算进去，实测把 18 报成了 35。")}
