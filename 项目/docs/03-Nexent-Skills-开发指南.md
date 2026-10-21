# Nexent Skills 机制与开发指南（v2.7.0 源码实证）

> 源码基线：`<Nexent 源码目录>`，`VERSION` = `v2.7.0`（仓库根 `VERSION` 文件）。
> 本文所有结论均来自实读源码/文档，标注 `文件路径:行号`。无法执行验证的环节显式标注 `【未验证】`。
> 阅读范围：`sdk/nexent/skills/**`、`sdk/nexent/core/tools/{run_skill_script,read_skill_md,read_skill_config}_tool.py`、`sdk/nexent/core/agents/{context,prompt}/**`、`.agents/skills/**`、`.claude/skills/**`、`backend/**`、`frontend/**`、`deploy/**`、`doc/docs/zh/**`。

---

## 0. 先辨析：仓库里有三种互不相干的 "skill"

这是最容易踩的坑。三者同名但机制完全不同，**不要混用**。

| # | 名称 | 物理位置 | 谁来读 | 与 Nexent 运行时的关系 |
|---|---|---|---|---|
| A | **Nexent 平台 Skill（技能）** | 运行期磁盘 `<SKILLS_PATH>/<tenant_id>/<skill_name>/` + DB 表 `ag_skill_info_t` | Nexent 运行时：backend 组装 → SDK agent 的 `read_skill_md` / `run_skill_script` | **本文主题** |
| B | **仓库开发期 Skill** | `.agents/skills/*`、`.claude/skills/*` | **编码 Agent**（Cursor / Claude Code / 本 DSH） | 与运行时**无关**，Nexent 进程从不加载它 |
| C | **A2A Agent Skill** | `backend/consts/a2a_models.py:71`（`A2AAgentSkill`）、同文件 `:112`（`AgentCard.skills`） | A2A 协议对端 | 只是外部 Agent 的**能力声明字段**，与技能包无关 |

**证据 B**：全仓搜索 `.agents/skills` / `.claude/skills` / `openskills`，唯一命中文件是仓库根的 `AGENTS.md`（该文件用"Load rules for the task"表格把任务路由到 `.agents/skills/*/SKILL.md`）。没有任何 `.py` / `.ts` / 配置引用这两个目录 → 它们是**给写代码的 AI 读的**，不是给 Nexent 运行时读的。

**证据 C**：`doc/docs/zh/integration/integration-in/skills.md` 讲的是 A2A 接入；同目录的 `AGENTS.md` 明确写 "Skill | Agent 提供的具体能力列表"。这与 A 的"技能包"是两套语义。

**证据 A（运行时）**：
- SDK：`sdk/nexent/skills/__init__.py:3-7` 导出 `SkillLoader` / `SkillManager` / `SKILL_FILE_NAME`
- 目录常量：`sdk/nexent/skills/constants.py:3` `SKILL_FILE_NAME = "SKILL.md"`
- 磁盘根：`backend/consts/const.py:113` `CONTAINER_SKILLS_PATH = os.getenv("SKILLS_PATH")`；`deploy/env/.env.example:219` `SKILLS_PATH=/mnt/nexent-data/skills`；`deploy/docker/compose/docker-compose.yml:90,131,167,201` 挂载 `${ROOT_DIR}/skills:/mnt/nexent-data/skills`
- 官方技能 ZIP 播种目录：`backend/consts/const.py:116-118` `OFFICIAL_SKILLS_ZIP_PATH`（默认 `/mnt/nexent/official-skills-zip`），由 `backend/management/services/skill/service.py:1411,1434` `install_skills_for_tenant` 在租户初始化时解包安装

> 下文如无特别说明，"Skill" 一律指 A。

---

## 1. 目录结构与文件约定

### 1.1 权威目录结构

来自 `doc/docs/zh/backend/skills/overview.md:16-26`（官方结构图）与 `doc/docs/zh/integration/integration-in/skills.md:57-67`：

```text
<SKILLS_PATH>/<tenant_id>/<skill-name>/
├── SKILL.md              # 必需。frontmatter + 正文
├── config/
│   ├── config.yaml       # 可选。参数默认值 dict（运行时可被 per-agent 覆盖）
│   └── schema.yaml       # 可选。参数元数据（类型/必填/说明），驱动前端表单
├── scripts/              # 可选。可执行脚本，只有 .py / .sh 会被执行
│   └── *.py
├── references/           # 可选。按需读取的文档（Anthropic 约定，见 §1.5）
├── assets/               # 可选。产出物模板/字体/图标，不加载进上下文
├── examples.md           # 可选。使用示例
└── requirements.txt      # 可选。脚本依赖清单（见 custom-file-generation-skill.md:39）
```

**注意目录名是 `config/`（复数无 s）下的 `config.yaml` / `schema.yaml`**，代码里是硬编码常量：
- `backend/management/services/skill/support.py:1036,1041` → `<local_skills_dir>/<skill_name>/config/config.yaml`
- `backend/management/services/skill/support.py:1046,1051` → `.../config/schema.yaml`
- 读取端：`sdk/nexent/core/tools/read_skill_config_tool.py:77` `os.path.join(skill_dir, "config", "config.yaml")`

**磁盘布局由 `SkillManager` 决定（租户隔离）**：
- `sdk/nexent/skills/skill_manager.py:67-94` `resolve_tenant_dir`：`tenant_id=None` → 直接用 `base_skills_dir`；否则 `<base>/<tenant_id>`，并做绝对路径/`..`/分隔符/`basename` 五重校验
- `sdk/nexent/skills/skill_manager.py:96-101` `resolve_skill_dir` → 落到 `paths.py:59-70` `resolve_skill_path`（realpath 包含性 + 逃逸符号链接拒绝）
- `sdk/nexent/skills/paths.py:27-36` `validate_path_component`：技能名必须是**单个安全目录组件**（非空、非 `.`/`..`、不含 `/` `\` `\x00`、非绝对路径、非盘符路径、`basename(v)==v`）

> ⚠️ 因此 **Skill 的目录名 = 技能的"物理主键"之一**。`read_skill_md(skill_name=...)`、`run_skill_script(skill_name=...)` 传的 `skill_name` 直接参与路径拼接，必须与目录名**逐字符相同**。

### 1.2 SKILL.md 的 frontmatter 字段（白名单是硬约束）

Nexent **只认 5 个键**，其余全部静默丢弃：

`sdk/nexent/skills/skill_loader.py:14-20`
```python
_ALLOWED_SKILL_META_KEYS = frozenset([
    "name",
    "description",
    "allowed-tools",
    "tags",
    "script_outputs",
])
```
过滤发生在 `sdk/nexent/skills/skill_loader.py:65`：
```python
filtered_meta = {k: v for k, v in meta.items() if k in _ALLOWED_SKILL_META_KEYS}
```
注释原文（`:64`）：`# Filter to only known keys to tolerate extra fields like 'author'`。

| 字段 | 必填 | 类型 | 落地位置 | 说明 |
|---|---|---|---|---|
| `name` | **是** | str | `ag_skill_info_t.skill_name` + 目录名 | 缺失 → `skill_loader.py:59-60` 抛 `ValueError("Skill must have 'name' field")` |
| `description` | **是** | str | `ag_skill_info_t.skill_description`（`VARCHAR(1000)`，`db_models.py:1917`） | 缺失 → `:61-62` 抛错。**这是 agent 唯一的发现依据**（见 §2.4） |
| `allowed-tools` | 否 | list[str] | **映射为工具关联** `ag_skill_tools_rel_t` | `service.py:450,457`：`skill_db.get_tool_ids_by_names(allowed_tools, tenant_id)`，名称→ID 后写入关系表 |
| `tags` | 否 | list[str] | `ag_skill_info_t.skill_tags` (JSON) | `skill_loader.py:77-82` `_normalize_tags`：非 list → `[]`；逐项 strip；非 str/空串丢弃 |
| `script_outputs` | 否 | dict | **不落库**，运行时从 SKILL.md 现读 | 声明哪些脚本可产出可下载 artifact。见 §1.4 |

**生成端**：`skill_loader.py:213-239` `to_skill_md()` 只写这 5 个键（`name`/`description` 必写 `:216-219`；其余非空才写 `:221-226`），用 `yaml.dump(..., allow_unicode=True, sort_keys=False, default_flow_style=False, indent=2, width=float("inf"))`。**这意味着通过后端 API 创建技能时，正文以外的东西会被重新序列化**——`tags` 顺序保留，但注释全丢。

**解析容错链**（3 层，实读 `skill_loader.py`）：
1. `_fix_yaml_frontmatter`（`:84-141`）：为容忍 LLM 生成的非规范 YAML 而做的"补引号"预处理。跳过空行/注释（`:97-99`）、跳过缩进行（块标量内容，`:103-105`）、保留块标量指示符 `| |+ |- > >+ >-`（`:116-119`）、**`tags` 与 `allowed-tools` 不做引号化**（`:127-129`，避免把结构化列表变成字符串）。值里含 `:{}[],&*#?|-<>%=!%@\`` 之一且未加引号 → 整体裹双引号并转义内部引号（`:132-137`）。
2. `yaml.safe_load` 失败 → `logger.warning` 后回落
3. `_extract_frontmatter_by_regex`（`:143-203`）：纯正则兜底，能处理 `description: >` 块标量（`:173-185`）、`tags: [a, b]`（`:192-195`）、`allowed-tools: [x]`（`:198-201`）

> ⚠️ **兜底路径不解析 `script_outputs`**（对照 `:143-203` 只提取 name/description/tags/allowed-tools）。所以 YAML 一旦走到正则兜底，**文件产出能力会静默失效**。这是排查"脚本跑了但没附件"的第一嫌疑点。

### 1.3 frontmatter 分隔符与编码（已实测）

`SKILL.md` **必须**以 `---` 开头，正则 `sdk/nexent/skills/skill_loader.py:26`：
```python
FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
```
缺失 → `:43-44` `ValueError("SKILL.md must have YAML frontmatter")`。

编码由 `sdk/nexent/skills/text_codec.py:17-49` `decode_skill_text` 决定，优先级：`utf-8-sig`（BOM）→ `utf-32` → `utf-16` → UTF-16LE/BE 启发式（NUL 比例）→ `utf-8` → `gb18030` / `big5`（要求含 CJK `\u3400-\u9fff`）→ `charset_normalizer` 且 `chaos <= 0.3`。**严格解码，不静默替换**。

**我在本机跑了一组真实解析对照**（复刻 `FRONTMATTER_PATTERN` + `yaml.safe_load`，样本为 §4 的示例技能）：

| 输入 | frontmatter 正则 | 解析出的键 |
|---|---|---|
| LF，无 BOM | ✅ 通过 | `allowed-tools, description, name, script_outputs, tags`（5/5，`unknown=none`） |
| CRLF，无 BOM | ✅ 通过 | 同上 |
| **LF + UTF-8 BOM，按 `utf-8` 解码** | ❌ **失败** | — |
| **LF + UTF-8 BOM，按 `utf-8-sig` 解码（= `decode_skill_text` 的行为）** | ✅ 通过 | 同上 |
| GBK 编码（走 `gb18030` 回落） | ✅ 通过 | 同上 |

结论：**CRLF 和 GBK 都能吃；BOM 只有在走 `decode_skill_text` 时才安全**。`SkillLoader.load()`（`:28-36`）确实调用了 `decode_skill_text`，所以正常路径没问题；但**任何绕开它、直接 `Path.read_text()` 自建解析器的旁路代码都会被 BOM 打死**。

> 复现脚本落在 `%TEMP%\nexent-skill-check\check.py`（本次会话生成，可重跑）。

### 1.4 `script_outputs`：文件产出契约（决定"能不能给用户交付文件"）

这是 Nexent 相对 Anthropic Skills 的**原生扩展**，也是最容易被忽视的能力开关。权威文档：`doc/docs/zh/user-guide/resource-repository/custom-file-generation-skill.md`。

