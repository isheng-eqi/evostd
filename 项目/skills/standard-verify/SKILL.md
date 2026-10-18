---
name: standard-verify
description: 对一批已登记的数据资产执行标准合规校验，调用 std_verify 逐条比对现行判据库，把违例按条款归类并附上条款原文与证据字段，最后落成一份逐条可回指的合规报告。当用户说「检查这批资产是否合规」「跑一遍标准校验」「出一份违规清单」「这批数据有没有违反标准」「合规率是多少」「复核一下上次的校验结果」时使用。不适用于：新增或修改判据（用 clause-proposal）、让系统自己进化一轮（用 evolution-run）、解释某个既有结论凭什么（用 decision-trace）、把新文件首次入库（用 asset-onboarding）。本技能只读判据库，任何情况下都不写入判据库。
tags:
  - 标准对齐
  - 合规校验
  - 数据资产
allowed-tools:
  - asset_search
  - std_clause_list
  - std_verify
  - std_compliance
script_outputs:
  scripts/render_compliance_report.py:
    kind: file
    mime_types:
      - text/markdown
---

# 标准合规校验（standard-verify）

把「这批资产合不合规」变成一份**逐条可回指标准原文**的报告。

## 一、判定权归属（先读这条，它决定你不能做什么）

| 角色 | 职责 |
|---|---|
| MCP 侧判据库（`std_*`） | **唯一**给出对错的角色。判据是纯函数，同输入同输出，不依赖时钟、随机、网络、模型 |
| 本技能 / 你（LLM） | 组织输入、调用工具、把结论翻译成人话。**不得自行判定任何一条资产是否违规** |

推论（很重要）：当你认为某条资产明显违规、而 `std_verify` 却放行时，**以 `std_verify` 为准**。这不是你的判断错了，而是**判据库的盲区**——把它记下来交给 `clause-proposal`，不要在报告里写成违例。

## 二、输入与前置检查

先判断用户给的是哪一类输入：

| 用户给的 | 处理 |
|---|---|
| 已在库资产 ID 列表（如 `AST-2026-0001`） | 直接进第三步 |
| 一个目录 / 一批文件路径 | 它们是**未入库**的原始文件 → 先用 `asset-onboarding` 入库，再回来校验。不要跳过入库直接校验 |
| 「全部资产」「最近一周的」 | 用 `asset_search` 取回，并把实际取到的条数与筛选条件写进报告开头 |
| 空 | 追问范围。**不要**默认全量跑 |

一次校验的资产条数控制在 **50 条以内**（MCP 单次调用硬超时 60 秒；超时会终止整个 Agent run）。超过就分批，逐批调用、逐批汇报。

## 三、执行步骤

### 第 1 步：锁定判据库

```
std_clause_list(state="active")
```

- 记下返回里的**判据条数**与**判据库版本/快照标识**（若返回里带 `snapshot_id` / `updated_at`）。报告必须写明「依据哪一版判据库得出的结论」，否则结论不可复算。
- 返回里若含 `compilable=false` 的条款，这些是**只能靠人判断的条款**——在报告末尾单列一节「无人把关的条款」，明确告诉用户这部分没有被本次校验覆盖。**不要**把它们算进合规率。

### 第 2 步：取资产

```
asset_search(...)     # 仅当用户没直接给资产对象时
```

把资产对象整理成数组，字段名保持 `asset_id` / `modality` / `secrecy` / `owner_dept` / `collect_time` / `ingest_time` / `quality_score` / `completeness` / `file_hash` / `cited_standards` / `storage_uri` **原样不变**——判据是按字段名寻址的，改名即等于换了一条判据。

### 第 3 步：逐条校验

```
std_verify(assets=[...])
```

- 返回是一份**逐资产的结论 + 每条判据的 Finding**。Finding 的字段契约见
  <reference path="references/verify-io-contract.md" />
- 只要返回里出现 `ok=false`，它就是违例，**必须原样进入报告**，不许省略、不许合并同类项后只报一条。
- 同一资产被多个条款判违例时，逐条列出（这正是「按条款归类」的价值）。

### 第 4 步：取合规率

```
std_compliance(assets=[...])
```

合规率是后续 `evolution-run` 的适应度基线，必须落进报告摘要里，并且写明它是对**哪一批资产、哪一版判据库**算出来的。禁止自己用「违例数 ÷ 总数」手算——口径必须来自 MCP。

