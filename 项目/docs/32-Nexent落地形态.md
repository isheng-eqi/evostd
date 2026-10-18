# 32 · Nexent 落地形态：什么放在哪一层，为什么

> 前提事实全部来自已验证的平台调研（`docs/01`~`docs/05`）与本机实测，不是设想。

---

## 一、一张图看完

```
                    ┌──────────────────────────────────────────┐
                    │        Nexent Agent（LLM）                │
                    │  角色：变异算子 + 编排者                   │
                    │  ★ 它不能给自己打分、不能改判据库           │
                    └───────┬──────────────┬───────────────────┘
                            │              │
              工具调用（MCP）│              │技能调用（三层机制）
                            ▼              ▼
   ┌────────────────────────────┐   ┌──────────────────────────────┐
   │  MCP Server: evostd        │   │  Skills（工作流模板）          │
   │  （★ 够不着的地方）          │   │  L1 提示注入 name+description  │
   │                            │   │  L2 read_skill_md 载入正文     │
   │  std_*  标准与裁决          │   │  L3 run_skill_script 跑脚本    │
   │  evo_*  进化回路（分片）     │   │                              │
   │  asset_* 数据资产           │   │  · standard-verify            │
   │                            │   │  · clause-proposal            │
   │  ┌──────────────────────┐  │   │  · evolution-run              │
   │  │ 自带存储（不落平台库）│  │   │  · decision-trace             │
   │  │ · 判据库 + 版本快照   │  │   │  · asset-onboarding           │
   │  │ · 硬锚点 / 软确认     │  │   └──────────────────────────────┘
   │  │ · 台账（可回滚）      │  │
   │  └──────────────────────┘  │   Nexent 平台侧
   └────────────┬───────────────┘   ┌──────────────────────────────┐
                │                   │ 知识库（ES）：三模态语料 + 检索 │
                └──────────────────►│ 对话溯源（cite_index 链路）    │
                                    │ 多模态解析（表格/图文）         │
                                    └──────────────────────────────┘
```

---

## 二、放哪一层，为什么（这是整个方案的安全设计）

| 东西 | 放哪 | 为什么 |
|---|---|---|
| **判据库 / 硬锚点 / 软确认 / 台账** | ★ **MCP Server 内部存储** | 平台表**没有 version 字段**，且证照类字段**明文入库**；自持存储才有内容级版本与回滚 |
| **裁决逻辑（消融、证伪、采纳/否决）** | ★ **MCP Server 内** | agent 只能**调用**、不能**修改**——"裁决发生在它够不着的地方"由此落地 |
| **进化回路** | ★ **MCP Server 内，分片暴露** | 绕开 **MCP 单次调用 60 秒硬超时**（超时会终止整个 Agent run） |
| **工作流知识与 SOP** | **Skill** | L3 的 `run_skill_script` 在沙箱里跑，技能目录 `chmod a-w` **只读**——技能脚本**天然改不了判据库** |
| **三模态语料与检索** | **平台知识库（ES）** | 现成的解析/切分/混合检索/Rerank，不重复造 |
| **对话级溯源** | **平台 cite_index 链路** | 已有"回答↔来源"表与前端渲染，复用 |

> **一句话**：能让 agent 够得着的都放平台，**不能让 agent 够得着的都放 MCP**。

---

## 三、MCP 工具清单（三组）

### `std_*` —— 标准与裁决（★ 核心，agent 够不着）

| 工具 | 职责 | 输入 → 输出 |
|---|---|---|
| `std_clause_list` | 判据库清单（含条款原文、来源、状态） | `state` → 判据列表 |
| `std_verify` | 逐条资产校验，**违例回指条款** | `assets` → 违例 + 条款引用 + 证据 |
| `std_compliance` | 合规率（可直接当适应度） | `assets` → 数值 + 明细 |
| `std_hard_anchors` | **机械抽取的锚点**（零人工） | `clause_id?` → 锚点样本 + 来源条款 |
| `std_propose` | **提交候选判据 → 裁决** | 判据规格 → `adopted/rejected` + 理由 + 证据 |
| `std_audit` | 对抗探针 + 判据库健康度 | — → 探针结果、误纳率、落差点检 |
| `std_ledger` | 台账（可回滚、可审计） | `limit` → 事件流 |

### `evo_*` —— 进化回路（分片，绕 60s 超时）

