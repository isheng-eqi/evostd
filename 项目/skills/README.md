# Skills：标准驱动的自进化数据资产认知智能体

本目录是可直接上传到 Nexent 平台的 **5 个 Skill 包** + 1 个本地自检脚本。

```
项目/skills/
├── README.md                       ← 本文件（不属于任何技能包，不参与上传）
├── _validate.py                    ← 本地静态自检（不属于任何技能包）
├── asset-onboarding/               ┐
├── standard-verify/                │ 每个目录 = 一个独立 ZIP = 一个技能
├── clause-proposal/                │
├── decision-trace/                 │
└── evolution-run/                  ┘
```

每个技能包内部结构：

```
<技能名>/
├── SKILL.md                # frontmatter（5 个白名单键）+ 正文（≤ 500 行）
├── scripts/*.py            # 确定性脚本，stdout 只输出 artifact 契约 JSON
└── references/*.md         # 长判据与契约文档，由 SKILL.md 一层直达引用，按需读取
```

---

## 一、五个技能

| # | 技能 | 何时触发 | 做什么 | 依赖的 MCP 工具 | 产出附件 |
|---|---|---|---|---|---|
| 1 | **`asset-onboarding`** | 新资产入库 | 三模态识别 → 六维资产标识 → 硬锚点自查 → `asset_ingest` 入库 | `asset_ingest` `asset_search` `std_hard_anchors` `std_clause_list` `std_verify` | `onboarding-card.json` `onboarding-card.md` |
| 2 | **`standard-verify`** | 用户要求「检查这批资产是否合规」 | 取资产 → `std_verify` 逐条校验 → 违例按条款归类讲给人听 | `asset_search` `std_clause_list` `std_verify` `std_compliance` | `compliance-report.md` |
| 3 | **`clause-proposal`** | 出现「有违规但现有判据都放行」 | 归集盲区 → 实测确认 → 取硬锚点 → 提炼候选判据 → `std_propose` | `asset_blindspots` `asset_search` `std_clause_list` `std_hard_anchors` `std_verify` `std_propose` `std_ledger` | `clause-proposal.json` `clause-proposal.md` |
| 4 | **`decision-trace`** | 用户问「这个结论凭什么」 | `asset_lineage` 取血缘 + 平台 `cite_index` → 逐跳标注来源 → 出证据链报告 | `asset_lineage` `asset_search` `std_clause_list` `std_verify` `std_ledger` | `evidence-chain.md` |
| 5 | **`evolution-run`** | 用户要求「让系统自己变好一轮」 | 编排 `evo_init → N×evo_step → evo_status → evo_finalize`，出代际曲线与审计报告 | `evo_init` `evo_step` `evo_status` `evo_finalize` `evo_archive` `std_audit` `std_ledger` | `evolution-report.md` `evolution-curve.csv` |

### 每个技能为什么做成 Skill 而不是 MCP 工具

| 技能 | 理由 |
|---|---|
| `asset-onboarding` | 核心是**流程知识**（三模态怎么判、六维顺序、卡口门槛），不是一次原子调用 |
| `standard-verify` | 判定在 MCP 侧，但「怎么把违例按条款讲给人听」是**领域话术**，且要交付一份报告文件 |
| `clause-proposal` | 承载**盲区实测纪律**（docs/23 的第一手教训）与八类判据规格，是判断标准而非调用 |
| `decision-trace` | 承载**无源断言禁令**这条硬规则；并且要做「有源跳数 ÷ 总跳数」的确定性评级 |
| `evolution-run` | 编排 + 60 秒超时的分片策略 + 曲线判读规则，正是「多步工作流 + 文档」的形态 |

### 五条共享的硬约束（写进每个技能的正文，不靠自觉）

1. **判定权在 MCP。** 技能里的 LLM 取数、编排、翻译，**不打分、不改判据、不自裁**。
2. **盲区与新判据都必须实测。** 「现有判据放行」要有 `violations == 0` 的证据才算数。
3. **无源断言不进入推理。** 证据链缺来源就标缺口，不补全、不顶替、不沉默。
4. **分数只由确定性程序给出。** 同输入同输出，`seed` 必须显式。
5. **只读优先。** 只有 `asset-onboarding` 写资产、`clause-proposal` 提判据（且写入只发生在 MCP 侧裁决之后）。

---

## 二、上传顺序

**推荐顺序（也是演示叙事顺序）：**

```
1. asset-onboarding   ← 数据进得来
2. standard-verify    ← 判得了
3. clause-proposal    ← 判不了的地方能自己长出来
4. decision-trace     ← 每个结论都说得清凭什么
5. evolution-run      ← 整个回路自己变好一轮
```

**为什么是这个顺序**：

- 后一个技能的输入是前一个技能的产物：没有入库的资产就没法校验，没有校验结果就没有盲区，
  没有盲区就没有提案，没有提案就讲不出「系统自己变好」的完整故事。
- 五个包在平台上是**互相独立**的（各自一个 ZIP、各自一条 `ag_skill_info_t` 记录），
  所以顺序不影响安装成功与否，只影响**联调与演示**时能不能顺着链路走通。
