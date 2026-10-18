# std_verify / std_compliance / std_clause_list 输入输出契约

本文件是 `standard-verify` 读写的 JSON 形状。字段名与 `standard/compiler.py` 的 `Finding`
数据类逐字对应，判据端与渲染端共用同一套字段，**任何一端改名都会导致报告字段缺失**。

## 一、`std_verify` 入参

```json
{
  "assets": [
    {
      "asset_id": "AST-2026-0001",
      "modality": "表格",
      "secrecy": "内部",
      "owner_dept": "设备管理部",
      "collect_time": "2026-03-01",
      "ingest_time": "2026-03-05",
      "quality_score": 87.5,
      "completeness": 0.93,
      "file_hash": "aaaaaaaa...（64 位小写十六进制）",
      "cited_standards": ["GB/T 33863.1-2017"],
      "storage_uri": "oss://assets/2026/0001.csv"
    }
  ],
  "ctx": {
    "controlled_standards": ["GB/T 33863.1-2017", "GB/T 42131-2022"],
    "retired_assets": []
  }
}
```

- `ctx` 装**受控清单**（白名单 / 黑名单）。判据 `跨文档引用` 与 `禁止取值` 只从 `ctx` 里取清单，
  不给清单时这两类判据一律放行——所以调用前必须确认清单已带上。
- 字段名即判据的寻址键，**不要**做驼峰化、翻译或重命名。

## 二、`std_verify` 返回（渲染脚本认这个形状）

```json
{
  "standard": "工业数据资产登记与校验规范（演示样例）",
  "snapshot_id": "clause-db-2026-10-11T09-00-00Z",
  "summary": {
    "assets": 5,
    "checked_clauses": 12,
    "violations": 7,
    "compliance": 0.8833
  },
  "results": [
    {
      "asset_id": "AST-2026-0001",
      "compliant": false,
      "findings": [
        {
          "clause_id": "第6条",
          "source": "《工业数据资产登记与校验规范（演示样例）》第6条",
          "ok": false,
          "message": "modality=「表格图像」不在受控词表 ['文本', '表格', '图文', '影像'] 内",
          "evidence": {"field": "modality", "value": "表格图像"},
          "clause_text": "数据资产的模态应属于受控词表：文本、表格、图文、影像。"
        }
      ]
    }
  ]
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `results[].asset_id` | 是 | 被校验资产 |
| `results[].compliant` | 否 | 该资产是否全部通过；缺省时由 `findings` 里是否存在 `ok=false` 推导 |
| `findings[].clause_id` | 是 | 条款号，如 `第6条` |
| `findings[].source` | 是 | 全称引用，如 `《标准名》第6条`。**报告里展示这个，不展示 `clause_id` 单号** |
| `findings[].ok` | 是 | `false` 即违例 |
| `findings[].message` | 是 | 一句话说清哪里不对，已含实际取值 |
| `findings[].evidence` | 是 | 结构化证据，字段名与取值原样展示 |
| `findings[].clause_text` | 否 | 条款原文。缺失时由 `std_clause_list` 补齐 |

### 兼容形状

渲染脚本同时接受下面两种退化形状，便于早期 MCP 版本对接：

- 扁平违例表：`{"violations": [{"asset_id": "...", "clause_id": "...", "message": "...", ...}]}`
- 裸数组：`[{"asset_id": "...", "findings": [...]}]`

三种形状渲染出的 Markdown 结构一致。

## 三、`std_compliance` 返回

```json
{
  "compliance": 0.8833,
  "detail": [
    {"asset_id": "AST-2026-0001", "compliance": 0.9167, "violations": 1}
  ],
  "assets": 5,
  "checked_clauses": 12,
  "snapshot_id": "clause-db-2026-10-11T09-00-00Z"
}
```

- `compliance` 是**适应度口径**：每条判据等权，`通过判据数 ÷ 判据总数`，再对资产取均值。
- 报告摘要里的合规率只能抄这个值。自己用「违例数 ÷ 资产数」手算会得到另一个数，属于口径错误。

## 四、`std_clause_list` 返回

```json
{
  "snapshot_id": "clause-db-2026-10-11T09-00-00Z",
  "clauses": [
    {
      "clause_id": "第6条",
      "title": "资产模态受控",
      "kind": "受控词表",
      "text": "数据资产的模态应属于受控词表：文本、表格、图文、影像。",
      "standard": "工业数据资产登记与校验规范（演示样例）",
      "source": "《工业数据资产登记与校验规范（演示样例）》第6条",
      "params": {"field": "modality", "allowed": ["文本", "表格", "图文", "影像"]},
      "state": "active",
      "compilable": true
    }
  ],
  "uncompilable": [
    {"clause_id": "第20条", "title": "业务真实性", "text": "数据资产应真实反映其所描述的业务过程，不得虚构。"}
  ]
}
```

| 字段 | 用途 |
|---|---|
| `snapshot_id` | 报告必须写明「依据哪一版判据库」，否则结论不可复算 |
| `clauses[].text` | 条款原文，用于补齐 `clause_text` |
| `clauses[].kind` | 判据类型，决定了「怎么讲给人听」与「怎么修」（见 `clause-taxonomy.md`） |
| `clauses[].params.field` | 该条款约束的字段名，用于生成修复动作 |
| `clauses[].state` | `active` 才参与校验；`draft` / `retired` 不参与，但要在报告里区分开 |
| `uncompilable[]` | **只能靠人判断**的条款。单列「无人把关的条款」一节，不计入合规率 |

## 五、渲染脚本的命令行

```
python scripts/render_compliance_report.py \
    --verify std_verify.json \
    [--clauses std_clause_list.json] \
    [--compliance std_compliance.json] \
    [--title "报告标题"] \
    [--out compliance-report.md]
```

- 除 `--verify` 外全部可选。缺 `--clauses` 时报告不输出条款原文与无人把关条款两节，并在文首标注「条款原文缺失」。
- `--out` 给裸文件名即写入脚本当前目录（Nexent 沙箱里就是本轮运行工作区的 `outputs/`）。
- stdout 恰好一行 artifact 契约 JSON，不输出任何其它内容。