声明写法（同文档 `:49-59`）：
```yaml
script_outputs:
  scripts/generate_report.py:
    kind: file                      # 必填，固定 file
    mime_types:                     # 建议必填
      - application/pdf
```
- 键 = **相对技能根目录的脚本路径**，统一正斜杠（文档 `:43`）
- 匹配前会去掉开头 `./` 并把反斜杠归一为正斜杠（文档 `:78`；实现见 `sdk/nexent/core/tools/run_skill_script_tool.py:138-144` `_normalize_script_path`）
- 脚本成功时**必须**在 stdout 打印 JSON（文档 `:82-97`）：

```json
{
  "status": "success",
  "artifacts": [
    {
      "kind": "file",
      "absolute_path": "/mnt/nexent/output/monthly-report.docx",
      "file_name": "monthly-report.docx",
      "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "file_size_bytes": 12800
    }
  ]
}
```

**SDK 侧校验（实读实现，比文档更细）**：`run_skill_script_tool.py:146-197` `_extract_file_artifacts`，逐条筛：
1. `:154-158` 从 SKILL.md 现读 `script_outputs`，取该脚本的声明；`:159` `kind != "file"` → 直接返回空
2. `:161-167` stdout 必须能 `json.loads` 成 dict（`:125-136`）、`status == "success"`、`artifacts` 是 list；`mime_types` 收成集合
3. `:170-171` 每条 artifact 的 `kind` 必须是 `file`
4. `:172-177` `absolute_path`/`file_name`/`mime_type` 必须是非空 str
5. `:178-179` `file_size_bytes` 必须是非负 int，**且不能是 bool**（Python 里 `bool` 是 `int` 子类，这里显式排除）
6. `:180-181` 文件必须真实存在
7. `:182-188` **磁盘实际大小必须精确相等**，否则打 warning 丢弃
8. `:189-195` 若声明了 `mime_types`，artifact 的 `mime_type` 必须在其中，否则打 warning 丢弃

通过后由 `:199-221` `_publish_artifacts` 以 `ProcessType.SKILL_ARTIFACT` 结构化事件发布（`:221` `self.observer.add_message("", ProcessType.SKILL_ARTIFACT, content)`），后端在 `backend/management/services/agent/run.py:535` 识别 `chunk_type == ProcessType.SKILL_ARTIFACT.value` 并落成 `skill_files` 流事件。

> **关键推论**：`print()` 出来的 JSON **不会**自动变附件（文档 `:27` 明确说"普通文本、执行日志和脚本标准输出中的 JSON 不会被自动转换为文件附件"）。只有声明 + 完整契约 + 大小精确匹配才发布。这是"脚本跑通了但用户拿不到文件"的唯一原因集合。

### 1.5 `references/` 与 `scripts/` 各放什么

**Nexent 自身的权威定义在 `.claude/skills/skill-creator/SKILL.md`（Anthropic 上游）**，Nexent 的实现与之兼容：

| 目录 | 定义 | 出处 | Nexent 运行时行为 |
|---|---|---|---|
| `scripts/` | "Executable code (Python/Bash/etc.) for tasks that require deterministic reliability or are repeatedly rewritten" | `skill-creator/SKILL.md:73-80` | **只有 `.py` / `.sh` 可执行**（`skill_manager.py:857-862`、`:962-963`、`run_skill_script_tool.py:857-862`）；其它后缀 → `ValueError: Unsupported script type` |
| `references/` | "Documentation and reference material intended to be loaded as needed into context" | `skill-creator/SKILL.md:82-91` | **不自动进上下文**，必须 agent 显式 `read_skill_md(skill_name, additional_files=["references/xxx.md"])` |
| `assets/` | "Files not intended to be loaded into context, but rather used within the output Claude produces" | `skill-creator/SKILL.md:93-100` | 脚本可通过相对路径读取；不占 token |
| **不要放** `README.md` / `CHANGELOG.md` / `INSTALLATION_GUIDE.md` 等 | "A skill should only contain essential files that directly support its functionality" | `skill-creator/SKILL.md:102-112` | — |

**渐进披露（三级加载）**，`skill-creator/SKILL.md:114-120`：
1. 元数据（name + description）——**始终在上下文**，约 100 词
2. SKILL.md 正文——技能触发时加载，建议 < 5k 词 / < 500 行（`:124`）
3. 打包资源——按需，因为脚本执行**不需要**读进上下文

**`references/` 的两条硬规矩**（`skill-creator/SKILL.md:197-200`）：
- 引用**只允许一层深**，所有 reference 必须直接从 SKILL.md 链接
- 超过 100 行的 reference 文件，顶部加目录

**Nexent 正文里的资源引用语法**（`sdk/nexent/core/prompts/zh/agent/agent_worker.yaml:23` 定义，`doc/docs/zh/integration/integration-in/skills.md:135-159` 举例）：
> 技能指南中的 `<use_script path="..." />`、`<reference path="..." />`、单反引号路径、仅含单行路径的三反引号代码块和自然语言引用都表示待按需读取的资源。只读取当前任务需要的文件。

即：`<reference>` / `<use_script>` **不是运行时指令，是给 LLM 看的"资源清单提示"**。真正生效的仍然是 `read_skill_md` / `run_skill_script` 调用。

### 1.6 两套 frontmatter 校验规则的冲突（必须知道）

| 校验器 | 允许的键 | 出处 |
|---|---|---|
| **Nexent 运行时** | `name`, `description`, `allowed-tools`, `tags`, `script_outputs` | `skill_loader.py:14-20` |
| **Anthropic skill-creator 校验器** | `name`, `description`, `license`, `allowed-tools`, `metadata` | `.claude/skills/skill-creator/scripts/quick_validate.py:42` |

两者**交集只有 `name` / `description` / `allowed-tools`**。后果：

| 场景 | 结果 |
|---|---|
| 写了 `license:`（skill-creator 允许） | Nexent **静默丢弃**（`skill_loader.py:65` 过滤） |
| 写了 `tags:` / `script_outputs:` | skill-creator 的 `quick_validate.py:45-50` **报错拒收**（`Unexpected key(s)`），无法打成 `.skill` 包 |
| 只想让 Nexent 用 | 遵循 Nexent 白名单，忽略 skill-creator 校验 |
| 想同时兼容两边 | 只用 `name` / `description` / `allowed-tools`；把 tags/script_outputs 的诉求分别用别的机制实现 |

**命名规范冲突**：`quick_validate.py:63-71` 强制 `^[a-z0-9-]+$`、≤64 字符、首尾不能是连字符、不能有 `--`；而 **Nexent 的 `SkillLoader` 完全不校验命名规范**，只在 `paths.py:27-36` 校验"是否是安全目录组件"。所以 `My_Skill.v2` 在 Nexent 里能建，在 skill-creator 里会被拒。

**已存在的真实不一致**：`.claude/skills/prompts-writing/SKILL.md:2` 写的是 `name: prompt-writing`，但目录名是 `prompts-writing` → **name 与目录名不匹配**。这在开发期 Skill（B 类）里只是观感问题；**如果照搬到 A 类，会导致 `read_skill_md("prompt-writing")` 找不到目录**（`skill_manager.py:96-101` 用 skill_name 拼路径）。

---

## 2. 发现 → 加载 → 注入上下文的完整链路

### 2.1 全景图（谁读、何时读、读进谁的上下文）

```
【写盘】前端/API 上传
   │  frontend/services/skillService.ts:305 submitSkillFromFile
   │  → POST /skills/upload            backend/apps/skill_app.py:226
   │  → SkillService.create_skill_from_file      service.py:356
   │  → SkillManager.save_skill / _upload_zip_files
   ▼
磁盘: <SKILLS_PATH>/<tenant_id>/<name>/SKILL.md (+scripts/ references/ config/)
DB  : ag_skill_info_t (name/description/tags/content/config_values/config_schemas)
      ag_skill_tools_rel_t (allowed-tools → tool_id)
   │
   │【绑定】POST /skills/instance/update   skill_app.py:480
   ▼
DB  : ag_skill_instance_t (skill_id, agent_id, tenant_id, enabled, version_no, config_values)
   │
   │【组装】每次 run 都重新组装
   ▼
backend/agents/create_agent_info.py:1748  _get_skills_for_template(...)
   → SkillService.get_enabled_skills_for_agent(...)          service.py:797-843
   → 只取 name + description                                  create_agent_info.py:861-864
   ▼
backend/agents/create_agent_info.py:1850  build_context_inputs(skills=skills, ...)
   → backend/utils/context_utils.py:78-115  (薄适配层)
   ▼
sdk/nexent/core/agents/prompt/composer.py:223-234
   → ContextItemInput(id="skill:<name>", type=SKILL, priority=40,
                      metadata={"render_group":"skills",
                                "usage_guidance": <skill_usage 段落>})
   ▼
【渲染成消息】sdk/nexent/core/agents/context/rendering.py:211-214 (render_group="skills")
   → sdk/nexent/core/agents/context/formatting.py:44-61  _format_skills_inventory
   → role = "system" (rendering.py:236)
   ▼
注入 LLM 的 system 消息：
   ### 技能
   你拥有以下技能：

   <available_skills>
     <skill>
       <name>...</name>
       <description>...</description>
     </skill>
   </available_skills>

   ### 技能使用要求
   先根据 <available_skills> 中的 description 判断是否匹配。匹配时先调用
   read_skill_md(skill_name="技能名", additional_files=[])，再按指南执行。
   ...
```

### 2.2 第一跳：`SkillManager` 直读磁盘，无缓存

- `sdk/nexent/skills/skill_manager.py:118-142` `list_skills`：遍历 `<tenant_dir>/*`，**只有存在 `SKILL.md` 的目录才算技能**（`:134-135`）。`deploy/docker/assets/scripts/sync_skill_directory.py:346-351` 的迁移脚本用同一条判据。
- `:144-156` `_get_skill_metadata`：**为了拿 name/description/tags，会把整个 skill 完整 load 一遍**（`load_skill` → `SkillLoader.load` 读全文件 + 解析）。列表页开销 = N 次全文件读。**无缓存**。
- `:158-176` `load_skill`：拼 `<tenant>/<name>/SKILL.md`，**每次调用都重新读盘**。→ **改了磁盘文件，下一次 run 立即生效，无需重启**（见 §7）。

DB 侧对应：`backend/database/skill_db.py:367` `list_skills`、`:412` `get_skill_by_name`、`:432` `get_skill_by_id`、`:194` `search_skills_for_agent`、`:737` `get_tool_ids_by_names`。

**DB 与磁盘的一致性由 `_enrich_configs_from_yaml` 兜住**：`service.py:140-179` 在读技能时，把磁盘 `config/config.yaml` 覆盖到 `config_values`（`:157-166`），把磁盘 `config/schema.yaml` 覆盖到 `config_schemas`（`:168-178`，注释 `:167` 明说 "schema.yaml takes precedence over DB config_schemas"）。**文件不存在则把该 key 从响应里 pop 掉**（`:165-166`、`:177-178`），避免返回脏数据。

> **推论**：`config/` 下两个 YAML 是**磁盘权威**；`description`/`content`/`tags` 是**DB 权威**（磁盘 SKILL.md 的 frontmatter 只在 `SkillManager` 被调用时才读，而 agent 拿到的 name/description 来自 DB）。二者可能在"改盘未改库"时漂移。

### 2.3 第二跳：技能如何绑定到 agent

**绑定表 = `ag_skill_instance_t`**，`deploy/sql/migrations/v2.0_merged_migrations.sql:74-88`：
```sql
CREATE TABLE IF NOT EXISTS nexent.ag_skill_instance_t (
    skill_instance_id SERIAL4 NOT NULL,
    skill_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL,
    user_id VARCHAR(100),
    tenant_id VARCHAR(100),
    enabled BOOLEAN DEFAULT TRUE,
    version_no INTEGER DEFAULT 0 NOT NULL,
    ...
    CONSTRAINT ag_skill_instance_t_pkey PRIMARY KEY (skill_instance_id, version_no)
);
```
SQLAlchemy 模型：`backend/database/db_models.py:1947-1968`（`:1967` `enabled` 注释 "Whether this skill is enabled for the agent"）。