- 如果你只想先验证「技能能被调用」，先传 `standard-verify`——它依赖的工具最少、流程最短。

**挂载到一个 agent 的顺序**（前端：智能体开发 → 工具配置 → Skills 页签）：

1. 先确认 **MCP 服务已注册且健康检查通过**（工具清单里能看到 `std_*` / `evo_*` / `asset_*`）。
   技能通过 `allowed-tools` 关联工具；工具缺失时平台会在组装 agent 时报「技能所需工具缺失」。
2. 再把 5 个技能逐个启用，`version_no` 用 `0`（**草稿版本才能改技能挂载**，`version_no != 0` 会被拒）。
3. 一个 agent 同时挂 5 个技能的 `description` 会**常驻 system prompt**（技能清单每轮都付 token）。
   演示用建议全挂；生产用按角色拆成 2~3 个 agent。

---

## 三、打包与上传

**打包**（`SKILL.md` 必须在 ZIP 的根，或第一层子目录下；根目录最稳）：

```powershell
cd "项目\skills"
foreach ($s in @('asset-onboarding','standard-verify','clause-proposal','decision-trace','evolution-run')) {
    if (Test-Path "$s.zip") { Remove-Item "$s.zip" }
    Compress-Archive -Path $s -DestinationPath "$s.zip"
    Write-Output "$s.zip  $((Get-Item "$s.zip").Length) bytes"
}
```

**上传**（Nexent 平台已部署时）：

```powershell
curl.exe -X POST "http://<nexent-host>:<port>/api/skills/upload" `
  -H "Authorization: Bearer <token>" `
  -F "file=@standard-verify.zip" `
  -F "source=custom"
