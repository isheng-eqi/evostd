# 进化回路指标口径与判读规则

## 一、一轮 run 的身份（缺一不可）

| 字段 | 含义 | 为什么必须有 |
|---|---|---|
| `domain` | 进化对象（本体增长 / 抽取规则 / Skill 模板 / 资产标识规则） | 不同领域的分数不可比 |
| `run_id` | 回路标识 | 后续所有 `evo_step` / `evo_status` / `evo_finalize` 的钥匙 |
| `seed` | 随机种子 | **同输入同输出的唯一凭据**。没有它，这条曲线不可复现 |
| `budget.evals` | 评估次数上限 | 分数提升必须与预算一起讲，否则「涨了」可能只是「跑得久」 |
| `budget.used` | 已消耗评估次数 | 判断是否还能继续 |
| `budget.wall_s` | 墙钟耗时 | 与 60 秒调用超时对照，判断分片粒度是否合适 |

## 二、曲线的四种形态（用 `best_score` 判，不用 `gen_score`）

| 形态 | 特征 | 判读 |
|---|---|---|
| **爬升后平台** | `best_score` 连续上升后走平，`archive_size` 停止增长 | 正常收敛。继续跑没有收益，转 `clause-proposal` 补盲区 |
| **仍在爬升** | 最近 3 代 `best_score` 仍在提升 | 可以继续 `evo_step`，注意预算 |
| **抖动无趋势** | `gen_score` 大起大落，`best_score` 长期不动 | 变异算子多样性不足，或验证器区分度不够。看审计的 `spread` |
| **高开低走** | 早期就拿到高分，之后一路走平甚至变差 | 极可能是验证器塌缩（分数没有区分度），**先查审计再谈成绩** |

`gen_score` 为 `null` 表示该代候选全部非法，**不是 0 分**。计算任何均值时必须跳过它，
并用 `invalid_cum` 单独讲非法候选的累计情况。

## 三、关键指标

| 指标 | 位置 | 口径 | 方向 |
|---|---|---|---|
| `best_score` | `status.best.score` | 存档中最高分 | ↑ |
| `gen_score` | `curve[].gen_score` | 当代候选分数 | 波动正常，不代表水平 |
| `archive_size` | `curve[].archive_size` | 累计入档候选数 | ↑ 表示多样性还在 |
| `invalid_cum` | `curve[].invalid_cum` | 累计非法候选数 | ↓ |
| `run_validity.verdict` | finalize | 本轮是否有效 | 必须 `ok` |
| `run_validity.margin` | finalize | `best_score - worst_probe_score` | 越大越可信 |
| `proxy_reality_gap` | finalize | 代理指标与实际指标的落差 | ↓，超过 0.1 视为脱节 |
| `audit.probe_pass_rate` | finalize | 对抗探针被正确拒绝的比例 | 必须为 1.0 |
| `audit.collapse.spread` | finalize | 分数极差 | 过小 = 塌缩 |
| `gate.verdicts` | finalize | 闸门裁决分布 | `harmful` 必须为空 |

## 四、对抗审计怎么读

审计是「验证器有没有在自欺」的检查，比曲线重要。

**探针**（`audit.probes`）：每条探针是一个**不该拿高分的策略**。

| 探针 | 在测什么 |
|---|---|
| `select-all` | 全选策略：如果它能拿高分，说明验证器不惩罚滥选 |
| `suffix-only` | 只按后缀匹配：如果它能拿高分，说明验证器在看表面特征 |
| `high-freq-only` | 只选高频项：如果它能拿高分，说明验证器在奖励高频而非正确 |

判读：`correctly_rejected == true` 才算通过。只要有一条 `false`，
**本轮的所有分数提升都不可信**，必须在报告里写在最前面。

**塌缩检测**（`audit.collapse`）：

- `spread`（分数极差）过小 → 所有候选分数都差不多 → 验证器可能已退化成「永远通过」。
- `verdict != "ok"` 时，继续跑只是在优化噪声。停下来。

## 五、闸门裁决怎么读

`gate.verdicts` 是三个桶：`adopted` / `neutral` / `harmful`。

- `harmful` **不为空**时：有候选在真实指标上造成了伤害。
  这些候选项必须逐条列出（`gate.harmful[]`），并说明它们的 `cid` 与血统。
- `neutral` 占绝大多数是正常的——闸门保守，宁可中立不可有害。
- 「采纳数」不是成绩。采纳数高但 `run_validity` 不 ok，等于批量引入了噪声。

## 六、停止原因必须写进报告

报告结尾必须明确本轮**为什么停**，从下面选一个并给出对应数字：

| 停止原因 | 伴随数字 |
|---|---|
| 预算耗尽 | `budget.used == budget.evals` |
| 已停滞 | 连续 N 代 `best_score` 无提升 + `archive_size` 不再增长 |
| 无效候选激增 | 单轮 `invalid_cum` 增幅 > `n / 2` |
| 有效性判决异常 | `run_validity.verdict` 的值 |
| 落差过大 | `proxy_reality_gap` 的值 |
| 塌缩告警 | `audit.collapse.spread` 与 `verdict` |
| 调用超时 | 哪一次 `evo_step`、`n` 是多少 |

**「跑到不想跑为止」不是停止原因。**

## 七、三条硬约束的复述

1. **R1 外部**：分数只由确定性程序给出，任何语言模型不得参与打分。
   你抄数字，不造数字。
2. **R2 确定**：同输入必须同输出。不依赖时钟、随机数、网络。`seed` 必须显式。
3. **R3 可 held-out**：必须有进化集与保留集两套数据，保留集只在审计时读取。
   只有一套切分时，`run_validity` 的结论不成立。

这三条不是建议。任一条被破坏，代际曲线就不再是证据。