**这是"技能↔agent"的多对多 + 每 agent 独立配置**：
- 一个技能可挂到多个 agent；一个 agent 可挂多个技能
- 每个实例独立 `enabled` 与 `config_values`（per-agent 参数覆盖，见 `service.py:825-826`：`effective = dict(skill.config_values); effective.update(instance.config_values)`）
- `version_no`：0 = 草稿，≥1 = 已发布快照

**取"某 agent 启用的技能"**：`backend/management/services/skill/service.py:797-843`
```python
enabled_skills = skill_db.search_skills_for_agent(agent_id=agent_id, tenant_id=tenant_id, version_no=version_no)
...
merged = {
    "skill_id": skill_id, "name": skill.get("name"),
    "description": skill.get("description", ""),
    "content": skill.get("content", ""),
    "enabled": skill_instance.get("enabled", True),
    "tool_ids": skill.get("tool_ids", []),
    "config_schemas": skill.get("config_schemas") or [],
    "config_values": effective_config_values,
}
```
（`:814-838`）

### 2.4 第三跳：注入进上下文的**只有 name + description**

`backend/agents/create_agent_info.py:834-867`：
```python
def _get_skills_for_template(agent_id, tenant_id, version_no=0, runtime_skill_snapshot=None):
    ...
    if runtime_skill_snapshot is None:
        skill_service = SkillService()
        enabled_skills = skill_service.get_enabled_skills_for_agent(...)
    else:
        enabled_skills = runtime_skill_snapshot
    return [
        {"name": s.get("name", ""), "description": s.get("description", "")}
        for s in enabled_skills          # ← :861-864 只留两个字段
    ]
```

然后 `:1848-1850`：
```python
skills = _get_skills_for_template(agent_id, tenant_id, version_no,
                                 runtime_skill_snapshot=runtime_skill_snapshot)
...
context_items = build_context_inputs(..., skills=skills, ...)
```

**Composer 构造 ContextItem**，`sdk/nexent/core/agents/prompt/composer.py:223-234`：
```python
if include_skills and skills:
    for index, skill in enumerate(skills):
        name = str(skill.get("name", index))
        inputs.append(ContextItemInput(
            id=f"skill:{name}", type=ContextItemType.SKILL, content=dict(skill),
            source=(f"skill:{name}",), priority=40,
            metadata={
                "render_group": "skills", "language": language,
                "usage_guidance": composer.render_system_section("skill_usage"),
                "authority": "agent",
            },
        ))
```

**渲染成 system 消息**，`sdk/nexent/core/agents/context/rendering.py:186-237` `_render_group`：
- `:211-214`：`ContextItemType.SKILL` → `_format_skills_inventory(contents, language)`，再**追加** `usage_guidance`
- `:236`：`role = "user" if first.type == MEMORY else "system"` → **技能清单是 `system` 角色**

**`_format_skills_inventory`**，`sdk/nexent/core/agents/context/formatting.py:44-61`（注意 `:48` 注释："usage policy comes from the role YAML"，即 SDK 只负责格式，策略文案由角色 prompt 提供）：
```python
labels = load_prompt(language, "agent/context_sections")["skills"]
lines = [labels["heading"], labels["intro"], "", "<available_skills>"]
for skill in skills:
    lines.extend([
        "  <skill>",
        f"    <name>{skill.get('name', '')}</name>",
        f"    <description>{skill.get('description', '')}</description>",
        "  </skill>",
    ])
lines.append("</available_skills>")
```

标题/引导语来自 prompt 文件 `sdk/nexent/core/prompts/zh/agent/context_sections.yaml:26-29`：
```yaml
skills:
  heading: '### 技能'
  intro: 你拥有以下技能：
  unavailable: '- 当前没有可用的技能'
```

**行为策略来自角色 YAML** `sdk/nexent/core/prompts/zh/agent/agent_worker.yaml:17-24`（`agent_manager.yaml:17-24` 为同构的英文/manager 版）：
```yaml
skill_usage: |-
  ### 技能使用要求
  先根据 `<available_skills>` 中的 description 判断是否匹配。匹配时先调用 `read_skill_md(skill_name="技能名", additional_files=[])`，再按指南执行。
  读取指定参考文件时调用 `read_skill_md(skill_name="技能名", additional_files=["examples.md"])`；读取配置时调用 `read_skill_config(skill_name="技能名")`。
  执行技能自带脚本时调用 `run_skill_script(skill_name="技能名", script_path="scripts/example.py", params="", source="skill")`；只有执行本轮生成的脚本时才使用 `source="workspace"`。
  `run_skill_script(source="workspace")` 的 `script_path` 从运行工作区根目录解析，因此执行本轮生成的脚本时传入 `outputs/<脚本名>`；脚本自身仍向当前 outputs 目录写入裸文件名。
  技能指南中的 `<use_script path="..." />`、`<reference path="..." />`、单反引号路径、仅含单行路径的三反引号代码块和自然语言引用都表示待按需读取的资源。只读取当前任务需要的文件。
  `run_skill_script` 失败后，依据错误修复并重新运行；成功前不得校验或上传产物，也不得用其他命令绕过。多个技能按依赖顺序执行。
```

**排序与裁剪**：
- `sdk/nexent/core/agents/context/models.py:29-43` `ContextSection`：`SKILL = 20`（在 `TOOL = 10` 之后、`WORKER_AGENT = 30` 之前）→ 稳定前缀顺序固定
- `models.py:370-371` `_compact_skill`：压缩时**只保留** `name` / `trigger` / `constraints` / `description` 四键。注意 `trigger` / `constraints` **在 Nexent 的 frontmatter 白名单里不存在**（`:14-20`），所以实际只有 `name` + `description` 能存活 → 压缩对技能清单**无实质损失**
- **评分/降级是 TODO**：`sdk/nexent/core/agents/context/handlers/skill_handler.py:16-21` `score()` 直接 `return 1.0`，注释 `# TODO(W13): Implement scoring`；`:23-38` `reduce()` 是 passthrough。→ **所有技能清单等权、永不被上下文预算裁掉**

### 2.5 上下文注入的另外两条通道（不只清单）

除了 `<available_skills>` 清单，技能还会以两种方式影响上下文：

**(a) 工具描述里的 `builtin_tools` 文案**：`sdk/nexent/core/prompts/zh/agent/context_sections.yaml:82-85`
```yaml
builtin_tools:
  run_skill_script: 在 Docker 沙箱中执行已启用技能的脚本或本轮生成的工作区脚本。普通智能体代码不得用 Shell 执行系统命令。
  read_skill_md: 读取已启用技能的执行指南及按需附加文件。
  read_skill_config: 读取技能配置并返回工作流变量。
```
（`formatting.py:63-97` `_format_tools_description` 渲染 `### 工具` 段；MCP 工具额外加 ` [MCP]` 标记 `:87-90`）

**(b) 沙箱 workspace 提示**：`agent_worker.yaml:12-16` `sandbox_workspace_guidance`，说明上传文件在 `inputs`、产物写 `outputs`、**禁止 `subprocess`/`os.system`/Shell**。这直接决定了技能脚本是"唯一合法的系统能力出口"。

### 2.6 三个技能工具是怎么被注入 agent 的

`backend/agents/create_agent_info.py:1018-1186` `_get_skill_script_tools`：
- `:1054-1073` 先算 `skill_config_values = {skill_name: config_values}`（来源：DB `get_enabled_skills_for_agent` 或 `runtime_skill_snapshot`）
- **`:1077` `if skill_config_values:` —— 这是总开关**：agent 一个技能都没启用 → **三个工具全部不注册**
- `:1078-1098` `RunSkillScriptTool`：`params.local_skills_dir = skill_snapshot_root or CONTAINER_SKILLS_PATH`；`isolated_skills_root = bool(skill_snapshot_root)`；`workspace_path`；**`authorized_skill_names = sorted(skill_config_values)`**
- `:1099-1115` `ReadSkillMdTool`（`authorized_skill_names` 仅在 `runtime_skill_snapshot is not None` 时注入，`:1109-1110`）
- `:1116-1132` `ReadSkillConfigTool`（额外 `config_overrides = skill_config_values`）
- `metadata = skill_context = {"agent_id", "tenant_id", "version_no"}`（`:1037-1041`）

SDK 侧实例化：`sdk/nexent/core/agents/nexent_agent.py:569-612`（三个 `elif` 分支），把 `params` / `metadata` 映射成构造函数参数。

**运行时技能快照（Workbench 路径）**：`backend/agents/create_agent_info.py:789-817` `_materialize_runtime_skill_snapshot`
- 把快照里的 `files` 逐个写到 `<workspace_path>/.skill_snapshot/<tenant_id>/<skill_name>/...`（`:796-813`）
- **每个技能必须落出 `SKILL.md`，否则抛 `WorkbenchError("RUNTIME_SKILL_DEPENDENCY_UNAVAILABLE")`**（`:814-815`）
- 之后 `local_skills_dir` 指向这个快照根 + `isolated_skills_root=True` → `SkillManager` 用 `object.__new__` 绕过单例直接注入 `base_skills_dir`（`run_skill_script_tool.py:114-119`、`read_skill_md_tool.py:84-89`）

> 这是"技能随 run 冻结"的机制：会话级技能挂载（`backend/consts/model.py:877-883` `RuntimeSkillMount`，`:910` `skill_mounts` 上限 20）会被物化成 run 私有目录，与全局 `SKILLS_PATH` 隔离。

### 2.7 ⚠️ 两个"看起来在注入、实际没人调用"的遗留函数

- `sdk/nexent/skills/skill_manager.py:694-733` `SkillManager.build_skills_summary` → 产出 `<skills>...</skills>` XML（**注意标签名与真正的 `<available_skills>` 不同**）
- `backend/management/services/skill/service.py:875-951` `SkillService.build_skills_summary` → 同样产出 `<skills>`

**全仓 grep `build_skills_summary`：只有这两个定义 + 测试文件（`test/sdk/skills/test_skill_manager.py`、`test/backend/services/test_skill_service.py`），没有任何生产调用点。**

→ 结论：**这两个函数是死代码/遗留 API**，真正生效的注入路径是 §2.4 的 ContextManager/Composer 链路。排查"技能为什么没进上下文"时，不要去改 `build_skills_summary`。

---

## 3. agent 如何"调用"一个 Skill —— 三种机制叠加，不是单一工具调用

**这是理解 Skill 能力边界的核心。** Nexent 的 Skill **不是**一个工具调用，而是**三层机制串联**，每层解决不同问题：

| 层 | 机制 | 载体 | 何时发生 | 进谁 |
|---|---|---|---|---|
| **L1 发现** | **提示注入**（system message） | `<available_skills>` 里每个技能的 `name` + `description` | **每一轮 LLM 调用都在** | LLM 的 system 上下文 |
| **L2 加载** | **工具调用**（读文件） | `read_skill_md` / `read_skill_config` 的返回值 | agent 自己决定调用时 | LLM 的 tool observation |
| **L3 行动** | **脚本执行**（子进程 / 沙箱） | `run_skill_script` 的返回值 + `skill_artifact` 事件 | agent 读完指南后调用时 | LLM observation + 产物上传管道 |

### 3.1 L1 发现层：纯文本，无副作用

- 常驻成本 = 所有已启用技能的 `description` 之和。**`description` 是被"每一轮都付钱"的字段**（对应 `skill-creator/SKILL.md:309-312`：description 是唯一触发机制，"Include all when-to-use information here — Not in the body"）
- 正文**不在**这一层（§2.4 只取 name/description）
- 无裁剪（`skill_handler.py:16-21` 是 TODO）→ 技能写多了会线性膨胀 system prompt

### 3.2 L2 加载层：`read_skill_md` / `read_skill_config`

**`read_skill_md`**（`sdk/nexent/core/tools/read_skill_md_tool.py`）