```

单包体积上限 `MAX_SKILL_UPLOAD_SIZE_MB = 10`，本目录 5 个包都在几十 KB 量级，远低于上限。

> 打包前先跑一次 `python _validate.py`。若本机执行过技能脚本，`scripts/__pycache__/` 会被
> `Compress-Archive` 一起打进 ZIP；自检脚本已忽略该目录，但**打包前手工删掉更干净**：
> `Get-ChildItem -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force`

---

## 四、本地自检

```powershell
cd "项目\skills"
python _validate.py              # 静态自检（5 项必检 + 5 项附加）
python _validate.py --execute    # 追加：跑每个脚本的 --selftest，逐条校验 artifact 8 项契约
```

静态自检的 5 项必检：

| # | 检查 | 依据 |
|---|---|---|
| ① | 目录名与 frontmatter `name` 一致 | `read_skill_md(skill_name=...)` 直接参与路径拼接 |
| ② | frontmatter 只有 5 个白名单键 | `skill_loader.py:14-20`，其余**静默丢弃** |
| ③ | 所有文本文件 UTF-8 无 BOM | BOM 会让 `FRONTMATTER_PATTERN` 直接失配 |
| ④ | 脚本只在 `scripts/` 下且后缀 ∈ {`.py`, `.sh`} | `skill_manager.py:962-963` |
| ⑤ | 正文 ≤ 500 行 | `skill-creator/SKILL.md:124` |

附加 5 项：frontmatter 正则可解析、`description` 非空且 ≤ 1000 字符（DB 列宽）、
`allowed-tools` 全部是已知 MCP 工具名、`script_outputs` 声明与实际文件一致、
正文引用的 `<reference>` / `<use_script>` 路径都存在。

`--execute` 会真实运行每个脚本并逐条核对 artifact 契约的 8 项要求
（stdout 单行 JSON / `status=success` / `artifacts` 是数组 / `kind=file` /
三个必填字符串非空 / `file_size_bytes` 是非负 int 且非 bool / 文件存在且**大小精确相等** /
`mime_type` 在声明的 `mime_types` 内）。

---

## 五、改造指引

| 想改什么 | 改哪里 | 不要改 |
|---|---|---|
| 判据口径、受控词表 | MCP 侧判据库（`standard/` 与 `evolve/`） | **不要**改技能脚本里的默认值——脚本里的默认值只是拿不到 `std_clause_list` 时的兜底 |
| 技能被触发的说法 | `SKILL.md` 的 `description` | 正文改了不起作用：正文只在触发后才加载 |
| 报告长什么样 | `scripts/*.py` 的 `render_*` 函数 | 不要动 `emit()` 与落盘/`stat()` 的顺序 |
| 判读阈值（停滞代数、代理落差） | 脚本命令行参数 `--max-gap` 等，或在 `SKILL.md` 里写死调用参数 | 不要改 `_validate.py` 的 5 项必检阈值 |
| 领域话术、长判据 | `references/*.md` | 不要让 `references/` 互相引用（只允许从 `SKILL.md` 一层直达） |

## 六、五个包的共同约定

1. frontmatter **只写** `name` / `description` / `allowed-tools` / `tags` / `script_outputs`。
   写 `license` / `version` / `author` 会被平台静默丢弃，等于白写。
2. `description` 必须写全「做什么 / 何时用 / 何时不用」——它是唯一的触发依据，且每轮都付 token。
3. 正文 ≤ 500 行；判据、契约、话术模板一律进 `references/`。
4. 脚本只允许 `.py` / `.sh`；脚本 **stdout 恰好一行** artifact 契约 JSON，调试信息只能进 stderr。
5. **先落盘再 `stat()`**，顺序反了 `file_size_bytes` 对不上，附件会被静默丢弃。
6. 所有文件 **UTF-8 无 BOM**。
7. 技能里出现的工具名只用 `std_*` / `evo_*` / `asset_*` 这 17 个，不臆造别的。

---

## 全部工具索引（程序生成，共 36 个）

> ★ 本索引由 `evostd.server.TOOLS` **程序生成**，说明取自各函数 docstring 首行。
> **不要手工维护** —— 手工维护的清单会漂（见 docs/42 第 144~147 节）。

| 工具 | 作用 |
|---|---|
| `asset_blindspots` | ★ 返回"被标为违规、但现有判据全部放行"的资产 —— 判据提案的靶子。 |
| `asset_ingest` | 资产入库：先做硬锚点自查与合规校验，零违例才准入库；返回六维资产标识卡。 |
| `asset_lineage` | 决策链路溯源：这条资产被哪些条款判过、结论是什么（逐条带原文）。 |
| `asset_list` | 列出当前领域包里的资产，供智能体先取数、再交给 std_verify 校验。 |
| `asset_search` | 在**当前领域的**多模态语料里检索（文本/表格/图文），返回命中与来源标识。 |
| `evo_apply` | ★ 把进化搜索的结果**接回判据库**：搜索提议，闸门裁决。 |
| `evo_archive` | 返回进化存档：最优候选、血统与后代表现（CMP 近似）。 |
| `evo_finalize` | 出最终报告（含审计与保留集落差）。这一步可能略慢，但仍是秒级。 |
| `evo_init` | 建立一次进化运行，返回 run_id。单步秒级，不触发 60s 超时。 |
| `evo_status` | 返回代际曲线与当前最优。 |
| `evo_step` | 跑 n 代（每代毫秒级）。这是"绕开 60s 超时"的关键设计。 |
| `multi_hop` | 跨文档多跳知识推理：把一条资产的证据链逐跳摊开，每跳标注来源。 |
| `std_adjudicate` | ★ 记录一次**人对待裁决事项的裁决**（只记录，不改标准/判据）。 |
| `std_adjudications` | 列出全部裁决记录（追加式，同一 ref 以最后一次为准）。 |
| `std_amendments` | 把判据冲突变成**可执行的标准修订建议**（含语料原文与字符区间作为证据）。 |
| `std_answer` | ★ 闭环：人对 std_ask 的问题作答 → 系统吸收 → 立即分型并给出下一步。 |
| `std_ask` | ★ 主动提问：这批未标注资产里，**标哪几条信息量最大**（机械排序，不靠模型）。 |
| `std_audit` | 审计：对抗探针 + 锚点自检 + 区分度 + 硬锚点覆盖率。 |
| `std_capability` | ★ 声明服务的能力指纹（**权威口径**）。 |
| `std_clause_list` | 列出判据库（含状态、来源、理由）。state 可选 adopted/rejected/uncompilable。 |
| `std_compliance` | 只返回合规率与逐条得分（适合放进循环做适应度）。 |
| `std_confirm` | ★ 人工确认一条锚点（唯一"贵"的真值来源）。label 必须是 compliant 或 violating。 |
| `std_conflicts` | 机械发现判据库内部的矛盾（标准自身不自洽时会出现）。 |
| `std_degradations` | ★ 降级台账：列出所有"悄悄走了回落路径"的地方。 |
| `std_feedback` | ★ 汇总"人机不一致"的两个方向：盲区（判据偏松）与误伤（判据偏严）。 |
| `std_grammar` | 判据语言的当前版本与演进证据：三类新判据是否真的扩大了可分辨范围。 |
| `std_hard_anchors` | 返回**机械抽取**的硬锚点（零人工真值）。label 可选 compliant/violating。 |
| `std_ledger` | 台账（追加式事件流，可回滚、可审计）。 |
| `std_parity` | ★ 能力对账：把**外部声称的工具清单**与服务权威清单做差。 |
| `std_pending` | ★ 待裁决台账：**现在有几件事在等人**。 |
| `std_propose` | ★ 提交一条候选判据，由锚点裁决。 |
| `std_reference_time` | 基准时间的当前值与**来源**（基准时间约束类判据依赖它）。 |
| `std_retire` | ★ 退役一条判据（准入的对称操作）。默认要求有危害证据。 |
| `std_rollback` | 回滚到指定快照。 |
| `std_snapshot` | 给判据库打一个内容级版本快照（平台做不到这件事）。 |
| `std_verify` | 校验一批资产。入参是 JSON 数组字符串；返回逐条违例（含条款引用与原文）。 |
