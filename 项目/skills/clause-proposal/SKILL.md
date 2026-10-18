---
name: clause-proposal
description: 把「有资产违规、但现有判据全部放行」这件事，变成一条带消融实测证据的候选判据，并提交给 std_propose 裁决。整条链路是：归集语料侧盲区、对每个盲区做实测确认、取机械硬锚点、起草候选判据规格、做静态校验、提交裁决、按否决理由分流重试。当用户说「明明违规系统却放行了」「这批数据有判据没覆盖到的问题」「给系统补一条判据」「盲区有哪些」「上次提案为什么被拒」时使用。不适用于：只是想知道某批资产合不合规（用 standard-verify）、让系统自己进化一轮（用 evolution-run）、解释已有结论的依据（用 decision-trace）。本技能只提案，永远不写入判据库。
tags:
  - 判据提案
  - 盲区
  - 硬锚点
  - 标准对齐
allowed-tools:
  - asset_blindspots
  - asset_search
  - std_clause_list
  - std_hard_anchors
  - std_verify
  - std_propose
  - std_ledger
script_outputs:
  scripts/build_proposal.py:
    kind: file
    mime_types:
      - application/json
      - text/markdown
---

# 候选判据提案（clause-proposal）

目标：把「系统该判违规却没判」的盲区，变成一条**可被外部裁决**的候选判据。

## 一、三条不可越过的线

1. **盲区必须实测确认，不能凭想象。**
   设计者凭直觉认定的盲区，实测经常是零——现有判据早就把它抓住了。
   所以每个盲区都要有一份「实测放行」证据：拿该盲区的样本跑 `std_verify`，返回 **0 违例**。
   拿不出这份证据的盲区，不许进入提案。

2. **裁决发生在你够不着的地方。**
   你只能起草规格并调用 `std_propose`。采纳 / 否决由 MCP 侧做，理由与证据由它给。
   **你不得自己宣布一条判据成立**，也不得因为「这条明显对」就跳过裁决。

3. **一次只提一小批同源判据。**
   同一盲区、同一字段、同一类判据的一批可以一起提；跨盲区混着提会被一起否决，
   而且无法归因是哪一条出的问题。

## 二、输入与前置检查

| 情况 | 处理 |
|---|---|
| 用户指了一批「被放行的违规」资产 | 进第 1 步，用 `asset_blindspots` 归集 |
| 用户只说「盲区有哪些」 | 跑到第 2 步为止，输出盲区清单 + 每条盲区的实测状态，**不**自动提案 |
| 用户说「上次提案为什么被拒」 | 用 `std_ledger(limit=20)` 取回裁决记录，只说记录里写了什么，不做推测 |
| 用户要求「直接改判据库」 | 明确拒绝：本技能只提案，写入只发生在 MCP 侧的裁决之后 |

## 三、执行步骤

### 第 1 步：归集盲区

```
asset_blindspots()
```

返回**语料侧盲区**：未覆盖术语、未解释违规。拿到后先做一次粗筛：

- 同义反复的（同一现象换个说法）→ 合并成一条
- 已经被现有判据覆盖的 → 用第 2 步的实测证伪，直接丢弃
- 现象描述模糊、说不清「哪个字段、什么取值算违规」的 → 丢弃。**判据要求可机械判定**

### 第 2 步：实测确认（这一步不能省）

对每条候选盲区：

1. 用 `asset_search` 或用户提供的样本，找出**人判违规、但现有判据全部放行**的资产 ID 清单；
2. 对这批资产调用 `std_verify(assets=[...])`；
3. 只有返回 **0 条违例** 时，这条盲区才成立。把它记成一条消融证据：

```json
{
  "BS-001": {
    "assets": ["AST-2026-0031", "AST-2026-0032"],
    "checked_clauses": 12,
    "violations": 0,
    "snapshot_id": "clause-db-2026-10-11T09-00-00Z",
    "note": "该批资产人工判为违规，现行 12 条判据全部放行，确认为真盲区"
  }
}
```

只要有 1 条违例，就不是盲区（现有判据已经覆盖），丢弃。
判定口径与三条实测教训见 <reference path="references/blindspot-ablation.md" />。

### 第 3 步：取机械硬锚点

```
std_hard_anchors()                  # 全部锚点
std_hard_anchors(clause_id="第15条")  # 指定条款的锚点
```

硬锚点是标准文本里**机械可判定**的部分（受控词表、取值域、必填清单、条款编号关系、附录示例），
零人工、无争议、免费。它解决的是「凭什么说这条判据对」——提案必须挂上锚点支撑。

同时确认：该盲区涉及的**受控清单**（白名单 `registry`）在判据库里已存在。
引用一个不存在的清单，判据会在运行时静默放行。

### 第 4 步：起草候选判据规格

按 <reference path="references/clause-spec.md" /> 的 8 类规格写。要点：

- `kind` 必须落在 8 类之内。**表达不了就先提案扩张判据语言，不要把规则硬塞进近似类型。**
- `params` 必须按该类型的必填键写全（`field` / `min` / `max` / `allowed` / `pattern` /
  `when_field` / `when_value` / `require_field` / `left` / `right` / `op` / `registry`）。
- 判据约束的字段名必须来自 `std_clause_list` 里已出现的字段，**不要发明新字段**。

### 第 5 步：落盘 + 静态校验 + 出提案书

把下面这些**原样**落进运行工作区（用本轮生成的工作区脚本写文件，当前目录就是 `outputs/`）：

| 文件 | 内容 |
|---|---|
| `proposal-input.json` | 见下方骨架；`blindspots` / `anchors` / `existing_clauses` 三个字段直接贴工具返回里的数组 |