```
name = "read_skill_md"                                    # :41
description = "Read a skill execution guide and optional files from the tenant-scoped skill directory."   # :42
inputs = {
  "skill_name": {"type": "string", "description": "Name of the skill to read."},
  "additional_files": {"type": "array",
     "description": "Optional additional files relative to the skill root.", "nullable": True},
}
output_type = "string"                                    # :51
```

行为（`:139-196` `execute`）：
1. `:154-155` 授权闸门：`authorized_skill_names is not None and skill_name not in ...` → 返回 `"Skill access is not authorized"`（**字符串，不抛异常**）
2. `:159-160` `skill_name` 为空 → `_read_direct_file`（`:206-237`）：从 tenant 根目录直接读，等价于"读该租户下任意文件"
3. `:162-165` `load_skill` 失败 → `"Skill not found: <name>"`
4. **`:177-181` 关键默认行为**：`additional_files` 为空 → **只读 `SKILL.md`**
5. **`:182-191` 关键陷阱**：一旦传了 `additional_files`，**只读列出的文件，SKILL.md 不再自动附带**（除非显式写进列表）
6. **`:133-134` frontmatter 被剥掉**：`full_path.endswith('.md')` → `_strip_frontmatter`（`:104-105` 正则 `r'^---\s*\n.*?\n---\s*\n'`）。→ **agent 永远看不到 frontmatter**，看到的只有正文
7. `:118-123` 路径候选：`file_path`、`file_path + ".md"`、去前导 `/` 的两种 → **可以省略 `.md` 后缀**
8. `:12` `_SUPPORTED_ENCODINGS = ("utf-8-sig", "gb18030")`，严格解码，失败抛 `UnicodeError`（`:33-35`）。注意这个工具的编码白名单**比 `decode_skill_text` 窄**（没有 utf-16/charset_normalizer 兜底）
9. `:127-129` 越界保护：**仅当 `authorized_skill_names is not None` 时**才做 `is_relative_to` 校验 → 非 Workbench 路径下越界读依赖 `os.path.join` 的自然拼接（弱于 `SkillManager` 的 realpath 校验）

**`read_skill_config`**（`sdk/nexent/core/tools/read_skill_config_tool.py`）

```
name = "read_skill_config"                                # :17
description = "Read config.yaml from a tenant-scoped skill directory."   # :18
inputs = {"skill_name": {...}}                            # :19-21
```
- `:61-65` **已授权路径（Workbench）**：直接 `json.dumps(self.config_overrides.get(skill_name, {}))` —— **不读盘**，返回的是后端算好的 effective 值
- `:70-99` 非授权路径：读 `<skill_dir>/config/config.yaml`（`:77`），`yaml.safe_load`（`:83`），非 dict 报错（`:88-89`），再 `update(config_overrides.get(skill_name))`（`:91-92`）后返回 JSON
- 报错文案：`[Error] config.yaml not found in skill: <name>`（`:79`）

> **返回给 LLM 的是 JSON 字符串**（`json.dumps(..., ensure_ascii=False, indent=2)`，`:65`、`:94-95`）。

### 3.3 L3 行动层：`run_skill_script`

```
name = "run_skill_script"                                 # :19
inputs = {
  "skill_name":  "Name of the skill containing the script.",
  "script_path": "For source='skill', path relative to the skill root. ...",
  "params":      "Optional raw command-line arguments for the script.",   # nullable
  "source":      "Script source: 'skill' (default) or 'workspace'.",      # default "skill"
}
output_type = "string"                                    # :55
```
（`sdk/nexent/core/tools/run_skill_script_tool.py:20-54`；工具描述 `:20-31` 明确写了 `source='workspace'` 时"code executor 写的裸 `build.js` 实际位于 `outputs/build.js`"、脚本 CWD 是本轮 outputs 目录、失败必须修复重跑、**"Do not use subprocess, os.system, or shell calls from ordinary agent code for system commands; use a skill-bundled wrapper"**）

**执行路径分叉**（`:222-321` `execute`）：
1. `:250-252` `source` 只允许 `"skill"` / `"workspace"`，否则 `ValueError`
2. **`:253-256` 授权闸门**：`authorized_skill_names is not None and skill_name not in ...` → **抛 `PermissionError`**（注意与 `read_skill_md` 返回字符串不同，这里是硬失败）
3. `:258-279` **有 `execution_backend`（沙箱）**：调用 `backend(manager=..., skill_name=..., script_path=..., params=..., tenant_id=..., working_directory=...)`（`source != "skill"` 时额外传 `source`）。返回 str 且含 `error` 字段 → 抛 `SkillScriptExecutionError("Sandbox script failed. Repair the script and rerun run_skill_script before continuing: ...")`（`:275-279`）——**这个异常被 `:317-318` 显式重抛，不吞**
4. `:280-293` **无 `execution_backend`（进程内）**：`source == "workspace"` → 直接 `RuntimeError("Workspace scripts require an available Docker sandbox execution backend")`（`:281-284`）；否则 `manager.run_skill_script(...)`（`:288-293`）
5. `:294-295` `on_complete(result)` 回调（用于把沙箱产物拉回工作区，见 `nexent_agent.py:972`）
6. `:296-301` 若 `source == "skill"` → `_extract_file_artifacts` → `_publish_artifacts`
7. 异常映射（`:303-321`）：`SkillNotFoundError` → `[SkillNotFoundError] ...`、`SkillScriptNotFoundError` → `[SkillScriptNotFoundError] ...`、`FileNotFoundError`、`TimeoutError`、兜底 `[UnexpectedError] ...`。**多数异常被转成字符串返回给 LLM，让它自我修复**

**脚本路径信任边界**（`sdk/nexent/skills/skill_manager.py:864-965` `resolve_skill_script`，注释 `:872-877` 自称 "the common trust boundary used by both local execution and sandbox execution"）：
- `:878-880` 技能目录不存在 → `SkillNotFoundError("Skill '<name>' not found.")`
- `:893-898` 剥掉外层引号（LLM 从反引号/代码块里抠路径时常带引号）
- `:903-906` 去前导 `./` / `.\` / `/` / `\`，统一分隔符
- `:929-934` `_resolve_skill_file_path` 做 realpath 包含性校验；失败 → `"resolved path escapes the skill root directory"`
- `:940-960` **扩展名回落**：无扩展名时依次试 `.py` / `.sh`，且要求仍在技能根内
- `:962-963` **只允许 `.py` / `.sh`**
- `:911-926` 失败时构造"友好错误"：`reason` / `resolved to` / `skill root` / `tried` / **`available scripts`**（`:913` `_list_available_scripts`，`:967-995` 遍历技能根与 `scripts/` 收集 `.py`/`.sh`）。**这是 LLM 自我纠错的关键**——路径写错时它能看到正确清单。

**进程内执行**（无沙箱时）：
- `:997-1043` `_run_python_script`：`cmd = [sys.executable, script_path] + shlex.split(params)`；`timeout=300`（硬编码）；`env` 从 `os.environ` 复制，**若给了 `working_directory` 则额外注入 `NEXENT_WORKSPACE=<working_directory>`**（`:1022`）并 `makedirs(exist_ok=True)`；`cwd=working_directory`。注释 `:1014-1016` 说明用 `sys.executable` 是为了让 `python-docx` 等已安装包可用
- `:1045-1087` `_run_shell_script`：`cmd = ["bash", script_path] + shlex.split(params)`，同样 `timeout=300`
- `:1031-1037` / `:1075-1081` **非零退出码 → 返回 `json.dumps({"error": stderr_or_stdout, "output": ...})`，不抛异常**（沙箱路径下这个 dict 会被 `run_skill_script_tool.py:271-279` 升级为异常）

**沙箱执行**（`sdk/nexent/core/agents/sandbox.py`，类 `SandboxSkillScriptRunner`）：
- 绑定时机：`sdk/nexent/core/agents/nexent_agent.py:950-974` —— 仅当 `sandbox_config.level != SandboxLevel.LOCAL` 时创建 runner 并 `bind_execution_backend(script_runner, on_complete=...)`；`:1855-1857` 在收尾时清理
- 超时：`:953-959` 取 `sandbox_config.timeout_seconds`（正数）否则 300
- **技能目录 staging**（`:213-286`）：
  - `:222-226` 先走 `manager.resolve_skill_script` 复用同一信任边界
  - `:229-231` 计算 `script_fingerprint = (st_mtime_ns, st_size)`
  - `:233-243` **指纹变了就 `rm -rf` 已 staging 的目录** → 这是"改了脚本立刻生效"的缓存失效机制
  - `:265-268` 沙箱内路径 = `<skills_root>/<sanitized_name>-<sha256(realpath)[:16]>`，名字做了 `[^A-Za-z0-9_.-]+ → _` 消毒
  - `:271-275` 打 tar 用 `put_archive` 拷进容器
  - `:276-283` **`chmod -R a+rX` 再 `chmod -R a-w`** → 技能目录在沙箱里**只读**，脚本无法自我改写
- **执行命令**（`:288-371`）：`["timeout", "--signal=KILL", str(timeout), *interpreter_args, *shlex.split(params)]`
  - `:326-329` `source="skill"`：解释器 = `python`（`.py`）或 `bash`（`.sh`）
  - `:318-325` **`.sh` 的 CRLF 处理**：只在 staging 副本上 `sed -i 's/\r$//'`，**保留上传原件不动**（注释 `:319-321`）
  - `:330-361` `source="workspace"`：`.py` 先过 `_validate_workspace_python`（`:195-203` 扫禁用的 shell 调用，`allow_package_installs=network_enabled`）再跑；**`.js` 用 Node 的 `--experimental-permission` + `--allow-fs-read/write` 白名单**（`:352-361`）
  - `:372-374` CWD 设为 `<workspace>/outputs`

### 3.4 三层机制的差异 → Skill 到底能做什么 / 不能做什么

| 能做 | 机制依据 |
|---|---|
| 注入**流程知识 / 领域规则 / 判断标准**（纯文本，零副作用） | L1 + L2（正文进上下文） |
| 打包**确定性脚本**，让 LLM 不必每次重写代码 | L3（`.py`/`.sh`） |
| 产出**用户可下载的文件附件** | L3 + `script_outputs`（§1.4） |
| 提供**可按需检索的长文档**，不撑爆上下文 | `references/` + L2 的 `additional_files` |
| 让**同一技能在不同 agent 上用不同参数** | `ag_skill_instance_t.config_values` 覆盖（`service.py:825-826`） |
| **要求**某些工具必须一起启用 | `allowed-tools` → `ag_skill_tools_rel_t`（`service.py:450-457`）；`create_agent_info.py:1944-2018` 会校验"已启用技能所需的工具是否缺失" |
| 在沙箱里获得**系统能力**（读文件/算/调库），而普通 agent 代码被禁止用 Shell | `agent_worker.yaml:16` 禁令 + `run_skill_script_tool.py:29-30` 指引 |
| **不能**：凭空注册一个新的 LLM 可调用函数（没有动态 schema 生成） | 工具集在 `create_agent_info.py` 静态构造 |
| **不能**：拦截/改写其它工具调用、hook 循环 | 无相应扩展点 |
| **不能**：常驻状态跨轮次（除落盘的 `outputs/`） | `SkillManager` 只读盘；沙箱每轮 staging |
| **不能**：自我修改技能文件（沙箱内 `a-w`） | `sandbox.py:280-283` |
| **不能**：用非 `.py`/`.sh` 脚本 | `skill_manager.py:962-963` |

> **一句话**：**Skill = "按需加载的提示 + 可执行脚本 + 按需文档 + 参数模板" 的打包**。它扩展的是**知识密度与确定性执行能力**，不是"新的工具接口"。
> 如果需求是"给 agent 一个新的可调用原子接口"→ 写 **MCP 工具**（§5）；如果是"给 agent 一套做完某件事的流程与配套脚本"→ 写 **Skill**。

---

## 4. 手把手：写一个最小 Skill 并验证它被调用

下面这个例子同时演示 L1/L2/L3 + `references/` + `config/`，并且**可复用为赛题第一个技能模板**（见 §6 模板 T1）。

### 4.1 完整文件内容

**放置路径**（租户隔离，`tenant_id` 用实际值替换）：
```
<SKILLS_PATH>/<tenant_id>/decision-case-scorer/
├── SKILL.md
├── config/
│   ├── config.yaml
│   └── schema.yaml
├── references/
│   └── rubric.md
└── scripts/
    └── score_case.py
