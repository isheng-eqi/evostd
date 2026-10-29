"""按官方模板（第一章/第二章/第三章）重组开发设计文档。

为什么需要它
------------
官方模板（`【赛题3-模板1】…docx`）的骨架是：

    第一章 项目设计思路
      智能体设计背景、应用场景、数据来源（必填）
      agent设计（必填）
      相关MCP及Skills设计（必填）
      其他补充（如有）
    第二章 代码说明
    第三章 参考资料

而赛题文字要求的六项内容（项目概述 / 整体方案 / 知识图谱 / 智能体构建 / 原始数据 / 数据处理）
是**内容清单**，不是标题。两者对不上时，正确做法是：**用模板的骨架，把六项内容装进去**。

本脚本把 `开发设计文档-六节版.md` 的内容按模板骨架重组，输出
`开发设计文档-模板版.md`（再由 md2docx 转 Word）。

用法：
    cd 项目
    python tools/build_template_doc.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUB = os.path.join(ROOT, "submission")
SRC_MD = os.path.join(SUB, "开发设计文档-六节版.md")
OUT_MD = os.path.join(SUB, "开发设计文档-模板版.md")

H2 = re.compile(r"^## (?!#)(.*)$")
H3 = re.compile(r"^### (.*)$")
H1 = re.compile(r"^# (.*)$")


def parse(lines: list[str]) -> tuple[list[str], dict[str, dict]]:
    """返回 (preamble, {h2_key: {title, body, subs{h3_key: {title, body}}}})。"""
    preamble: list[str] = []
    h2s: dict[str, dict] = {}
    cur_h2 = None
    cur_h3 = None
    for line in lines:
        m1 = H1.match(line)
        m2 = H2.match(line)
        m3 = H3.match(line)
        if m1 and cur_h2 is None:
            preamble.append(line)
            continue
        if m2:
            title = m2.group(1).strip()
            key = (re.match(r"([0-9]+(?:\.[0-9]+)*)", title) or [None, ""])[1]
            cur_h2 = {"title": title, "body": [], "subs": {}, "key": key}
            h2s[key or title] = cur_h2
            cur_h3 = None
            continue
        if m3 and cur_h2 is not None:
            title = m3.group(1).strip()
            key = (re.match(r"([0-9]+(?:\.[0-9]+)*)", title) or [None, ""])[1]
            cur_h3 = {"title": title, "body": []}
            cur_h2["subs"][key or title] = cur_h3
            continue
        if cur_h3 is not None:
            cur_h3["body"].append(line)
        elif cur_h2 is not None:
            cur_h2["body"].append(line)
        else:
            preamble.append(line)
    return preamble, h2s


def trim(lines: list[str]) -> list[str]:
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return lines


def sub_body(h2: dict, key: str) -> list[str]:
    s = h2["subs"].get(key)
    if s is None:
        raise KeyError(f"缺少子节 {key}（在 {h2['title']} 下）")
    return trim(list(s["body"]))


def emit(out: list[str], level: int, title: str):
    out.append("#" * level + " " + title)
    out.append("")


def emit_body(out: list[str], body: list[str]):
    out.extend(trim(body))
    out.append("")


def cell(body, limit):
    """把一节正文压成表格单元格：表格行转成 A：B；C：D，去掉表头与竖线。"""
    parts = []
    for idx, line in enumerate(body):
        s = line.strip()
        if not s:
            continue
        if re.fullmatch(r"\|?[\s:|-]+\|?", s):
            continue
        # 跳过表头（下一行是分隔线的表格行）
        if s.startswith("|") and idx + 1 < len(body) and re.fullmatch(
                r"\|?[\s:|-]+\|?", body[idx + 1].strip()):
            continue
        if s.startswith("|"):
            cols = [c.strip() for c in s.strip("|").split("|")]
            if len(cols) > 1:
                parts.append(cols[0] + "：" + "；".join(cols[1:]))
            else:
                parts.append(cols[0])
        elif s.startswith("- "):
            parts.append("· " + s[2:])
        else:
            parts.append(s)
    out = " ".join(parts).replace("|", "／")
    out = re.sub(r"\s+", " ", out).strip()
    return out[:limit]


def main() -> int:
    text = open(SRC_MD, encoding="utf-8").read().splitlines()
    preamble, h2 = parse(text)

    need = ["1", "2", "3", "4", "5", "6", "7"]
    for k in need:
        if k not in h2:
            print(f"★ 源文档缺少章节 {k}", file=sys.stderr)
            return 1
    appendix = next(v for v in h2.values() if v["title"].startswith("附"))
    team = h2.get("参赛队伍基础信息")
    if team is None:
        # 队伍信息在第一个 ## 里（标题不是数字）
        team = next(v for k, v in h2.items() if "参赛队伍" in v["title"])

    out: list[str] = []
    out.append("# 第十一届华为ICT大赛创新赛")
    out.append("# 开发设计文档")
    out.append("")
    # 数据声明（取源文档 preamble 里的引用块）
    decl = [l for l in preamble if l.startswith(">")]
    if decl:
        out.extend(decl)
        out.append("")

    # ---- 参赛队伍基础信息（按模板字段）----
    emit(out, 2, "参赛队伍基础信息")
    def tsv(key: str) -> str:
        s = team["subs"].get(key)
        if not s:
            return ""
        lines = [l.strip() for l in trim(list(s["body"])) if l.strip()]
        return " ".join(lines)
    raw_text = open(SRC_MD, encoding="utf-8").read()
    m_phone = re.search(r"(?<!\d)1[3-9]\d{9}(?!\d)", raw_text)
    m_mail = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", raw_text)
    phone = m_phone.group(0) if m_phone else "（待填）"
    mail = m_mail.group(0) if m_mail else "（待填）"
    duty = cell(trim(list(team["subs"]["团队分工"]["body"])), 300)
    abstract = cell(trim(list(team["subs"]["摘要"]["body"])), 500)
    tech = cell(trim(list(team["subs"]["应用技术"]["body"])), 600)
    demo = ("平台智能体：Nexent「标准驱动·可进化决策智能体」V4 已发布（36 个自研 MCP 工具 + 1 个知识库检索工具 + 5 个技能）；"
            "端到端决策实录见 §2.2.3 与 screenshots/11；工具 / 技能 / 知识库截图 screenshots/07、08、05；"
            "检索-推理双驱动 screenshots/16；决策工作台 screenshots/13（本地可跑）；"
            "离线闭环 python -m evostd.smoke；演示视频 videos/00-演示视频-3分钟.mp4。")

    out.append("| 项目 | 内容 |")
    out.append("|---|---|")
    out.append("| 学校名称 | 太原理工大学 |")
    out.append("| 队伍名称 | WarrantedBelief |")
    out.append("| 队伍成员 | 指导老师（1 人）：周稻祥；队员（不超过 3 人）：王炳晟（队长）、宋金明、任俊儒 |")
    out.append(f"| 队长联系电话 | {phone} |")
    out.append(f"| 队长联系邮箱 | {mail} |")
    out.append("| 团队合照 | 待补（另附 1 张约 2MB 合照） |")
    out.append(f"| 团队分工 | {duty[:300]} |")
    out.append("| 赛题 | 赛题 3：基于华为 ModelEngine Nexent 打造可进化决策智能体 |")
    out.append(f"| 摘要 | {abstract[:500]} |")
    out.append(f"| 应用技术 | {tech[:300]} |")
    out.append(f"| 智能体展示 | {demo[:300]} |")
    out.append("")

    # ---- 第一章 ----
    out.append("# 第一章 项目设计思路")
    out.append("")
    emit(out, 2, "智能体设计背景、应用场景、数据来源（必填）")
    emit(out, 3, "1.1 项目概述")
    for old, new in (("1.1", "1.1.1 要解决的问题"),
                     ("1.2", "1.1.2 系统做了什么"),
                     ("1.3", "1.1.3 实测规模")):
        emit(out, 4, new)
        emit_body(out, sub_body(h2["1"], old))
    emit(out, 3, "1.2 原始数据说明")
    emit_body(out, trim(list(h2["5"]["body"])))

    emit(out, 2, "agent设计（必填）")
    emit(out, 3, "2.1 整体方案设计")
    for old, new in (("2.1", "2.1.1 分层"),
                     ("2.2", "2.1.2 五条设计纪律"),
                     ("2.3", "2.1.3 模块解耦与多行业")):
        emit(out, 4, new)
        emit_body(out, sub_body(h2["2"], old))
    emit(out, 3, "2.2 智能体构建方案")
    for old, new in (("4.1", "2.2.1 平台配置"),
                     ("4.2", "2.2.2 角色边界"),
                     ("4.3", "2.2.3 端到端实测")):
        emit(out, 4, new)
        emit_body(out, sub_body(h2["4"], old))

    emit(out, 2, "相关MCP及Skills设计（必填）")
    emit(out, 3, "3.1 检索-推理双驱动")
    emit_body(out, sub_body(h2["4"], "4.4"))
    emit(out, 3, "3.2 Skill 分层与模板沉淀")
    emit_body(out, sub_body(h2["4"], "4.8"))
    emit(out, 3, "3.3 决策工作台")
    emit_body(out, sub_body(h2["4"], "4.7"))
    emit(out, 3, "3.4 调试迭代经验")
    emit_body(out, sub_body(h2["4"], "4.5"))
    emit(out, 3, "3.5 需要同步提交的文件说明")
    emit_body(out, sub_body(h2["4"], "4.6"))

    emit(out, 2, "其他补充（如有）")
    emit(out, 3, "4.1 知识图谱构建方案")
    for old in ("3.1", "3.2", "3.3", "3.4", "3.5", "3.6", "3.7"):
        s = h2["3"]["subs"].get(old)
        if not s:
            continue
        emit(out, 4, f"4.1.{old.split('.')[1]} {re.sub(r'^[0-9.]+\s*', '', s['title'])}")
        emit_body(out, trim(list(s["body"])))
    emit(out, 3, "4.2 数据处理说明")
    for old in ("6.1", "6.2", "6.3", "6.4", "6.5"):
        s = h2["6"]["subs"].get(old)
        if not s:
            continue
        emit(out, 4, f"4.2.{old.split('.')[1]} {re.sub(r'^[0-9.]+\s*', '', s['title'])}")
        emit_body(out, trim(list(s["body"])))
    emit(out, 3, "4.3 业务价值测算")
    emit_body(out, trim(list(h2["7"]["body"])))
    emit(out, 3, "4.4 与赛题五条任务要求的对应")
    emit_body(out, trim(list(appendix["body"])))

    # ---- 第二章 ----
    out.append("# 第二章 代码说明（完整代码文件请作为单独附件（必选），与本文档一并打包上传）")
    out.append("")
    emit(out, 2, "2.1 代码清单与目录结构")
    out.append("| 目录 | 职责 | 关键文件 |")
    out.append("|---|---|---|")
    out.append("| `src/standard/` | 判据语言（12 类）· 编译器 · 硬锚点抽取 · 语料抽表 | `compiler.py` `grammar_v2.py` `anchors_v2.py` `table_extractor.py` |")
    out.append("| `src/evolve/` | 进化内核：候选池 → 评测 → 采纳 · 岛屿存档 · 对抗探针 | `core.py` `domains/` |")
    out.append("| `src/evostd/` | MCP 服务（36 工具）· 判据库 · 台账 · 决策工作台 | `server.py` `service.py` `store.py` `webui.py` |")
    out.append("| `src/domains/` | 可插拔领域包（制造业 / 政务） | `manufacturing/` `gov_catalog/` |")
    out.append("| `tools/` | 测试、图谱导出、数字对账、效率基准、打包 | `run_all.py` `kg_export.py` `verify_numbers.py` |")
    out.append("| `skills/` | 5 个技能包（SKILL.md + scripts + references） | `standard-verify/` 等 |")
    out.append("")
    loc = 0
    nfiles = 0
    for d in ("src", "tools", "skills"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, d)):
            dirnames[:] = [x for x in dirnames if x not in ("__pycache__", "runs")]
            for fn in filenames:
                if fn.endswith((".py", ".md")):
                    nfiles += 1
                    with open(os.path.join(dirpath, fn), encoding="utf-8", errors="replace") as f:
                        loc += sum(1 for _ in f)
    out.append(f"- 代码规模（实测）：**{nfiles} 个源文件 / {loc} 行**（`src` + `tools` + `skills`，不含运行状态）。")
    out.append("- 完整代码作为单独附件（`代码/` 目录）与本文件一并打包。")
    out.append("")
    emit(out, 2, "2.2 运行方式")
    out.append("```powershell")
    out.append("cd 项目/src && set EVOSTD_NOW=2026-10-21")
    out.append("python -m evostd.webui --port 8899        # 决策工作台")
    out.append("python -m evostd.server  --port 8770      # MCP Server")
    out.append("cd .. && python tools/run_all.py          # 测试→图谱→对账→打包")
    out.append("```")
    out.append("")

    # ---- 第三章 ----
    out.append("# 第三章 参考资料（如有）")
    out.append("")
    out.append("1. 华为 ModelEngine Nexent 官方文档：<https://modelengine-group.github.io/nexent/zh/user-guide/home-page.html>")
    out.append("2. Model Context Protocol 规范：<https://modelcontextprotocol.io>")
    out.append("3. *Audit the Scaffold, Not the Checkpoint*（arXiv:2609.34924）——固定可达编辑集的收益递减")
    out.append("4. *Harness Evolution Hits a Ceiling*（arXiv:2610.11655）——失败构成决定改 harness 还是改权重")
    out.append("5. *Large Language Models Cannot Self-Correct Reasoning Yet*（arXiv:2310.01798）——无外部反馈时自纠会变差")
    out.append("6. *Pride and Prejudice*（arXiv:2402.11436）——自评偏见随迭代放大")
    out.append("7. *Hill Sampling*（arXiv:2609.25510）——简单基线与复杂进化脚手架的对照")
    out.append("")
    out.append("> 本项目开源于 <https://github.com/isheng-eqi/evostd>（Apache-2.0）。")

    final = "\n".join(out) + "\n"
    final = final.replace("§6.5", "§4.2.5").replace("§4.3", "§2.2.3")
    open(OUT_MD, "w", encoding="utf-8", newline="\n").write(final)
    print(f"[build] 已生成 {OUT_MD}（{len(out)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