```python
# outputs/write_proposal_input.py —— 由你本轮生成，用 run_skill_script(source="workspace") 执行
import json
PAYLOAD = {
    "standard": "……",              # std_clause_list 返回里的 standard
    "snapshot_id": "……",           # std_clause_list 返回里的 snapshot_id
    "blindspots": [...],           # asset_blindspots 返回里的盲区数组，原样
    "ablations": {...},            # 第 2 步的实测证据，键为盲区 id
    "anchors": [...],              # std_hard_anchors 返回里的锚点数组，原样
    "registries": {...},           # 受控清单名 -> 条目列表，用于校验 registry 是否存在
    "existing_clauses": [...],     # std_clause_list 返回里的 clauses 数组，原样
    "candidates": [...],           # 第 4 步起草的候选判据
}
with open("proposal-input.json", "w", encoding="utf-8") as fh:
    json.dump(PAYLOAD, fh, ensure_ascii=False, indent=2)
print("written")
```

<use_script path="scripts/build_proposal.py" />

```
run_skill_script(
    skill_name="clause-proposal",
    script_path="scripts/build_proposal.py",
    params="--in proposal-input.json --out clause-proposal",
    source="skill",
)
```

脚本做 9 项静态校验（类型、参数完整性、正则可编译、清单存在、盲区存在、消融为 0、
锚点或合规样本支撑、与现有判据不重复、id 不冲突），产出两个附件：

- `clause-proposal.json` —— 机器可提交的规格，含每条候选的 `checks.passed`
- `clause-proposal.md` —— 给人看的提案书（盲区表 / 消融证据 / 锚点 / 逐条校验结论）

**只提交 `checks.passed` 为 `true` 的候选。** 未通过的项，脚本会给出具体失败原因，按原因修。

### 第 6 步：提交裁决

```
std_propose(spec=<candidate>)      # spec 用 clause-proposal.json 里对应候选的字段
```

逐条提交，把返回的 `adopted` / `rejected`、理由、证据**原样**记下来。
一次提交一批同源候选时，逐条对齐返回结果，不要笼统说「都通过了」。

### 第 7 步：按否决理由分流

| 否决理由类型 | 含义 | 下一步 |
|---|---|---|
| 表达不了 / 文法不支持 | 8 类判据语言覆盖不到这条规则 | 改提**判据语言扩张**，而不是换一个近似类型硬塞 |
| 证据不足 | 缺硬锚点或缺「见过合规样本」 | 回第 3 步补锚点，或补一批合规样本证明不误伤 |
| 与现有判据重复 | 已有等价判据 | 丢弃候选项，回头检查第 1 步的粗筛 |
| 误纳（合规样本被判违规） | 判据过严 | 收紧 `params`（缩小 `allowed`、放宽 `pattern`、加 `when_*` 前置条件）后重提 |
| 与已有判据冲突 | 两条判据对同一字段给出相反要求 | 先解决冲突：提一条修订提案，不要两条并存 |

### 第 8 步：核对台账

```
std_ledger(limit=20)
```

确认本次提案与裁决**已落账**。台账要能回答「这条判据是谁、什么时候、凭什么加进来的」。
落账缺失是必须报告的问题，不是可以略过的小事。

## 四、交付给用户的话

1. 本次确认了 n 条真盲区，提了 m 条候选判据，采纳 a 条、否决 r 条。
2. 每条盲区：现象、涉及资产、实测放行证据（判据库快照 + 0 违例）。
3. 每条候选：类型、约束字段、参数、支撑锚点。
4. 每条否决：原样引用 MCP 给的理由，**不许改写、不许解释成「差不多通过了」**。
5. 明确声明：判据库的写入只发生在 MCP 侧裁决之后，本技能没有写库能力。

## 五、硬规则

1. **无实测不提案**：没有「0 违例」的消融证据，候选不许进 `std_propose`。
2. **无锚点不提案**：没有硬锚点或合规样本支撑的候选，脚本会判 `passed=false`，不许绕过。
3. **不改库、不自裁、不预判结果**。不要说「这条肯定会被采纳」。
4. **`std_propose` 一次只提交同源的一小批**，避免批量否决后无法归因。
5. **被拒不是失败**。被拒的理由是下一轮迭代的输入，报告里必须如实呈现。
6. **不许把「提案被采纳」讲成「系统变好了」**。判据库扩张与合规率提升是两件事，
   后者只在 `evolution-run` 的代际曲线里说。

## 六、降级与排错

| 现象 | 处理 |
|---|---|
| `asset_blindspots` 返回空 | 不是错误。告知「语料侧暂无盲区」，并建议先跑一轮 `standard-verify` 扩大语料覆盖 |
| 盲区实测出 1 条以上违例 | 该候选不是盲区，丢弃，并在报告里记为「实测证伪的候选盲区」 |
| 脚本报 `registry not declared` | 第 3 步漏了。要么补清单，要么该判据类型换不了——先确认清单归属哪个条款 |
| 脚本报 `duplicate with existing clause` | 与现有判据同类型同字段。确认是否真的等价；确不等价则改 `params` 使约束面可区分 |
| `std_propose` 超时 | 候选批量太大。拆成单条重提 |
| 提案被采纳但 `std_verify` 行为没变 | 判据库快照可能未刷新。用 `std_clause_list` 确认新判据的 `state`，并重跑 `standard-verify` 验证生效 |

## 七、本技能的资源

- 执行脚本：`scripts/build_proposal.py`
- 盲区实测纪律与证据口径：<reference path="references/blindspot-ablation.md" />
- 八类判据规格与校验规则：<reference path="references/clause-spec.md" />