```
（生产：`SKILLS_PATH=/mnt/nexent-data/skills` → `/mnt/nexent-data/skills/<tenant_id>/decision-case-scorer/`；宿主对应 `${ROOT_DIR}/skills/<tenant_id>/decision-case-scorer/`）

---

#### 文件 1：`SKILL.md`

```markdown
---
name: decision-case-scorer
description: Score exactly one decision case with a fixed deterministic rubric and emit a machine-readable JSON score file. Use when a decision case must be judged without an LLM, when a score must be byte-for-byte reproducible across runs, or when an evaluation harness needs a per-case score artifact. Do not use for open-ended judgement, style review, or anything requiring a subjective rubric.
tags:
  - evaluation
  - decision
allowed-tools:
  - read_file
script_outputs:
  scripts/score_case.py:
    kind: file
    mime_types:
      - application/json
---

# Decision Case Scorer

Score **one** case file per invocation. The score is produced by fixed rules, never by a model.

## Before scoring

Read the rubric once per run so the rule ids you report match the current definitions:

<reference path="references/rubric.md" />

## How to score

1. Locate the case JSON in the run inputs (default `inputs/case.json` under the run workspace).
2. Run the bundled scorer. Pass the case path and the output path explicitly:

<use_script path="scripts/score_case.py" />

<code>
result = run_skill_script(
    skill_name="decision-case-scorer",
    script_path="scripts/score_case.py",
    params="--case inputs/case.json --out outputs/score.json",
    source="skill",
)
print(result)
</code>

3. Return the script's `artifacts` result to the user unchanged so the JSON file reaches the attachment area.

## Rules

- Never score more than one case per call. Loop in separate calls.
- If the script raises or returns a non-`success` status, repair the input and rerun.
  Do not compute a score by hand and do not fall back to a model judgement.
- The optional thresholds come from skill configuration; read them with
  `read_skill_config(skill_name="decision-case-scorer")` only when a case needs
  a custom pass mark. Defaults live in `config/config.yaml`.
```

> **为什么 description 这么长**：它是唯一的触发依据（`skill-creator/SKILL.md:309-312`），且每轮都占上下文。这里显式写了"做什么 + 何时用 + 何时**不**用"三段。生产环境请按实际业务裁剪，**不要**堆关键词。
> 注意末尾**没有** "When to Use This Skill" 小节在正文里 —— 那部分信息必须在 `description` 里。

#### 文件 2：`config/config.yaml`（参数默认值，磁盘权威）

```yaml
# Case scorer defaults. Overridable per agent via the Skill instance config.
pass_threshold: 0.7
weight_correctness: 0.5
weight_evidence: 0.3
weight_cost: 0.2
emit_rule_trace: true
```

#### 文件 3：`config/schema.yaml`（参数元数据，驱动前端表单）

字段形态参考 `doc/docs/zh/integration/integration-in/skills.md:107-123`：

```yaml
pass_threshold:
  type: number
  required: false
  description: "Score at or above this value is reported as pass."
  description_zh: "达到或超过该分数判定为通过"
  default: 0.7

weight_correctness:
  type: number
  required: false
  description: "Weight of the correctness component."
  description_zh: "正确性分量权重"
  default: 0.5

weight_evidence:
  type: number
  required: false
  description: "Weight of the evidence component."
  description_zh: "证据分量权重"
  default: 0.3

weight_cost:
  type: number
  required: false
  description: "Weight of the cost component."
  description_zh: "成本分量权重"
  default: 0.2

emit_rule_trace:
  type: boolean
  required: false
  description: "Include per-rule hits in the output file."
  description_zh: "在输出文件中附带逐条规则命中情况"
  default: true
```

支持类型：`string` / `number` / `boolean` / `array` / `object`（`doc/docs/zh/integration/integration-in/skills.md:123`）。

#### 文件 4：`references/rubric.md`（按需加载，不进默认上下文）

```markdown
# Scoring rubric

## Rule ids

| id | component | meaning |
| --- | --- | --- |
| R1 | correctness | The chosen action matches the reference action for this case. |
| R2 | evidence | Every claim in the decision cites a source present in the case input. |
| R3 | cost | The action stays within the declared budget for this case. |

## Components

- `correctness` = 1.0 if R1 holds else 0.0
- `evidence`    = fraction of claims with a resolvable citation
- `cost`        = 1.0 if R3 holds else 0.0

## Total

`total = w_correctness*correctness + w_evidence*evidence + w_cost*cost`

Default weights come from `config/config.yaml`. `pass` is `total >= pass_threshold`.

## Determinism requirements