| 工具 | 职责 |
|---|---|
| `evo_init(domain, budget, seed)` | 建回路 → `run_id` |
| `evo_step(run_id, n)` | 跑 n 代（每代秒级完成）→ 本代摘要 |
| `evo_status(run_id)` | 代际曲线 |
| `evo_finalize(run_id)` | 最终报告（含审计与落差点检） |
| `evo_archive(run_id, top_k)` | 存档最优 + 血统 |

### `asset_*` —— 多模态数据资产

| 工具 | 职责 |
|---|---|
| `asset_ingest` | 三模态入库 + **六维资产标识** |
| `asset_search` | 检索 + 溯源引用 |
| `asset_lineage` | 决策链路溯源 |
| `asset_blindspots` | ★ **语料侧盲区**（未覆盖术语 / 未解释违规）→ 驱动判据提案 |

---

## 四、Skill 清单（工作流模板，对应赛题"能力沉淀"）

只允许 5 个 frontmatter 键：`name` / `description` / `allowed-tools` / `tags` / `script_outputs`。

| Skill | 何时触发（description 是唯一触发依据） | 做什么 |
|---|---|---|
| `standard-verify` | 用户要求"检查这批资产是否合规" | 取资产 → `std_verify` → 把违例按条款归类讲给人听 |
| `clause-proposal` | 出现"有违规但现有判据都放行" | 归集盲区 → 提炼候选判据 → `std_propose` |
| `evolution-run` | 用户要求"让系统自己变好一轮" | 编排 `evo_init → N×evo_step → evo_status → evo_finalize` |
| `decision-trace` | 用户问"这个结论凭什么" | 走 `asset_lineage` + 平台 `cite_index` → 出证据链报告 |
| `asset-onboarding` | 新资产入库 | 三模态识别 → 六维标识 → 硬锚点自查 → 入库 |

---

## 五、平台限制与对策（全部来自实测）

| 限制 | 对策 |
|---|---|
| **MCP 单次调用硬超时 60s**，超时终止整个 Agent run | 回路**分片**：`init/step/status/finalize`，每步秒级 |
| MCP 只支持 `sse` / `streamable-http`，**stdio 不能直连** | Server 用 FastMCP 起 HTTP；URL 结尾 `/mcp` |
| docker-compose 部署下 URL 必须写 `host.docker.internal`，不能写 127.0.0.1 | 注册时按此填写，并写进配置说明 |
| Skill **表里没有 version 字段**；`version_no` 冻的是绑定关系不是内容 | **判据库自己做内容快照**（MCP 内部存储），不依赖平台版本 |
| Skill frontmatter 白名单只有 5 键，其余**静默丢弃** | 只写这 5 个键；正文与长判据放 `references/` |
| MCP 工具**没有强沙箱**（`_nexent_execute_on_host=True`，跑主进程） | 自研工具内部自己做输入校验与资源限额 |
| Python 必须**恰好 3.11**；不能 `pip install nexent` | 用 `.venv-nexent311` + 本地 `sdk/` 安装 |

---

## 六、施工顺序（我来做）

| 阶段 | 做什么 | 做成的标准 |
|---|---|---|
| **S1** | 把 `evolve/` + `standard/` 打包成 **MCP Server**（FastMCP）。**先不碰 Nexent**，本地起服务自测 | 本地客户端能 `list_tools` 并跑通 `std_verify` / `evo_step` |
| **S2** | 优化 P0：**硬锚点抽取器产品化**（覆盖全部 8 类判据）+ "见过合规样本才准采纳"门槛入码 | 硬锚点覆盖条款类型数、误纳率、F1 三项指标出数 |
| **S3** | **挂上 Nexent**：注册 MCP → 健康检查 → 挂到 agent → 跑一次真实调用 | 平台里能看到工具、agent 调用序列留痕 |
| **S4** | 写 **5 个 Skill 包**并上传挂载 | agent 的 system prompt 里出现技能；`read_skill_md` 被真实调用 |
| **S5** | 接**真实语料与标准**（Domain Pack：**制造为主 + 政务数据目录对齐样板**，可插拔） | 三模态入库 + 资产标识 + 硬锚点覆盖率出数 |
| **S6** | 端到端演示 + 初赛材料（含调用关系图、截图、JSON、说明） | 可复跑、可当场验算 |

**行业我按调研结论定为"制造（主）+ 政务数据目录（对齐样板）"**，Domain Pack 保持可插拔——要换只换包，内核与 MCP 一行不改。
