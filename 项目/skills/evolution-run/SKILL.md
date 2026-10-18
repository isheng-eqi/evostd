---
name: evolution-run
description: 编排一次完整的自进化回路运行，把 evo_init 建回路、多次 evo_step 逐代推进、evo_status 看曲线、evo_finalize 出报告串成一次可复算的实验，并把代际曲线、闸门裁决、对抗审计与落差点检渲染成报告与 CSV。当用户说「让系统自己变好一轮」「跑一次进化」「提一下这个领域的分数」「看看这几代的曲线」「复现上次那轮实验」时使用。不适用于：只校验一批资产合规不合规（用 standard-verify）、给系统补一条判据（用 clause-proposal）、解释某个结论凭什么（用 decision-trace）。本技能不产生任何分数、不修改判据库、不在客户端做任何裁决。
tags:
  - 自进化
  - 编排
  - 代际曲线
  - 审计
allowed-tools:
  - evo_init
  - evo_step
  - evo_status
  - evo_finalize
  - evo_archive
  - std_audit
  - std_ledger
script_outputs:
  scripts/render_evolution_report.py:
    kind: file
    mime_types:
      - text/markdown
      - text/csv
---

# 自进化回路编排（evolution-run）

一次「让系统自己变好一轮」= 建回路 → 逐代推进 → 看曲线 → 出报告。
所有**分数、裁决、审计**都由 MCP 侧给出，本技能只负责**编排与呈现**。

## 一、为什么必须分片（这条决定成败）

MCP 单次调用有 **60 秒硬超时，超时会终止整个 Agent run**。
所以：

- **绝对不要**指望一次调用跑完整条回路。
- 每一代都必须是秒级完成的；`evo_step` 的 `n` 取 **4 到 8**，跑完立刻 `evo_status` 看耗时与曲线。
- 任何一次调用明显变慢（接近 60 秒），立刻把 `n` 降到 1~2 重试，并把这件事写进报告。

回路形态固定为：

```
evo_init(domain, budget, seed) → run_id
   ↓
evo_step(run_id, n) × k        # 每次推进 n 代，每次之间看 evo_status
   ↓
evo_status(run_id)             # 曲线
   ↓
evo_finalize(run_id)           # 最终报告（含审计与落地点检）
   ↓
evo_archive(run_id, top_k)     # 可选：存档最优 + 血统
```

## 二、三条硬约束（写死在流程里，不靠自觉）

| 编号 | 约束 | 落地方式 |
|---|---|---|
| **R1** | 分数只由确定性程序给出，**任何语言模型不得参与打分** | 你永不给出、修改、估算任何候选的分数；只抄 `evo_*` 返回值 |
| **R2** | 同输入必须同输出 | **`seed` 必须显式传入**，不许省略、不许用默认值、不许用时间戳 |
| **R3** | 必须有进化集与保留集 | 确认 `evo_init` 的返回里两套切分都存在；只有一套时报告里必须标红 |

## 三、执行步骤

### 第 1 步：建回路

```
evo_init(domain="<领域标识>", budget=<评估次数上限>, seed=<固定整数>)
```

- `domain` 必须是**已在 MCP 侧注册的领域标识**（例如本体增长、抽取规则、Skill 模板、资产标识规则）。不确定就问用户，不要猜一个。
- `budget` 是**评估次数上限**，不是代数。按「每代消耗 1~2 次评估」估，并留出审计与落地点检的余量。
- `seed` 显式给一个整数，并在整个 run 里复用它。**这是复现的唯一凭据。**
- 拿到 `run_id` 后立刻把它写进报告草稿——后续所有调用都要它。

### 第 2 步：逐代推进（循环）

```
evo_step(run_id="<run_id>", n=6)
evo_status(run_id="<run_id>")
```

每一轮之后按下面的规则判断**继续还是停**（判定必须用返回里的数字，不许凭感觉）：

| 停止条件 | 判据 |
|---|---|
| 预算耗尽 | `budget.used >= budget.evals` |
| 已停滞 | `best.score` 连续 **≥ 3** 代没有提升，且 `archive_size` 也不再增长 |
| 无效候选激增 | `invalid_cum` 单轮增幅超过 `n` 的一半 → 变异算子开始大量产出非法配置 |
| 有效性判决异常 | `run_validity.verdict != "ok"` |
| 落差过大 | `proxy_reality_gap` 超过阈值（默认 0.1） |
| 塌缩告警 | `audit.collapse.verdict != "ok"` |

一次 run 的 `evo_step` 调用总数控制在 **10 次以内**（对应约 40~60 代）。
超了就说明参数不合适，停下来汇报，不要硬跑。

### 第 3 步：收尾