### 第 5 步：把工具返回落盘

`render_compliance_report.py` 从文件读输入。用本轮生成的工作区脚本把上一步的返回**原样**写进运行工作区：

```python
# outputs/write_std_verify.py —— 由你本轮生成，用 run_skill_script(source="workspace") 执行
import json
PAYLOAD = { ... }        # ← 把 std_verify 的返回对象整段原样贴进来，不要改写字段名
with open("std_verify.json", "w", encoding="utf-8") as fh:
    json.dump(PAYLOAD, fh, ensure_ascii=False, indent=2)
print("written")
```

同法落 `std_clause_list` 的返回为 `std_clause_list.json`（用于补齐条款原文与列出无人把关的条款）。两个文件都落在运行工作区的 `outputs/` 下，脚本的当前目录就是它。

### 第 6 步：渲染报告

<use_script path="scripts/render_compliance_report.py" />

```
run_skill_script(
    skill_name="standard-verify",
    script_path="scripts/render_compliance_report.py",
    params="--verify std_verify.json --clauses std_clause_list.json --out compliance-report.md",
    source="skill",
)
```

- `--verify` 必填；`--clauses` 可选但强烈建议给（缺了就没法输出条款原文与无人把关条款）。
- 输出只有一个附件：`compliance-report.md`。把 `run_skill_script` 的返回**原样**回给用户，附件才会出现在下载区。
- 脚本失败时依据报错修好输入重跑。**不要**改成手工拼一份报告糊过去。

## 四、怎么讲给人听

报告渲染完之后，在对话里用这个顺序口述（细节口径见 <reference path="references/clause-taxonomy.md" />）：

1. **一句话结论**：合规率 x%，n 条资产里 m 条存在违例。
2. **按条款归类**：哪几条条款被违反得最多，各自命中哪些资产。**按「条款」分组，不按「资产」分组**——用户要的是「哪个标准要求没被满足」，不是「哪条数据难看」。
3. **逐条给原文**：每条违例都要能指到 `《标准名》第N条`，并引用条款原文一句话。
4. **给证据字段**：`evidence` 里的字段名与取值必须原样展示，例如「`modality=「表格图像」` 不在受控词表 `[文本, 表格, 图文, 影像]` 内」。
5. **给修复动作**：按 `references/clause-taxonomy.md` 的类型对照表给出该改哪个字段。
6. **声明边界**：本版判据库条数、无人把关的条款、以及「本报告不含任何人工或模型判定」。

## 五、硬规则

1. **判定只来自 `std_verify` / `std_compliance`**。你不打分、不改判据、不补判据。
2. **违例必须回指条款**。如果 `evidence` 里没有 `clause_text`，用第 1 步的 `std_clause_list` 补齐；补不齐就显式写「条款原文待补」，不许自己编原文。
3. **不许隐藏**。`ok=false` 的条目一条都不能丢，包括你觉得「影响不大」的。
4. **不许把合规率包装成进步**。合规率是当前状态的度量，不是进化成果；进步只在 `evolution-run` 的代际曲线里说。
5. **只读**。本技能不得调用 `std_propose`，不得写入任何判据。
6. **报告里的数字必须来自脚本产物或工具返回**，不允许口算、估摸、四舍五入后再讲。

## 六、降级与排错

| 现象 | 处理 |
|---|---|
| `std_verify` 返回 0 违例，但用户坚称有违规 | 不争论、不改判据。告知「现行判据库全部放行」，并**建议转 `clause-proposal`** 把盲区做成候选判据 |
| `std_verify` 报「未知字段」类错误 | 字段名被改过。回第 2 步按判据库的字段名重新取数 |
| MCP 调用超时（接近 60 秒） | 资产批太大。拆到每批 ≤ 20 条重跑，并在报告里写明分批口径 |
| `std_compliance` 与 `std_verify` 的违例数对不上 | 以 `std_compliance` 的数值为准（它是适应度口径），并在报告里注记差异；不要静默取其一 |
| 脚本报 `verify file not found` | 第 5 步没落盘，或落到了工作区根而非 `outputs/`。重做第 5 步 |

## 七、本技能的资源

- 执行脚本：`scripts/render_compliance_report.py`
- 输入输出契约：<reference path="references/verify-io-contract.md" />
- 条款类型与话术对照：<reference path="references/clause-taxonomy.md" />
