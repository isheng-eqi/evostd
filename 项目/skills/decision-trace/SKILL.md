---
name: decision-trace
description: 回答「这个结论凭什么」，把一条结论从头到尾的证据链摊开，走 asset_lineage 取血缘、std_clause_list 取条款原文、asset_search 取来源引用与平台 cite_index，最后落成一份逐跳标注来源的证据链报告，并给出可采信强度评级与缺口清单。当用户说「这个结论凭什么」「为什么判定它违规」「这条数据的来路是什么」「给我看证据链」「这个说法有依据吗」「追溯一下这个资产」时使用。不适用于：想知道一批资产合不合规（用 standard-verify）、想给系统补一条判据（用 clause-proposal）、想跑一轮进化（用 evolution-run）。本技能只读，不修改任何数据，也不替任何一条无来源的断言补来源。
tags:
  - 可溯源
  - 证据链
  - 血缘
  - 决策
allowed-tools:
  - asset_lineage
  - asset_search
  - std_clause_list
  - std_verify
  - std_ledger
script_outputs:
  scripts/render_evidence_chain.py:
    kind: file
    mime_types:
      - text/markdown
---

# 决策链路溯源（decision-trace）

目标：把「凭什么」回答成一条**每一跳都有来源**的链，而不是一段听起来合理的解释。

## 一、唯一一条最重要的规则：拒绝无源断言

> **没有 provenance 的跳，不允许进入推理。**

具体到本技能：

- 每一跳（边）必须有 `source`（来自哪份文档 / 哪次工具返回 / 哪个判据库快照）。
- 每一跳最好带平台 `cite_index`（对话溯源链路里的引用序号），带不上的要显式标为缺口。
- **不许补全。** 缺来源的跳就写「无来源，不可采信」，不要用「常识上应该是这样」顶上。
- 结论强度由**有源跳数 / 总跳数**决定，不由你说了算。

这条规则的推导见 <reference path="references/provenance-rule.md" />。

## 二、输入与前置检查

先把用户的「这个结论」锁定成一条**可比对的断言**：

| 结论类型 | 需要锁定的 id | 血缘方向 |
|---|---|---|
| 「判定某资产违反某条款」 | `asset_id` + `clause_id` | `up`（往上游查它凭什么被这么判） |
| 「这个资产是从哪来的」 | `asset_id` | `up` |
| 「这个资产影响了哪些下游结论」 | `asset_id` | `down` |
| 「这轮进化为什么得到这个分」 | `run_id` + `cid` | 走 `evo_status` 与 `std_ledger`，不是 `asset_lineage` |
| 「检索到这个答案是凭什么」 | 查询串 + 命中的 `asset_id` | `both` |

锁定不了就问清楚。**不要在没锁定结论的情况下先跑 `asset_lineage`**——血缘是围绕一个具体节点展开的。

## 三、执行步骤

### 第 1 步：取血缘

```
asset_lineage(asset_id="<id>", direction="up", depth=3)
```

- 返回 `{nodes: [...], edges: [{from, to, op}]}`，**边上的 `op` 是这次变换的操作名**。
- `depth` 默认 3。用户要「彻底追」时加到 5，但**每加一层都要在报告里说明深度**——深度不同的血缘图不可比。
- `direction` 按第二节的表选。答「凭什么」用 `up`，答「影响了谁」用 `down`。

### 第 2 步：取条款依据

```
std_clause_list(state="active")      # 拿条款原文
std_verify(assets=["<asset_id>"])    # 拿该资产的具体 Finding
```

- Finding 里的 `clause_id` / `source` / `message` / `evidence` / `clause_text` 是链路的**终点依据**。
- 条款原文必须原样引用。缺了就写「条款原文待补」，**不许自己组织语言**。

### 第 3 步：取来源引用与 cite_index

```
asset_search(...)      # 检索 + 溯源引用
```

- 返回里的溯源引用项（文档 / 片段 / 定位）即链路每一跳的来源。
- **平台 `cite_index`** 来自 Nexent 的对话溯源链路（回答 ↔ 来源的引用序号表）。把检索项与平台
  引用序号对齐后，每跳记一个 `cite_index`；对不上就留空，并在缺口清单里列出来。
- 一个跳可以有多个来源。**多个来源要全部列出**，不要只挑最像的那个。

### 第 4 步：落盘