```
evo_status(run_id="<run_id>")
evo_finalize(run_id="<run_id>")
evo_archive(run_id="<run_id>", top_k=5)     # 可选
std_audit()                                  # 判据库健康度与对抗探针
std_ledger(limit=20)                         # 台账：本次 run 的事件流
```

`evo_finalize` 的返回里含 **审计**（对抗探针、塌缩检测）与 **落地点检**，
这两块**必须原样进报告**，不许摘要成一句「审计通过」。

### 第 4 步：落盘

用本轮生成的工作区脚本把工具返回**原样**写进运行工作区（当前目录即 `outputs/`）：

```python
# outputs/write_evo_payloads.py —— 由你本轮生成，用 run_skill_script(source="workspace") 执行
import json
PAYLOADS = {
    "evo_status.json":  { ... },    # ← evo_status 的返回对象，整段原样贴
    "evo_finalize.json": { ... },   # ← evo_finalize 的返回对象，整段原样贴
    "evo_archive.json": { ... },    # ← evo_archive 的返回对象；没调就写 {}
}
for name, payload in PAYLOADS.items():
    with open(name, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
print("written")
```

字段名一个字都不要改——渲染脚本按字段名取值，改名等于丢数据。

### 第 5 步：渲染

<use_script path="scripts/render_evolution_report.py" />

```
run_skill_script(
    skill_name="evolution-run",
    script_path="scripts/render_evolution_report.py",
    params="--status evo_status.json --final evo_finalize.json --archive evo_archive.json --seed 20261011 --out evolution-report.md",
    source="skill",
)
```

产出两个附件：`evolution-report.md` 与 `evolution-curve.csv`。
把 `run_skill_script` 的返回**原样**回给用户，附件才会出现在下载区。

## 四、报告怎么写给人听

脚本已经按固定结构渲染，口述时按这个顺序（细节见 <reference path="references/evolution-metrics.md" />）：

1. **一轮实验的身份**：领域、`run_id`、`seed`、预算用量、实际代数。
   没有 `seed` 的曲线是不可复现的，必须在第一句就交代。
2. **曲线形态**：是「快速爬升后平台」还是「一直在抖」，用 `best_score` 轨迹说话，
   不要用 `gen_score` 的单点值下结论（单代分数回落是正常波动）。
3. **best 候选**：`cid`、分数、配置摘要、来自哪个岛。
4. **闸门裁决**：采纳 / 中立 / 有害各多少条；**有害项一条都不能省略**。
5. **对抗审计**：每条探针的分数与是否正确被拒；塌缩检测的 `spread` 与 `verdict`。
6. **落地点检与代理-真实落差**：这两个数字是「有没有在自欺」的核心信号，
   曲线好看但落差大，等于没进步。
7. **停止原因与下一步**。

## 五、硬规则

1. **你不打分。** 不估算、不插值、不修正任何分数。抄错数字比没有数字更糟。
2. **不掩盖负面信号。** 塌缩告警、`verdict != ok`、有害裁决、落差超标，
   必须出现在报告正文，不许只放在附录、不许措辞软化。
3. **不擅自改 `seed`、不改预算、中途不换 `domain`。** 任一变更都等于新开一轮实验。
4. **不用 `gen_score` 的最后一代代表整轮水平**，看 `best_score`。
5. **不把「曲线好看」讲成「系统变强了」。** 有效性判决（`run_validity`）才是结论，
   曲线的形状只是过程。
6. **只读判据库。** 本技能不调用 `std_propose`。

## 六、降级与排错

| 现象 | 处理 |
|---|---|
| `evo_step` 超时 | `n` 太大。降到 1~2 重试；若单代也超时，报告为「回路不可用」，不要伪造曲线 |
| `run_id` 丢失 | 用 `std_ledger(limit=20)` 找回最近的 run 事件；找不回就重开一轮并**明确告知用户上一轮作废** |
| `curve` 里有 `gen_score: null` | 该代候选全部非法，属正常记录。用 `invalid_cum` 讲，**不要**当成 0 分参与计算 |
| `budget` 已耗尽但曲线仍在上升 | 报告事实并建议提高预算重跑；**不要**在同一 run 上偷偷加预算 |
| `proxy_reality_gap` 偏大 | 停止推进，报告「代理指标与真实指标脱节」，建议先复核验证器再继续 |
| `audit.collapse.verdict != "ok"` | 停止推进。分数失去区分度时，继续跑只是在优化噪声 |
| 渲染脚本报 `curve missing` | 第 4 步贴错了对象。确认贴的是 `evo_status` 的返回，且含 `curve` 数组 |

## 七、本技能的资源

- 执行脚本：`scripts/render_evolution_report.py`
- 指标口径与判读规则：<reference path="references/evolution-metrics.md" />
