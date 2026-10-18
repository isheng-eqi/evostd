"""领域包（Domain Pack）：内核不变，换行业 = 换一个包（docs/10）。

可插拔适配层应实现四样东西：
    corpus()          语料（多模态）
    standards()       要与之对齐的标准体系
    build_verifier()  ★ 外部、确定性、可 held-out 的验证器
    narrative()       业务价值叙事（给人看的）

自进化内核不依赖任何具体行业；行业只提供回路的**目标函数**。
"""
from __future__ import annotations

__all__ = ["manufacturing"]