用本轮生成的工作区脚本把材料**原样**写进运行工作区（当前目录即 `outputs/`）：

```python
# outputs/write_evidence_input.py —— 由你本轮生成，用 run_skill_script(source="workspace") 执行
import json
PAYLOAD = {
    "claim": {
        "text": "……",                 # 待解释的结论，一句话
        "kind": "compliance",          # compliance / lineage / retrieval / evolution
        "asset_id": "……",
        "clause_id": "……",             # 没有就留空字符串
        "run_id": "",                  # 没有就留空字符串
    },
    "lineage": { ... },                # asset_lineage 的返回，整段原样贴
    "clauses": [ ... ],                # std_clause_list 返回里的 clauses 数组，原样
    "findings": [ ... ],               # std_verify 返回里该资产的 findings 数组，原样
    "cites": [ ... ],                  # 检索引用项，每项含 cite_index 与来源定位
}
with open("evidence-input.json", "w", encoding="utf-8") as fh:
    json.dump(PAYLOAD, fh, ensure_ascii=False, indent=2)
print("written")
```

`cites` 的每一项形状：

```json
{"cite_index": 3, "doc": "设备台账-2026Q1.xlsx", "locator": "Sheet1!B12",
 "quote": "点检记录缺失", "hop": "extract_field"}
```

`hop` 用来把引用项贴到具体的边上；对不上边的引用项会被脚本列进「未挂载引用」。

### 第 5 步：渲染

<use_script path="scripts/render_evidence_chain.py" />

```
run_skill_script(
    skill_name="decision-trace",
    script_path="scripts/render_evidence_chain.py",
    params="--in evidence-input.json --out evidence-chain.md",
    source="skill",
)
```

产出 `evidence-chain.md`，含：结论、逐跳链路表、条款依据、来源引用、**缺口清单**、可采信强度评级。
把 `run_skill_script` 的返回**原样**回给用户。

## 四、怎么讲给人听

1. **先给评级**：强 / 中 / 弱 / 不成立，以及它由什么算出来（有源跳数 ÷ 总跳数、cite_index 覆盖率）。
2. **再给链**：按跳顺序念「从 X 经 op 到 Y，来源是……」。**每一跳都要带来源**，念不出来就说明它是缺口。
3. **给终点依据**：条款原文一句话 + 判据库里对应的 `source`。
4. **最后给缺口**：哪几跳没有来源、哪些引用项没挂上边。缺口是结论可信度的上限，
   必须主动说出来，不能等用户问。
5. **评级为弱或不成立时，明确说「这条结论目前不可采信」**，并指出补哪一跳的来源就能升级。

## 五、硬规则

1. **缺来源不补来源。** 无源的跳标注为「不可采信」，不要推测、不要用相似文档顶替。
2. **不把「相似的文档」当来源。** 来源必须是链路实际经过的节点或实际返回的引用项。
3. **不修改任何数据。** 本技能不调用 `asset_ingest`、不调用 `std_propose`。
4. **不改变结论。** 你的任务是摊开证据，不是重新判定。结论对不对由 `std_verify` 说。
5. **`depth` 与 `direction` 必须写进报告**，否则血缘图不可比。
6. **评级由脚本算。** 不许手工升级，也不许因为「链路看起来很完整」就口头说强。

## 六、降级与排错

| 现象 | 处理 |
|---|---|
| `asset_lineage` 返回空图 | 不是错误。报告为「该资产无血缘记录」，评级为「不成立」，并建议转 `asset-onboarding` 确认是否已正确入库 |
| 边上有 `op` 但没有来源 | 正常缺口。列进缺口清单，**不许**用 `op` 的名字推断来源 |
| `cite_index` 全都对不上 | 报告「平台引用链路未接通」。仍输出血缘链，但评级最高只能到「中」 |
| 结论涉及进化分数而不是资产 | 用 `evo_status` + `std_ledger`，不要硬塞进 `asset_lineage` |
| `std_verify` 对该资产返回 0 违例 | 结论与判据库不一致。如实报告两边，并建议转 `clause-proposal` |
| 脚本报 `lineage missing` | 第 4 步没贴 `asset_lineage` 的返回，或贴成了别的工具的返回 |

## 七、本技能的资源

- 执行脚本：`scripts/render_evidence_chain.py`
- 无源断言禁令与评级口径：<reference path="references/provenance-rule.md" />
