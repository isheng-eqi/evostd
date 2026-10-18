---
name: asset-onboarding
description: 把一份新的多模态数据资产登记入库，流程是三模态识别、六维资产标识逐维填字段、用 std_hard_anchors 取机械锚点做入库前自查、用 std_verify 确认零违例，最后调 asset_ingest 入库并产出一张资产卡。当用户说「把这个文件入库」「新资产登记」「这批资料加进资产库」「识别一下这份 PDF 和这张照片」「资产标识卡给我」时使用。不适用于：检查已在库资产合不合规（用 standard-verify）、给系统补一条判据（用 clause-proposal）、解释某个结论的依据（用 decision-trace）。本技能不得绕过自查直接入库，也不得手工编造资产标识。
tags:
  - 数据资产
  - 入库
  - 资产标识
  - 多模态
allowed-tools:
  - asset_ingest
  - asset_search
  - std_hard_anchors
  - std_clause_list
  - std_verify
script_outputs:
  scripts/build_onboarding_card.py:
    kind: file
    mime_types:
      - application/json
      - text/markdown
---

# 多模态资产入库（asset-onboarding）

一道**有牙齿的卡口**：不合格的资产进不来，合格进来的每一条都带得出六维标识。

流程固定四步：**三模态识别 → 六维标识 → 硬锚点自查 → 入库**。

## 一、卡口原则（先读）

1. **自查不通过就不入库。** 存在未豁免的违例时，`asset_ingest` 一次都不能调。
2. **资产标识由 `asset_ingest` 生成，不许手写。** 你只提供内容与元数据；
   `asset_id` 的格式合规性由入库端负责。
3. **不许为了「让它过」而改数据。** 字段值来自文件本身；改值等于伪造资产。
4. **豁免必须留痕。** 确实要放行的违例，必须带 `approved_by`（批准人）与理由，
   写进输入并在资产卡里显示，不许静默忽略。

## 二、第 1 步：三模态识别

本项目只做**三模态**：**文本 / 表格 / 图文**。
视频、纯图表暂不支持——遇到就明确告知用户「本版不覆盖」，不要硬塞进三模态里的某一个。

| 模态 | 判定依据 | 典型文件 |
|---|---|---|
| **文本** | 内容以连续自然语言段落为主，无表格结构 | `.txt` `.md` 工单、说明、纪要 |
| **表格** | 存在行列结构，单元格是可寻址的值 | `.csv` `.xlsx`，或 PDF 里以表格为主的页 |
| **图文** | 图像与其说明文字共同承载信息 | 质检照片 + 说明、扫描件 + 标注 |

识别输出必须落在受控词表内：`文本` / `表格` / `图文` / `影像`。
其中 **`图文` 与 `影像` 是两个不同取值**，别混：`图文` 是图 + 文共同表意，
`影像` 是影像类资产本体。选定后**同时判断是否需要 `annotation_note`**（图文/影像模态通常需要）。

## 三、第 2 步：六维资产标识

逐维填字段，一维都不许空着交付。完整字段定义、来源判据与常见错填见
<reference path="references/six-dimensions.md" />。

| 维 | 字段 | 把关判据 |
|---|---|---|
| D1 身份维 | `asset_id`、`cited_standards` | 标识格式、引用标准必须在受控清单内 |
| D2 形态维 | `modality`、`annotation_note` | 模态在受控词表内；图文/影像需标注说明 |
| D3 权限维 | `secrecy`、`declassify_date` | 密级在受控词表内；秘密级必须有解密日期 |
| D4 归属维 | `owner_dept`、`storage_uri` | 责任部门与存储位置必填 |
| D5 时序维 | `collect_time`、`ingest_time` | 采集时间不得晚于入库时间 |
| D6 质量维 | `quality_score`、`completeness`、`file_hash` | 分数区间、完整度区间、哈希格式 |

取字段值的三条来源，按优先级：

1. 文件里直接读得到的（表头、页脚、正文）
2. 用户明确给的
3. **取不到就留空并标为缺口**——不要用「看起来合理」的值填空

## 四、第 3 步：硬锚点自查（入库前最后一道）

```
std_hard_anchors()                 # 全部锚点
std_clause_list(state="active")    # 条款原文与受控词表
std_verify(assets=[<草稿资产>])     # 逐条判据实测
```

- **硬锚点**是标准文本里机械可判定的部分（受控词表、取值域、必填清单、条款编号关系、附录示例），
  零人工、无争议。用它把「受控词表 / 取值域 / 必填 / 格式」四类一次性自查掉。
