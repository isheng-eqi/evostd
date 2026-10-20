"""生成「智能体调用关系图」SVG（赛题材料里明确要求的一张图）。

为什么单独画
------------
Nexent 的配置页能截到"工具列表"，但截不到"谁调用谁、判定权在哪一层"。
这张图只表达一件事：**模型在上层编排，判定在下层 MCP，检索在右侧知识库**。

用法：
    python tools/agent_callgraph.py --out submission/agent-callgraph.svg
"""
from __future__ import annotations

import argparse
import html
import os


def esc(s: str) -> str:
    return html.escape(s)


def box(x, y, w, h, lines, fill, stroke, fs=14, title_fs=15):
    """多行文本框：第一行加粗大一号。"""
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" '
           f'stroke="{stroke}" stroke-width="1.6"/>']
    n = len(lines)
    total = sum((title_fs if i == 0 else fs) + 6 for i in range(n))
    cy = y + h / 2 - total / 2
    for i, line in enumerate(lines):
        size = title_fs if i == 0 else fs
        weight = "700" if i == 0 else "400"
        cy += size
        out.append(f'<text x="{x + w / 2}" y="{cy}" text-anchor="middle" font-size="{size}" '
                   f'font-weight="{weight}" fill="#1f2328">{esc(line)}</text>')
        cy += 6
    return "".join(out)


def arrow(x1, y1, x2, y2, label="", dash=False, color="#8c959f"):
    dash_attr = ' stroke-dasharray="6 4"' if dash else ''
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    lab = (f'<text x="{mx}" y="{my - 7}" text-anchor="middle" font-size="12" '
           f'fill="#57606a">{esc(label)}</text>') if label else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
            f'stroke-width="1.7"{dash_attr} marker-end="url(#a)"/>' + lab)


def build() -> str:
    W, H = 1200, 820
    b = [('<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
          'markerHeight="7" orient="auto-start-reverse">'
          '<path d="M0,0 L10,5 L0,10 z" fill="#8c959f"/></marker></defs>')]
    b.append('<text x="600" y="40" text-anchor="middle" font-size="21" fill="#1f2328">'
             '智能体调用关系（模型编排 · 判定在 MCP · 检索在知识库）</text>')

    # 用户
    b.append(box(500, 70, 200, 52, ["用户"], "#eaeef2", "#8c959f"))
    # Agent
    b.append(box(360, 170, 480, 96,
                 ["Nexent Agent", "deepseek-flash · 37 工具 · 5 技能"],
                 "#ddf4ff", "#54aeff", fs=13))
    # 三层
    b.append(box(60, 340, 340, 92,
                 ["evostd MCP Server", "36 个自研工具 · 判定权在这一层"],
                 "#dafbe1", "#4ac26b", fs=13))
    b.append(box(440, 340, 300, 92,
                 ["5 个技能包", "确定性脚本 · 只做渲染与提案"],
                 "#fff8c5", "#d4a72c", fs=13))
    b.append(box(780, 340, 360, 92,
                 ["知识库 mfg_assets", "10 文档 / 51 分块 / 多模态向量"],
                 "#fbefff", "#c297ff", fs=13))
    # 底层
    b.append(box(60, 500, 158, 66, ["判据库", "38 条已采纳"], "#f6f8fa", "#d0d7de", fs=12, title_fs=13))
    b.append(box(231, 500, 158, 66, ["硬锚点", "103 条 · 零人工"], "#f6f8fa", "#d0d7de", fs=12, title_fs=13))
    b.append(box(402, 500, 158, 66, ["裁决/降级台账", "追加式 · 可回放"], "#f6f8fa", "#d0d7de", fs=12, title_fs=13))
    b.append(box(580, 500, 300, 66, ["技能脚本", "证据链 / 合规报告 / 进化报告"], "#f6f8fa", "#d0d7de", fs=12, title_fs=13))
    b.append(box(900, 500, 240, 66, ["向量索引", "Qwen3-VL 嵌入"], "#f6f8fa", "#d0d7de", fs=12, title_fs=13))

    # 连线
    b.append(arrow(600, 122, 600, 170, "提问 / 指令"))
    b.append(arrow(470, 266, 250, 340, "工具调用（36）"))
    b.append(arrow(600, 266, 600, 340, "技能编排（5）", dash=True))
    b.append(arrow(730, 266, 930, 340, "检索（KB）", dash=True))
    b.append(arrow(160, 432, 140, 500, "读"))
    b.append(arrow(300, 432, 310, 500, "读"))
    b.append(arrow(420, 432, 470, 500, "写"))
    b.append(arrow(590, 432, 700, 500, "调用"))
    b.append(arrow(930, 432, 1000, 500, "召回"))

    # 边界说明
    b.append('<rect x="60" y="610" width="1080" height="150" rx="10" fill="#ffffff" '
             'stroke="#d0d7de" stroke-dasharray="6 4"/>')
    b.append('<text x="84" y="646" font-size="14" font-weight="700" fill="#1f2328">'
             '为什么这样切</text>')
    b.append('<text x="84" y="674" font-size="13" fill="#57606a">'
             '· 模型只做编排与翻译：它不能宣布"这条算违规"，结论必须来自 std_verify 的返回</text>')
    b.append('<text x="84" y="698" font-size="13" fill="#57606a">'
             '· 判据库、硬锚点、台账都在 MCP 进程内：agent 够不着，改不了</text>')
    b.append('<text x="84" y="756" font-size="13" fill="#57606a">'
             '· 检索走平台知识库，检索结果不直接变成结论，只作为证据进入决策记录</text>')

    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
            f'viewBox="0 0 {W} {H}" font-family="Microsoft YaHei, SimHei, sans-serif">'
            f'<rect width="{W}" height="{H}" fill="#ffffff"/>' + "".join(b) + '</svg>')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="submission/agent-callgraph.svg")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(build())
    print(f"[callgraph] 已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