- No randomness, no clock, no network, no model calls.
- The same case input must always produce byte-identical output except for nothing.
```

#### 文件 5：`scripts/score_case.py`（唯一可执行交付逻辑）

```python
#!/usr/bin/env python3
"""Deterministic scorer for one decision case.

Emits the Nexent file-artifact contract on stdout:
    {"status": "success", "artifacts": [{...}]}
See doc/docs/zh/user-guide/resource-repository/custom-file-generation-skill.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

MIME_TYPE = "application/json"

DEFAULTS = {
    "pass_threshold": 0.7,
    "weight_correctness": 0.5,
    "weight_evidence": 0.3,
    "weight_cost": 0.2,
    "emit_rule_trace": True,
}


def load_config() -> dict:
    """Read config/config.yaml next to the skill root; fall back to defaults."""
    config_path = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
    if not config_path.is_file():
        return dict(DEFAULTS)
    import yaml  # PyYAML ships with the Nexent runtime

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in raw.items() if k in DEFAULTS})
    return merged


def score(case: dict, cfg: dict) -> dict:
    expected = case.get("reference_action")
    actual = (case.get("decision") or {}).get("action")
    correctness = 1.0 if expected is not None and actual == expected else 0.0

    claims = case.get("claims") or []
    sources = set(case.get("available_sources") or [])
    if claims:
        cited = sum(1 for c in claims if c.get("source") in sources and c.get("source"))
        evidence = cited / len(claims)
    else:
        evidence = 1.0

    budget = case.get("budget")
    used = (case.get("decision") or {}).get("cost")
    if budget is None or used is None:
        cost = 1.0
    else:
        cost = 1.0 if used <= budget else 0.0

    total = (
        cfg["weight_correctness"] * correctness
        + cfg["weight_evidence"] * evidence
        + cfg["weight_cost"] * cost
    )
    return {
        "case_id": case.get("case_id"),
        "total": round(total, 6),
        "pass": total >= cfg["pass_threshold"],
        "components": {
            "correctness": correctness,
            "evidence": round(evidence, 6),
            "cost": cost,
        },
        "rules": {
            "R1_correctness": bool(correctness),
            "R2_evidence": evidence >= 1.0,
            "R3_cost": bool(cost),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic decision case scorer")
    parser.add_argument("--case", required=True, help="Path to the case JSON")
    parser.add_argument("--out", required=True, help="Path to write the score JSON")
    args = parser.parse_args()

    case_path = Path(args.case)
    if not case_path.is_file():
        print(json.dumps({"status": "error", "error": f"case not found: {case_path}"}))
        return 1

    try:
        case = json.loads(case_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": f"invalid case JSON: {exc}"}))
        return 1

    cfg = load_config()
    result = score(case, cfg)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"config": cfg, "result": result}
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # The artifact contract is read from stdout only; nothing else may be printed.
    print(json.dumps({
        "status": "success",
        "artifacts": [{
            "kind": "file",
            "absolute_path": str(out_path.resolve()),
            "file_name": out_path.name,
            "mime_type": MIME_TYPE,
            "file_size_bytes": out_path.stat().st_size,
        }],
        "message": f"scored case {result['case_id']}: total={result['total']} pass={result['pass']}",
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**这个脚本为什么能通过 §1.4 的全部 8 项校验**（逐条对照）：
| 校验 | 本脚本对应 |
|---|---|
| `script_outputs` 声明了该路径且 `kind: file` | `SKILL.md` 里 `scripts/score_case.py: kind: file` |
| stdout 可 `json.loads` 成 dict | `print(json.dumps({...}))` |
| `status == "success"` | 显式写死 |
| `artifacts` 是 list | 显式 |
| 每条 `kind == "file"` | 显式 |
| `absolute_path`/`file_name`/`mime_type` 非空 str | `str(out_path.resolve())`、`out_path.name`、常量 |
| `file_size_bytes` 非负 int（非 bool） | `out_path.stat().st_size` 是真 int |
| 文件存在且大小精确相等 | **先写盘、再 `stat()`**（顺序不能反） |
| `mime_type` 在声明的 `mime_types` 里 | `MIME_TYPE = "application/json"` ↔ 声明 `application/json` |

> ⚠️ 三条硬性纪律：
> 1. **`print` 只能输出那一行 JSON**（调试信息写 stderr 或注释掉），否则 stdout 不是合法 JSON → artifact 全丢
> 2. **先落盘再 stat**，顺序反了会 `file_size_bytes` 对不上
> 3. 路径要落在**允许上传的工作目录**（通常是 run workspace 的 `outputs/`），否则后端拒绝上传（文档 `:199`）

### 4.2 验证步骤

#### 验证 A：文件契约静态自检（**本机可跑，我已实跑通过**）

```powershell
# 1) 确认 frontmatter 能被 Nexent 的解析链吃下
python - <<'PY'
import re, yaml, pathlib
p = pathlib.Path(r"<技能目录>/SKILL.md")
raw = p.read_bytes().decode("utf-8-sig")            # 等价 decode_skill_text 的 BOM 分支
PAT = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)   # = skill_loader.py:26
m = PAT.match(raw); assert m, "frontmatter 正则不匹配"
fm = yaml.safe_load(m.group(1))
ALLOWED = {"name","description","allowed-tools","tags","script_outputs"}  # skill_loader.py:14-20
print("keys      :", sorted(fm))
print("unknown   :", sorted(set(fm) - ALLOWED) or "none（会被静默丢弃）")
assert fm.get("name") and fm.get("description"), "name/description 必填"
so = fm["script_outputs"]
for path, spec in so.items():
    assert spec.get("kind") == "file", path
    assert (p.parent / path).is_file(), f"声明了不存在的脚本: {path}"
    print("script ok :", path, spec["mime_types"])
print("A) frontmatter/script_outputs 契约自检通过")
PY
```

我实际执行的结果（样本 = §4.1 的 frontmatter）：

```
[LF  no-BOM]           regex OK | keys=['allowed-tools','description','name','script_outputs','tags'] | unknown=none | kind=['file']
[CRLF no-BOM]          regex OK | keys=[...同上...] | unknown=none | kind=['file']
[LF  BOM (raw utf-8)]  frontmatter regex: FAIL      ← 必须用 utf-8-sig 解码
[LF  BOM (utf-8-sig)]  regex OK | keys=[...同上...] | unknown=none | kind=['file']
[GBK]                  regex OK | keys=[...同上...] | unknown=none | kind=['file']
```

#### 验证 B：脚本自身可跑（**本机可跑**）

```powershell
$d = "<技能目录>"
'{"case_id":"c-001","reference_action":"restart","decision":{"action":"restart","cost":3},"budget":5,"available_sources":["s1"],"claims":[{"source":"s1"}]}' | Set-Content "$d\case.json" -Encoding utf8
python "$d\scripts\score_case.py" --case "$d\case.json" --out "$d\score.json"
# 期望：stdout 恰好一行 JSON，status=success，artifacts[0].file_size_bytes 与 score.json 实际大小一致
python -c "import json,os;p=json.load(open(r'$d\score.json',encoding='utf-8'));print('total',p['result']['total'],'pass',p['result']['pass'])"
```

#### 验证 C：让 Nexent 认下这个技能（需要**已部署的 Nexent**）`【未验证-本机无 Nexent 实例】`

**C1（推荐，走官方链路）—— 打成 ZIP 走上传 API**

ZIP 结构必须让 `SKILL.md` 出现在**根**或**第一层子目录**下（`service.py:391-407`；`doc/docs/zh/integration/integration-in/skills.md:82` 说"可以在根目录或子目录中"，但 `:191` 的 FAQ 又说"确保 ZIP 包根目录下包含 SKILL.md"——**两处说法不一致，以根目录打包最稳**）：

```powershell
cd <技能目录>\..
Compress-Archive -Path decision-case-scorer -DestinationPath decision-case-scorer.zip
curl.exe -X POST "http://<nexent-host>:<port>/api/skills/upload" `
  -H "Authorization: Bearer <token>" `
  -F "file=@decision-case-scorer.zip" `
  -F "source=custom"
# 期望 201 + JSON（含 name / description / config_schemas / config_values）
```
路由出处：`backend/apps/skill_app.py:47` `APIRouter(prefix="/skills")` + `:226` `@router.post("/upload")`。上传大小上限 `MAX_SKILL_UPLOAD_SIZE_MB=10`（`const.py:260-261`），租户技能数上限 `MAX_SKILLS_PER_TENANT=1000`（`const.py:257-258`，超限在 `skill_db.py:41-63` 抛错）。

**C2 —— 把技能启用给某个 agent**

```powershell
# 先拿到 skill_id
curl.exe "http://<host>/api/skills?..." -H "Authorization: Bearer <token>"
# 再建 skill 实例（绑定到 agent）
curl.exe -X POST "http://<host>/api/skills/instance/update" `
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" `
  -d '{"skill_id": <id>, "agent_id": <aid>, "enabled": true, "version_no": 0, "config_values": {"pass_threshold": 0.8}}'
```
请求体字段：`SkillInstanceInfoRequest`（`backend/consts/model.py:1384-1394`）= `skill_id` / `agent_id` / `enabled` / `version_no` / `config_values`，**注意不收 `skill_description` / `skill_content`**（`:1386-1388` 注释）。路由 `skill_app.py:480`。`version_no != 0` 会被 `AgentDraftEditError("agent_not_draft")` 拒绝（`:493-494`）→ **只能往草稿版本上挂技能**。
也可在前端做：智能体开发 → 工具配置 → **Skills** 页签 → 选择 Skill → 配置参数（`doc/docs/zh/integration/integration-in/skills.md:170-176`；组件 `frontend/app/[locale]/agents/[agentId]/components/capability/SelectedSkillManagement.tsx`、`.../skill/SelectSkillsDialog.tsx`、参数弹窗 `frontend/app/[locale]/agents/components/capability/skill/SkillConfigModal.tsx:178-221`）。

**C3 —— 断言"它真的被调用了"（证据链，逐级加强）**

| # | 断言点 | 观察方式 | 判定 |
|---|---|---|---|
| 1 | 技能清单进了 system prompt | `backend/agents/create_agent_info.py:1868-1872` 的 debug 日志 | `skills_count=N` 且 items 里出现 `skill:decision-case-scorer(type=skill,priority=40)` |
| 2 | L1 清单可见 | 直接看发给模型的 system 消息 | 含 `<available_skills>` 与 `<name>decision-case-scorer</name>` + 你的 description |
| 3 | L2 被加载 | 会话里出现 `read_skill_md` 调用 | 参数 `skill_name="decision-case-scorer"`，`additional_files=[]`；返回值**不含 frontmatter**（`read_skill_md_tool.py:133-134`） |
| 4 | L2 按需读取生效 | 出现第二次 `read_skill_md` | `additional_files=["references/rubric.md"]`，且返回里**没有** SKILL.md 正文（`:182-191` 的语义） |
| 5 | 授权边界正常 | 故意传未启用技能名 | `read_skill_md` 返回字符串 `Skill access is not authorized`（`:154-155`）；`run_skill_script` 抛 `PermissionError`（`run_skill_script_tool.py:253-256`） |
| 6 | L3 被调用 | 出现 `run_skill_script` 调用 | `script_path="scripts/score_case.py"`、`source="skill"`（`agent_worker.yaml:21` 要求） |
| 7 | 脚本真的执行了 | `run_skill_script` 的返回值 | 是脚本 stdout 的原始字符串（`run_skill_script_tool.py:302`），含你的 `message` 字段 |
| 8 | **附件管道通了** | 会话附件区 / `skill_files` 流事件 | 出现 `score.json` 下载项。后端识别点 `backend/management/services/agent/run.py:535` |
| 9 | 配置覆盖生效 | 传 `config_values.pass_threshold=0.8` 后重跑 | `read_skill_config` 返回 `pass_threshold: 0.8`（`read_skill_config_tool.py:61-65` 走 overrides 分支） |

**C4 —— 离线单元级验证（不需要起服务，但需要装 SDK）** `【未验证-本机未安装 nexent】`
```bash
pip install -e "<repo>/sdk"        # 或 uv pip install -e "../sdk[dev]"
python - <<'PY'
from nexent.skills import SkillManager, SkillLoader
m = SkillManager(base_skills_dir=r"<SKILLS_PATH>")      # 单例，进程内只初始化一次
print(m.list_skills(tenant_id="<tenant_id>"))           # 期望含 decision-case-scorer
s = m.load_skill("decision-case-scorer", tenant_id="<tenant_id>")
print(s["name"], "|", s["tags"], "|", len(s["content"]), "|", s["script_outputs"])
print(m.get_skill_scripts("decision-case-scorer", tenant_id="<tenant_id>"))
print(m.resolve_skill_script("decision-case-scorer", "scripts/score_case.py", tenant_id="<tenant_id>"))
print(m.build_skills_summary(available_skills=["decision-case-scorer"], tenant_id="<tenant_id>"))
PY
```
> 注意 `SkillManager.__new__` 是**进程级单例**（`skill_manager.py:44-65`），`base_skills_dir` 只有第一次构造生效。同一进程里换目录测试必须新开进程。

### 4.3 最短可行版本（只想验证"被调用"时的最小集）

只保留两个文件即可：

```
<SKILLS_PATH>/<tenant_id>/hello-skill/SKILL.md
```
```markdown
---
name: hello-skill
description: Emit a fixed greeting line so skill invocation can be verified end to end. Use when you must prove that a skill was discovered, loaded, and executed.
---

# Hello Skill

Reply with the single line `hello-skill was loaded` and call no other tool.
```
验证：对挂了该技能的 agent 说"验证技能调用" → 出现 `read_skill_md(skill_name="hello-skill")`，且回复含 `hello-skill was loaded`。
**这个最小集只覆盖 L1+L2，不覆盖 L3/artifact** —— 用来快速排掉"技能没被发现"这类问题最高效。

---

## 5. Skill 与 MCP 工具的分工边界

### 5.1 机制差异对照（决定何时用哪个）

| 维度 | **MCP / 本地工具（Tool）** | **Skill（技能）** | 出处 |
|---|---|---|---|
| 上下文成本 | **常驻**：`### 工具` 段列出每个工具的 name + description + inputs + output_type，**每轮都付** | **常驻的只有 name + description**，正文/文档/脚本按需 | `formatting.py:63-97` vs `:44-61` |
| 是否给 LLM 新函数 | **是**：注册成可调用函数，有 schema | **否**：不生成函数签名 | `create_agent_info.py:1078-1132` 固定三个内置工具 |
| 粒度 | 单个原子操作 | 多步工作流 + 文档 + 参数 + 脚本 | `doc/docs/zh/backend/skills/index.md:14-15` |
| 分发方式 | 代码级 / MCP server 接入 | **ZIP 包即插即用** | `doc/docs/zh/integration/integration-in/skills.md:180-185` |
| 参数 | 固定 schema | 可自定义参数模板（`config/schema.yaml`），**且每个 agent 可覆盖** | `service.py:140-179`、`:825-826` |
| 执行环境 | 工具自己决定 | 沙箱（`.py`/`.sh`），目录只读 | `sandbox.py:276-283` |
| 能否产出附件 | 需自己实现上传 | **`script_outputs` 声明即可**接入附件管道 | `run_skill_script_tool.py:146-221` |
| 依赖工具 | 独立 | **可声明依赖**（`allowed-tools` → `tool_ids`），缺工具会报错 | `service.py:450-457`、`create_agent_info.py:2010-2018` |
| 权限模型 | 工具级 `is_user_selectable` 等 | 租户隔离 + 每 agent `enabled` + `authorized_skill_names` 硬闸门 | `skill_manager.py:67-94`、`run_skill_script_tool.py:253-256` |

### 5.2 决策规则（可直接照用）

**写 MCP / 本地工具，当：**
1. 这是**一个原子能力**，需要 LLM 在任意上下文里直接调用（"查天气"、"发邮件"、"写数据库"）
2. 需要对**参数做严格 schema 校验**（工具 `inputs` 会进 system prompt，模型能照着填）
3. 需要成为**别的 Skill 的依赖**（Skill 通过 `allowed-tools` 引用工具名）
4. 能力在**平台之外**（外部 HTTP 服务、第三方 SaaS）→ MCP server（工具描述里会打 ` [MCP]` 标记，`formatting.py:87-90`；并附 MCP 专属调用指引 `:89-90`）
5. 需要**复用现有 MCP 生态**（`create_agent_info.py:543-552` `create_mcp_tool`）

**写 Skill，当：**
1. 需要传授**流程知识 / 判断标准 / 领域规则**（"这类工单按这 5 步判"）——这是纯提示注入，工具做不到
2. 需要**确定性脚本**且不希望 LLM 每次重写（PDF 旋转、Excel 生成、报表导出）
3. 需要**交付文件**给用户下载/预览 → `script_outputs`（工具要自己实现整条上传链）
4. 需要**渐进披露长文档**（API 手册、schema 说明、政策条文）而不撑爆上下文
5. 需要**同一能力在不同 agent 上用不同参数**（`config_values` per-agent 覆盖）
6. 需要**在沙箱里获得系统能力**，而普通 agent 代码被明令禁止用 Shell（`agent_worker.yaml:16`）

**两者都要，当：** 技能的流程需要调用一个外部原子能力 → 写 MCP 工具 + 在 Skill 的 `allowed-tools` 里引用它。

### 5.3 反模式（源码/文档里有依据的坑）

| 反模式 | 后果 | 依据 |
|---|---|---|
| 把技能当"工具垃圾桶"：一个原子操作也包成 Skill | 多一次 `read_skill_md` 往返，且清单常驻 description 白付 token | `doc/docs/zh/backend/skills/index.md:14-15` |
| description 写成关键词堆砌 | 触发不准 + 每轮都贵 | `skill-creator/SKILL.md:309-312` |
| 把"何时使用"写在正文而不是 description | **永不生效**（正文只在触发后加载） | `skill-creator/SKILL.md:311` |
| 正文超 500 行 | 上下文膨胀；应拆进 `references/` | `skill-creator/SKILL.md:124,197-200` |
| 在技能里放 README/CHANGELOG | 增加噪音与误读 | `skill-creator/SKILL.md:102-112` |
| 用 Skill 承载"需要严格参数 schema 的原子调用" | Skill 不生成函数签名，模型只能靠正文自律 | §3.4 |
| 脚本里 `print` 调试信息 | stdout 非法 JSON → **artifact 全丢** | `run_skill_script_tool.py:161-167` |
| 用技能做跨轮次状态机 | 无状态载体（沙箱目录每轮 staging、只读） | `sandbox.py:253-286` |

---

## 6. 针对赛题应沉淀的 Skill 模板清单

> **赛题定位假设**：华为 ICT 大赛创新赛赛题 3「基于 ModelEngine Nexent 打造**可进化决策智能体**」（依据本工作区 `07-项目蓝图候选.md:81-107`：进化槽位 = agent 的 prompt/工具集/记忆策略/工作流配置，即 **harness 层，不动权重**；验证信号 = 决策案例回放的**可判定结果**；选择与存档 = Nexent 版本树 + 外部岛屿存档；安全约束 = 版本回滚 + 只读评测 + 预算上限 + 人类晋级闸门）。
> 若赛题实际不同，下列模板的"业务逻辑"列可替换，但「为什么它可复用」列（即**为什么要做成 Skill 而不是工具/一次性脚本**）依据的是 §5 的机制差异，仍然成立。

**共同的落地形态**：每个模板 = `SKILL.md` + `scripts/*.py`（确定性、可跑）+ `config/schema.yaml`（可调参数）+ `references/*.md`（长文档按需读）。**所有评分/判定类脚本必须是纯函数式的，禁止 LLM 参与**（这是赛题"可判定验证器"的硬要求，也是 Nexent `script_outputs` 契约天然鼓励的形态）。

| # | 技能名 | 解决什么业务逻辑 | 输入 → 输出 | 为什么它可复用 |
|---|---|---|---|---|
| **T1** | `decision-case-scorer` | **可判定评分器**：对单个决策案例按固定规则打分，产出可复现的分数文件。是"验证信号决定一切"这条主线的地基 | 入：`inputs/case.json`（含 reference_action / decision / budget / claims / available_sources）；出：`outputs/score.json`（total / pass / 分量 / 逐规则命中）| **每个赛题都需要**，且评分规则只在 `references/rubric.md` + `config/config.yaml` 里改，**不用改代码**；per-agent `config_values` 可让不同生产线用不同阈值。见 §4 完整实现 |
| **T2** | `decision-replay-harness` | **批量回放台**：对一整个 case 目录跑 T1，汇总成分数表 + held-out 切分，产出可绘曲线的结果文件 | 入：`inputs/cases/`（目录）+ `config`（holdout 比例、seed）；出：`outputs/replay.json` + `outputs/replay.csv`（两文件 dual artifact）| 一次编写，**每次实验都跑**。避免每轮让 LLM 重写评测循环（这是"评测器不稳定"的头号来源）。声明两个 `script_outputs` 即两个附件 |
| **T3** | `failure-attribution` | **失败归因分流**：把失败案例按"过程类 / 内容类"分类（过程类→改 harness；内容类→考虑训权重），产出分流表 | 入：`inputs/replay.json`（T2 产物）+ `references/attribution.md`（判据）；出：`outputs/attribution.json` | 蓝图里明确这是"当前无人占位的中间层"（`07-项目蓝图候选.md:104`）。判据是**规则不是模型**，所以必须做成确定性脚本；判据会持续演进 → `references/` 单独维护 |
| **T4** | `harness-mutation` | **变异算子**：依据失败轨迹生成 agent 配置的候选改动（prompt / 工具集 / 记忆策略 / 工作流配置），输出**结构化 patch** 而非直接改 | 入：`inputs/attribution.json` + `inputs/current_config.json`；出：`outputs/mutations.jsonl`（N 个候选，每个含 rationale + diff + 预估风险）| 变异模板要**反复迭代**（这是进化的核心算子）；把模板文本放 `references/mutation-patterns.md`，改模板不动代码。**必须产出结构化 patch 而非直接落库**，才能接"人类晋级闸门" |
| **T5** | `verifier-health-check` | **验证器体检台**：对抗探针 + 代理-真实落差检测 + 塌缩检测，输出"我的验证器是否在被自欺"的体检报告 | 入：`inputs/replay.json` + `outputs/` 历史分数序列；出：`outputs/health-report.json` + `outputs/health-report.md` | 蓝图三大亮点之一（`07-项目蓝图候选.md:105`）。体检项会不断增加 → `references/probes.md` 逐条沉淀；**这类"元评测"最适合 Skill 化**，因为它既是知识又是脚本 |
| **T6** | `island-archive` | **存档与谱系**：把每个候选版本（配置 + 分数 + 谱系父节点）落成不可变快照，支持岛屿拓扑与重放 | 入：`inputs/candidate.json` + `inputs/scores.json`；出：`outputs/archive/<candidate_id>.json`（含 parent_id / island_id / 时间戳 / 配置哈希）| 进化的"选择与存档"槽位。**同一套存档格式要被 T4/T5/T7 反复读写**，做成技能后格式定义只在一处（`references/archive-format.md`）。配置哈希用脚本算，杜绝手写 |
| **T7** | `promotion-gate` | **晋级闸门 + 预算上限**：校验候选是否满足"分数提升 ≥ 阈值、预算未超、无回归、体检通过"才允许晋级 | 入：`inputs/candidate.json` + `inputs/current.json` + `inputs/health-report.json`；出：`outputs/gate-decision.json`（allow/deny + 逐条理由） | 安全约束的**唯一执行点**（`07-项目蓝图候选.md:100`"版本回滚 + 只读评测 + 预算上限 + 人类晋级闸门"）。闸门条件要按评委/业务反馈调整 → `config/schema.yaml` 暴露阈值，**改参数不改代码** |
| **T8** | `decision-report-writer` | **交付物生成**：把 T2/T3/T5 的 JSON 渲染成方案书需要的表格与图表数据（Markdown/CSV/XLSX） | 入：上述 JSON；出：`outputs/report.md` + `outputs/report.xlsx` | 赛题 D13 要交材料（`07-项目蓝图候选.md:119`）。**这是纯确定性渲染**（不靠 LLM 编数字），正好是 Skill 的最佳用途；模板放 `assets/` |

**沉淀优先级建议**：T1 → T2 → T6 → T5 → T3 → T4 → T7 → T8。
理由：`07-项目蓝图候选.md:107` 强调"**先写验证器，再写变异器**"、并有一条硬门槛"**单次评估 < 10 秒，且同输入同输出**"——T1/T2/T6 正是这条门槛的载体，且它们**不依赖 LLM**，可以完全离线验证（§4.2 验证 A/B），在赛题早期就能锁定确定性。

**每个模板都该遵守的共同约定**（复述自 §1、§4）：
1. frontmatter 只用 `name` / `description` / `tags` / `allowed-tools` / `script_outputs` 五个键
2. `description` 写全"做什么 + 何时用 + 何时不用"（唯一触发依据，且在 L1 每轮付费）
3. 正文 < 500 行；长判据/格式定义进 `references/`，从 SKILL.md **一层直达**引用
4. 所有脚本 `.py`；stdout 只输出 artifact JSON；先落盘再 `stat()`
5. 可调阈值全部走 `config/config.yaml` + `config/schema.yaml`，**不硬编码**
6. 评分/判定脚本禁止随机、时钟、网络、模型调用（否则"同输入同输出"不成立，T2/T5 全部失效）

---

## 7. 版本与更新机制

### 7.1 改了什么，怎么生效（按变更类型分别说明）

| 改什么 | 生效时机 | 机制依据 |
|---|---|---|
| `SKILL.md` **正文** | **下一次 run 立即生效** | `read_skill_md` 每次调用都从盘读（`read_skill_md_tool.py:162-170` 先 `load_skill` 拿目录再 `_read_skill_file`）；`SkillLoader.load` 无缓存（`skill_loader.py:28-36`） |
| `description` | **下一次 run 立即生效（清单）**，但**取决于 agent 组装走 DB 还是磁盘** | 走 DB 时 description 来自 `ag_skill_info_t`（`service.py:823,831`）；**走 `runtime_skill_snapshot` 时来自快照**（`create_agent_info.py:1064-1066`） |
| `scripts/*.py` | **下一次 run 立即生效** | 进程内：每次 `subprocess.run` 直接执行磁盘文件（`skill_manager.py:1023-1030`）。沙箱：staging 指纹 `(st_mtime_ns, st_size)` 变了就 `rm -rf` 重建（`sandbox.py:229-243`） |
| `references/*` | 下一次 `read_skill_md` 立即生效 | 同上，按需读盘 |
| `config/config.yaml` | **下一次读立即生效**，且**优先于 DB** | `_enrich_configs_from_yaml`（`service.py:157-166`）：磁盘存在则覆盖 `config_values`；不存在则 `pop` 掉 DB 值 |
| `config/schema.yaml` | 同上，**优先于 DB `config_schemas`** | `service.py:167-178`（注释明说 "schema.yaml takes precedence over DB config_schemas"） |
| **新增技能目录并挂到 agent** | 下一次 run | 技能列表每次 run 从 DB 重查（`create_agent_info.py:1748`）→ **无需重启服务** |
| **新增技能目录但没建 skill 记录/实例** | **永不生效** | `list_skills` 只认磁盘，但注入靠 DB（`service.py:797-843`）→ **必须建 `ag_skill_info_t` + `ag_skill_instance_t` 两条记录** |

> **关键结论**：**没有编译期、没有缓存层、没有构建步骤。** 技能是"运行时读盘 + 运行时查库"的组合，改完即生效。唯一需要"重新保存"的动作是 **agent 配置里的技能挂载关系**（因为那在 DB）。

### 7.2 版本管理：有，但**不是技能级版本**

**技能表没有 version 字段**。`ag_skill_info_t`（`deploy/sql/migrations/v2.0_merged_migrations.sql:11-24` + `db_models.py:1887-1925`）的列：
`skill_id, skill_name, skill_description, skill_tags, skill_content, config_values(原 params), config_schemas, source, tenant_id, created_by, create_time, updated_by, update_time, delete_flag`
→ 只有 `create_time` / `update_time` 审计字段，**无 `version` / `release_notes` / `changelog`**。

**版本语义只存在于两处**：

**(1) Agent 版本树上的技能实例快照**（这才是赛题要用的"可回滚"底座）
- `ag_skill_instance_t.version_no`：`0` = 草稿/编辑态，`>=1` = 已发布快照（注释 `v2.0_merged_migrations.sql:102`；`db_models.py:1967`）
- 主键 `(skill_instance_id, version_no)`（`:87`）
- 发布新 agent 版本时**把当前草稿的技能实例整体复制一份**：`backend/database/agent_version_db.py:460-540` `copy_*`（`:494` 硬删草稿技能、`:532-537` 从目标版本批量 insert 技能并把 `version_no` 置 0）
- 查询/删除某版本的技能快照：`agent_version_db.py:615-640`（`query_skill_instances_snapshot`、`insert_skill_snapshot`）、`:540-547` `delete_skill_snapshot`
- 前端版本管理 API：`backend/apps/agent_app.py:1116`（注释 "This copies the target version's snapshot (agent, tools, relations, skills)"）
- 官方文档：`doc/docs/zh/backend/skills/overview.md:118-137`（"草稿版本 version=0 修改即时生效；已发布版本 version>=1 参数锁定"）

> ⚠️ **重要边界**：`version_no` 冻结的是**技能实例（绑定关系 + per-agent 参数）**，**不是技能内容**。`ag_skill_info_t` 是全局共享行，改技能正文/描述**会同时影响所有引用它的 agent 版本**。想要"技能内容也版本化"，只能靠**改技能名 + 新建目录/记录**（因为 `skill_name` 有唯一索引 `uq_skill_info_tenant_name_active`，`db_models.py:1892-1894`）或走下面的 marketplace 快照。
> 这对赛题很关键：**"可回滚的自修改"如果涉及技能内容，必须自己做内容快照（建议用 T6 `island-archive` 落盘）**，不能指望 `version_no`。

**(2) 技能市场（Skill Repository）的冻结快照**
- 表 `ag_skill_repository_t`：`deploy/sql/migrations/v2.3_merged_migrations.sql:273-295` / `db_models.py:1760-1779`
- 关键列：`skill_info_json JSONB NOT NULL`（`:1778` 冻结的技能元数据快照）、`skill_zip_base64 TEXT NOT NULL`（`:1779` 冻结的 ZIP 载荷）、`status VARCHAR(30) DEFAULT 'not_shared'`（`:288` 审核状态）、`downloads`（`:285`）
- 注释（`:308`）："Skill marketplace repository for **frozen installable skill snapshots**"
- **语义**：一次上架 = 一份**不可变**的 (元数据 + 完整 ZIP) 快照。安装方拿到的是快照内容，与源技能后续修改**解耦**
- **无 semver 字段**：没有 `version` / `major.minor.patch`，只有 `skill_repository_id` 自增 + `create_time` / `update_time`
- 路由：`backend/apps/skill_repository_app.py:28` `APIRouter(prefix="/repository/skill")`；`:291` 创建上架、`:242` 改状态（审核）、`:329` 安装
- 前端：`frontend/app/[locale]/skill-space/{page.tsx,my-skill.tsx,review-center.tsx,skill-space.tsx}`、`frontend/components/skill/{skill-detail.tsx,InstallOfficialSkillsModal.tsx}`、`frontend/hooks/skillRepository/useSkillRepositoryListings.ts`
- 官方文档：`doc/docs/zh/user-guide/resource-repository/skill-repository.md`、`official-skills.md`

### 7.3 更新技能的两条路径（以及它们的副作用差异）

**路径 1：`PUT` 文件更新 —— `@router.put(...)` at `backend/apps/skill_app.py:373` `update_skill_from_file`**
→ `service.py:644-665` `update_skill_from_file`（有权限校验，`rewrite_name` 参数）→ `service.py:470-491` `_save_skill_upload(update=True)`
- **副作用（重要）**：`service.py:472` **`self._delete_local_skill_files(name, tenant_id=...)` —— 先清空本地技能目录**，再 `save_skill` 重写 SKILL.md、`_upload_zip_files` 解压新文件
- `_delete_local_skill_files`（`:494-`起）注释明确："Delete all files within a skill's local directory, **preserving the directory itself**"
- 所以**用 ZIP 更新时，ZIP 里没有的旧文件会被删除**（对比 `_update_skill_from_zip` 的 docstring `service.py:510-512` 说的是 "Does not delete existing files not in the archive" —— **两处描述与实际行为不一致**，因为真正执行删除的是外层 `_save_skill_upload(update=True)`）

**路径 2：`PUT /skills/{skill_name}` JSON 更新** → `skill_app.py:719-780` `update_skill` → `service.py:667-673` `update_skill`
- 走 `SkillLoader.to_skill_md` 重新序列化 → **frontmatter 注释与非白名单字段丢失**（§1.2）

**路径 3（无版本语义）：直接改磁盘文件** —— 见 §7.1，立即可生效，**但 DB 里的 `description` / `content` 不会同步**（除非走 API），可能出现"清单里是旧描述、正文是新的"的漂移。

### 7.4 缓存与单例（排障必读）

| 组件 | 是否有缓存 | 后果 |
|---|---|---|
| `SkillLoader.load` / `parse` | **无** | 每次调用重新读盘 |
| `SkillManager` 实例 | **进程级单例**，`base_skills_dir` 首次构造固定（`skill_manager.py:44-65`） | 同进程无法切换技能根；`isolated_skills_root=True` 时用 `object.__new__` 绕过（`run_skill_script_tool.py:114-119`） |
| 三个技能工具的 `self.skill_manager` | 懒加载后**持有**（`run_skill_script_tool.py:111-123` 等） | 一个 agent run 内技能根固定 |
| 沙箱技能目录 staging | **有**，键 = `(realpath, relative_script)`，值含 `(mtime_ns, size)` 指纹（`sandbox.py:229-250,262-284`） | 脚本改动自动失效重建；**内容变了但 mtime+size 都没变**的极端情况不会失效 `【未验证-未实测该边界】` |
| 技能清单（`<available_skills>`） | 每次 run 重新组装 | DB 改动下一 run 生效 |
| 进程级 prompt 缓存 | 无（`load_prompt` 在渲染时调用） | 改 prompt yaml 需重启或看是否热加载 `【未验证】` |

### 7.5 限额与治理

| 项 | 默认值 | 出处 |
|---|---|---|
| 单租户技能数上限 | `MAX_SKILLS_PER_TENANT = 1000`（env `MAX_SKILLS_PER_TENANT`） | `backend/consts/const.py:257-258`；超限抛错 `backend/database/skill_db.py:41-63` |
| 单次上传体积上限 | `MAX_SKILL_UPLOAD_SIZE_MB = 10` → `MAX_SKILL_UPLOAD_SIZE_BYTES` | `const.py:260-263`；校验 `service.py:375-385` |
| Workbench 会话技能挂载数 | `skill_mounts` `max_length=20`，且 `skill_id` 不可重复 | `backend/consts/model.py:910,925-927` |
| 技能名唯一性 | 租户内 active 唯一（`uq_skill_info_tenant_name_active`），全局另有 `uq_skill_info_global_name_active` | `backend/database/db_models.py:1892-1902` |
| 上传重名 | 拒绝（`SkillException("Skill '...' already exists")`） | `service.py:419-420,447-448` |
| 安装重名 | 自动改名，`generate_available_copy_skill_name` | `backend/utils/skill_import_utils.py:13-` |

---

## 8. 未验证清单与已知风险

### 8.1 本次**未能**验证的项（均已在上文标注）

| 项 | 原因 |
|---|---|
| 打包 ZIP 后走 `/skills/upload` 的真实响应 | 本机无 Nexent 实例 |
| `read_skill_md` / `run_skill_script` 的真实端到端调用链与附件落盘 | 同上；本机 `python -c "import nexent"` → `ModuleNotFoundError`（工作区 `.venv-nexent313` 也未安装 nexent / smolagents） |
| 沙箱 `SandboxSkillScriptRunner` 的真实容器行为 | 需要 Docker + `sandbox_config.level != LOCAL` |
| `agent_worker.yaml` 提示词的热加载行为 | 未追 `load_prompt` 的缓存实现 |
| staging 失效的"mtime+size 均未变"极端边界 | 未构造该场景 |
| 前端 Skills 页签的实际交互 | 未启动前端 |

### 8.2 文档/代码**互相矛盾**之处（建议以代码为准）

| 说法 A | 说法 B | 判定 |
|---|---|---|
| `doc/docs/zh/backend/skills/overview.md:120-123`：已发布版本 "参数锁定，防止误改" | `backend/apps/skill_app.py:493-494`：`version_no != 0` 直接拒改（`AgentDraftEditError("agent_not_draft")`） | **一致**（锁定的是实例操作入口） |
| `doc/docs/zh/integration/integration-in/skills.md:82`：ZIP 内 SKILL.md "可以在根目录或子目录中" | 同文档 `:191` FAQ：必须"确保 ZIP 包根目录下包含 SKILL.md"；`service.py:395-403` 实现上**两者都支持**（先找一层深的，再回落 `endswith("skill.md")`） | **实现支持子目录**；FAQ 过严。但打包时放根目录最稳 |
| `service.py:510-512` `_update_skill_from_zip` docstring："Does not delete existing files not in the archive" | `service.py:472` 实际会 `_delete_local_skill_files` 清空目录 | **docstring 与真实行为不一致**，以真实行为为准 |
| `SkillCreateRequest.config_schemas: Optional[Dict[str, Any]]`（`model.py:2283`） | `support.py:843-` `_parse_skill_schema_from_yaml_bytes` 返回 **`List[SkillParam]`** | **类型不一致**（dict vs list）。前端 `skillService.ts:184-190` 也按"数组则收、否则置 null"处理 → 实际用 list 更安全 |
| `skill-creator` 校验器允许 `license` / `metadata` | Nexent 白名单不含这两个 | 见 §1.6 |

### 8.3 赛题落地时最该盯的 5 个风险

1. **技能内容不可回滚**（§7.2）→ 自进化如果改技能，必须自己做内容快照（T6）。
2. **`build_skills_summary` 是死代码**（§2.7）→ 别改错地方。
3. **`script_outputs` 在 YAML 兜底路径下静默失效**（§1.2）→ 生成 SKILL.md 时务必用规范 YAML。
4. **技能清单无裁剪**（`skill_handler.py:16-21` 是 TODO）→ 技能数量直接线性吃掉 system prompt 预算；**模板不要贪多**（建议 ≤ 8 个，与 §6 清单一致）。
5. **`description` 是唯一的发现机制**（§2.4）→ 它是赛题里"技能被正确调用率"的唯一杠杆；T1–T8 的 description 必须写"做什么/何时用/何时不用"，且不能靠正文补救。

---

## 附：关键文件索引（速查）

| 关注点 | 文件 |
|---|---|
| frontmatter 白名单 / 解析 / 序列化 | `sdk/nexent/skills/skill_loader.py:14-20, 59-65, 84-141, 143-203, 213-239` |
| 目录约定常量 | `sdk/nexent/skills/constants.py:3-4` |
| 路径安全 | `sdk/nexent/skills/paths.py:27-70` |
| 编码判定 | `sdk/nexent/skills/text_codec.py:17-49` |
| 上传格式嗅探 | `sdk/nexent/skills/upload.py:6-16` |
| 技能生命周期 / 脚本执行 / 信任边界 | `sdk/nexent/skills/skill_manager.py:118-188, 190-309, 341-447, 579-631, 805-965, 997-1087` |
| `run_skill_script` 工具 + artifact 校验 | `sdk/nexent/core/tools/run_skill_script_tool.py:17-55, 146-221, 222-330` |
| `read_skill_md` 工具 | `sdk/nexent/core/tools/read_skill_md_tool.py:41-51, 95-137, 139-237` |
| `read_skill_config` 工具 | `sdk/nexent/core/tools/read_skill_config_tool.py:17-22, 52-99` |
| 沙箱脚本执行 | `sdk/nexent/core/agents/sandbox.py:213-286, 288-374` |
| 工具绑定到 agent | `sdk/nexent/core/agents/nexent_agent.py:569-612, 950-974` |
| 上下文项定义 / 顺序 / 压缩 | `sdk/nexent/core/agents/context/models.py:14-43, 370-371` |
| 清单渲染 | `sdk/nexent/core/agents/context/formatting.py:44-61`、`rendering.py:186-237` |
| 清单构造 | `sdk/nexent/core/agents/prompt/composer.py:220-234` |
| 评分 TODO | `sdk/nexent/core/agents/context/handlers/skill_handler.py:16-38` |
| 技能提示词（中） | `sdk/nexent/core/prompts/zh/agent/{context_sections.yaml:26-29,82-85, agent_worker.yaml:12-24}` |
| 后端组装入口 | `backend/agents/create_agent_info.py:789-817, 834-867, 1018-1186, 1747-1763, 1841-1872` |
| 服务层 | `backend/management/services/skill/service.py:124-179, 368-492, 530-642, 797-843` |
| ZIP/schema 解析 | `backend/management/services/skill/support.py:310-360, 614-752, 814-948, 1035-1097` |
| DB 访问 | `backend/database/skill_db.py:41-63, 194-208, 367-452, 737-758` |
| 表模型 | `backend/database/db_models.py:1760-1779, 1887-1968` |
| DDL 迁移 | `deploy/sql/migrations/v2.0_merged_migrations.sql:11-107`、`v2.2_merged_migrations.sql:6,22`、`v2.3_merged_migrations.sql:273-295` |
| HTTP 路由 | `backend/apps/skill_app.py:47-48, 81-807`、`backend/apps/skill_repository_app.py:28-329` |
| 请求/响应模型 | `backend/consts/model.py:877-883, 1384-1394, 2272-2316` |
| 常量与限额 | `backend/consts/const.py:113, 116-118, 257-263` |
| 前端类型 / 服务 | `frontend/types/skill.ts:38-46, 124-129`、`frontend/services/skillService.ts:168, 248, 305, 666, 675, 701` |
| 前端参数弹窗 | `frontend/app/[locale]/agents/components/capability/skill/SkillConfigModal.tsx:178-221` |
| 开发期 Skill（B 类） | `.agents/skills/*/SKILL.md`、`.claude/skills/*/SKILL.md`、`.claude/skills/skill-creator/{SKILL.md, scripts/*.py, references/*.md, .openskills.json}` |
| 官方文档 | `doc/docs/zh/backend/skills/{overview.md,index.md}`、`doc/docs/zh/user-guide/resource-repository/{custom-file-generation-skill.md,skill-repository.md}`、`doc/docs/zh/integration/integration-in/skills.md` |