- `std_verify` 的返回是**唯一**的判定来源。**`violations` 必须为 0** 才能进第 4 步。
- 自查结果要能回答三个问题：哪一维不合格、不合格的字段值是什么、依据哪一条条款。

## 五、第 4 步：落盘 → 出卡 → 入库

### 5.1 先出卡（不调入库）

用本轮生成的工作区脚本把材料写进运行工作区（当前目录即 `outputs/`）：

```python
# outputs/write_onboarding_input.py —— 由你本轮生成，用 run_skill_script(source="workspace") 执行
import json
PAYLOAD = {
    "draft": { ... },            # 第 2 步的六维字段，字段名原样
    "modality_source": "…",      # 三模态识别的判定依据，一句话
    "anchors": [ ... ],          # std_hard_anchors 返回里的锚点数组，原样
    "clauses": [ ... ],          # std_clause_list 返回里的 clauses 数组，原样
    "verify": { ... },           # std_verify 对草稿资产的返回，整段原样
    "waivers": [ ... ],          # 可选。要放行的违例，每项必须含 clause_id / reason / approved_by
    "ingest": {},                # 先留空；入库成功后回填 asset_ingest 的返回
}
with open("onboarding-input.json", "w", encoding="utf-8") as fh:
    json.dump(PAYLOAD, fh, ensure_ascii=False, indent=2)
print("written")
```

<use_script path="scripts/build_onboarding_card.py" />

```
run_skill_script(
    skill_name="asset-onboarding",
    script_path="scripts/build_onboarding_card.py",
    params="--in onboarding-input.json --out onboarding-card",
    source="skill",
)
```

脚本产出 `onboarding-card.json`（机器可读）与 `onboarding-card.md`（给人看），
并在 JSON 里给出 `decision.status`：

- `allow` → 六维齐全、自查零违例，可以入库；
- `deny` → 列出原因（缺哪一维、违反哪条条款），**此时不得调 `asset_ingest`**。

### 5.2 再入库

仅当 `decision.status == "allow"`：

```
asset_ingest(asset=<draft>, modality=<modality>, dimensions=<六维标识>)
```

入库成功后再跑一次脚本，把 `asset_ingest` 的返回填进 `ingest` 字段重新出卡，
这样资产卡上才有真正的 `asset_id` 与目录项。

### 5.3 交付

把两次 `run_skill_script` 的返回**原样**回给用户，并附上：资产标识、六维摘要、
自查结论、以及（若有）豁免项与缺口。

## 六、硬规则

1. **`violations > 0` 且未豁免 → 拒绝入库。** 明确告诉用户违反了哪条、该改哪个字段。
2. **`asset_id` 不由你生成**，只在 `asset_ingest` 返回后回填。
3. **模态判定写清依据**，不允许只给结论不给理由。
4. **不覆盖已有资产。** 入库前用 `asset_search` 查重；命中同源资产时报告冲突，
   由用户决定是修订还是新建。
5. **时间字段一律从文件或用户处取**，脚本不使用当前时钟生成任何值。
6. **不把「入库成功」讲成「数据合规」。** 入库只证明它通过了现行判据库的自查，
   不证明它符合标准全文（未编译条款未覆盖）。

## 七、降级与排错

| 现象 | 处理 |
|---|---|
| 文件不属于三模态 | 明确告知本版不覆盖，不要强行归类。记下它作为后续扩张的输入 |
| 六维里有字段取不到值 | 标为缺口并拒绝入库；让用户补值，**不要**填默认值 |
| `std_verify` 返回 `violations > 0` | 逐条列给用户并给出修复动作；改完重新走第 3 步 |
| `std_hard_anchors` 返回空 | 说明该条款类型还没有机械锚点。此时**不能**靠猜值入库，转 `clause-proposal` 补锚点 |
| `asset_search` 命中同源资产 | 报告冲突与两边差异，交由用户决定，不要自动覆盖 |
| `asset_ingest` 失败 | 原样报错，不要重试第二次入库（可能已部分写入）；用 `asset_search` 确认实际状态后再决定 |
| 用户要求「先入库再补字段」 | 拒绝。入库前自查是卡口，不是流程装饰 |

## 八、本技能的资源

- 执行脚本：`scripts/build_onboarding_card.py`
- 六维资产标识的字段定义与错填对照：<reference path="references/six-dimensions.md" />
