# 02 · Nexent v2.7.0 MCP 接入指南（实读版）

> 代码基线：`第三方/nexent`，`VERSION` = `v2.7.0`（`第三方/nexent/VERSION:1`）
> 本文所有结论均来自实际读源。凡未读到实证的写 **【未验证】**。
> 路径约定：下文 `backend/...`、`sdk/...` 均相对 `第三方/nexent/`；行号为该文件当前行号。

---

## 0. 先纠正三处"任务书里写了但仓库里不存在"的路径

任务书列的必读清单与仓库实际结构有出入，先对齐，否则会找不到文件：

| 任务书要求 | 实际情况 |
|---|---|
| `backend/tool_collection/mcp/remote_mcp_service.py` | **不存在**。`backend/tool_collection/mcp/` 下只有 3 个文件：`local_mcp_service.py`、`nl2agent_mcp_service.py`、`nl2agent_mcp_tools.py`（见 `backend/tool_collection/` 目录树）。真正的 remote MCP 业务在 `backend/services/remote_mcp_service.py`（1761 行） |
| `backend/services/remote_mcp_service.py` | ✅ 存在，是 remote MCP 的**业务服务层**（CRUD/健康检查/容器编排），**不是** MCP 协议服务 |
| `sdk/nexent/core/tools/` 里"所有与 MCP 相关的代码" | `sdk/nexent/core/tools/` 下**没有任何 MCP 专属文件**。MCP 的 SDK 侧代码在 `sdk/nexent/core/agents/`（`managed_mcp.py`、`run_agent.py`、`tool_user_context.py`）与 `sdk/nexent/container/`（`docker_client.py`、`k8s_client.py`）、`sdk/nexent/consts/mcp_errors.py` |
| `docker/` 根目录、`k8s/` 根目录 | **不存在**。部署编排全在 `deploy/docker/compose/`、`deploy/images/dockerfiles/` |

### 0.1 全仓 MCP 关键文件索引（先读这张表）

| 层 | 文件 | 职责 |
|---|---|---|
| MCP 协议服务进程 | `backend/mcp_service.py`（556 行） | FastMCP 主服务 + 租户路由 + OpenAPI 管理 API，入口 `python backend/mcp_service.py` |
| 内置工具挂载 | `backend/tool_collection/mcp/local_mcp_service.py`（26 行） | `FastMCP("local")`，挂 `nl2a` 子服务 |
| 内置 NL2Agent 工具 | `backend/tool_collection/mcp/nl2agent_mcp_service.py`（62 行）、`nl2agent_mcp_tools.py`（1232 行） | 6 个内部工具 |
| Remote MCP 业务层 | `backend/services/remote_mcp_service.py`（1761 行） | 注册/健康检查/启停/工具枚举 |
| 容器编排 | `backend/services/mcp_container_service.py`（486 行） | `MCPContainerManager`，委托 SDK |
| 容器实现 | `sdk/nexent/container/docker_client.py`（709 行）、`k8s_client.py` | Docker / K8s 两种后端 |
| 工具编排 | `backend/services/tool_configuration_service.py`（1560 行） | MCP→ToolInfo 扫描、`/tool/list`、工具测试 |
| 运营/市场 | `backend/services/mcp_management_service.py`、`backend/apps/mcp_management_app.py` | 社区上架/审核/下载 |
| HTTP 路由 | `backend/apps/remote_mcp_app.py`（1142 行，`prefix="/mcp"`）、`mcp_management_app.py`（546 行，`prefix="/mcp-tools"`） | 都挂在 config 服务（`backend/apps/config_app.py:200-201`，端口 5010） |
| SDK 运行时 | `sdk/nexent/core/agents/managed_mcp.py`（336 行）、`run_agent.py`、`agent_model.py` | 连接/超时/取消 |
| DB | `backend/database/remote_mcp_db.py`（471 行）、`db_models.py:1441-1555` | `mcp_record_t` 等 |
| 前端 | `frontend/app/[locale]/mcp-space/`、`frontend/app/[locale]/agents/[agentId]/components/capability/McpConfigModal.tsx` | MCP 空间 + Agent 工具配置弹窗 |
| 官方技能 | `deploy/docker/assets/official-skills-zip/mcp-builder.zip` | 内置"写 MCP Server"技能（Anthropic 原版） |

---

## 1. Nexent 支持哪些 MCP 传输方式

### 1.1 结论表

| 形态 | 实际传输 | Nexent 是否原生 | 判定/配置入口 |
|---|---|---|---|
| 远程 MCP（SSE） | `sse` | ✅ | URL 以 `/sse` 结尾自动判定 |
| 远程 MCP（Streamable HTTP） | `streamable-http` | ✅ | URL 以 `/mcp` 结尾自动判定 |
| **本地 stdio MCP** | **不直连** | ❌ 需容器代理 | 只能走"容器化"方式，由 `mcp-proxy` 把 stdio 转成 streamable-http |
| 容器化 MCP | 对外仍是 `streamable-http` | ✅ | 提交 `mcpServers.command/args/env` + 端口 |
| API→MCP（OpenAPI 转 MCP） | 进程内 `FastMCP.from_openapi()`，**不是网络 MCP** | ✅ | 提交 `config_json` 含 `openapi` 键 |
| 平台内置 MCP（local/tenant） | `sse`（仅平台自用） | ✅ | `http://nexent-mcp:5011/mcp/{tenant_id}/sse` |

### 1.2 传输判定逻辑（三处实现完全一致，硬编码后缀匹配）

```
URL 以 "/sse" 结尾        → sse
URL 以 "/mcp" 结尾        → streamable-http
其它任意 URL             → streamable-http（兜底）
```

- SDK 侧：`sdk/nexent/core/agents/run_agent.py:269-286`（`_detect_transport`）
- 后端健康检查侧：`backend/services/remote_mcp_service.py:115-132`、`:164-181`
- 后端工具枚举侧：`backend/services/tool_configuration_service.py:131-156`（`_create_mcp_transport`，用 `SSETransport` / `StreamableHttpTransport`）
- 组装 `mcp_host` 时：`backend/agents/create_agent_info.py:2880-2883`

**显式传输字段的合法取值只有两个**，写别的直接抛错：

```python
# sdk/nexent/core/agents/run_agent.py:326-327
if transport not in ("sse", "streamable-http"):
    raise ValueError(f"Invalid transport type: {transport}. Must be 'sse' or 'streamable-http'")
```

自检：`ToolConfig` 里 `source` 取值见 `backend/agents/create_agent_info.py:2175`（`tool.get("source")`），SDK 分派见 `sdk/nexent/core/agents/nexent_agent.py:665-674`（`local` / `mcp` / `langchain` / `builtin`）。

### 1.3 三种接入方式的具体配置

#### 方式 A：远程链接（SSE / Streamable HTTP）

请求体模型 `AddMcpServiceRequest`（`backend/consts/model.py:2351-2376`）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | str | ✅ | 服务名，租户内唯一（重名 409） |
| `server_url` | str | ✅ | MCP 端点，**必须含传输后缀** |
| `description` / `tags` | str / list | ❌ | 展示用 |
| `source` | enum | ❌ | `local`(默认) / `mcp_registry` / `community`（`model.py:2344-2348`） |
| `authorization_token` | str | ❌ | 会被塞进 `Authorization` 头 |
| `custom_headers` | dict | ❌ | 任意自定义头（JSON 对象） |
| `container_config` / `container_port` | dict / int | ❌ | 容器方式才用 |
| `config_json` | dict | ❌ | 含 `openapi` 键时走 API→MCP |
| `enabled` | bool | ❌ | 默认 `False`；`True` 会在创建时置 `status=True` |
| `skip_health_check` | bool | ❌ | 供社区导入兜底，跳过连通性校验 |

前端弹窗里的输入项（`frontend/app/[locale]/agents/[agentId]/components/capability/McpConfigModal.tsx:880-955`）：服务器名称、服务器 URL、自定义 Headers(JSON)、Authorization Token。
中文提示原文：`"服务器URL (如: http://localhost:3001/mcp)，目前支持sse和streamable-http协议"`（`frontend/public/locales/zh/common.json:1660`）。

> ⚠️ Docker 部署时本地服务地址要写 `host.docker.internal`，不要写 `127.0.0.1` —— 见官方文档 `doc/docs/zh/backend/tools/mcp.md:98`。

#### 方式 B：容器化（stdio 的唯一落地路径）

请求体 `AddContainerMcpServiceRequest`（`backend/consts/model.py:2379-2399`），核心是 `mcp_config`（`MCPConfigRequest`，`model.py:1865-1868`）：

```json
{ "mcpServers": { "<任意键>": { "command": "npx", "args": ["-y","pkg@ver"], "env": {"KEY":"VAL"}, "port": 5031, "image": "可选，覆盖默认镜像" } } }
```

单服务器配置模型 `MCPServerConfig`（`backend/consts/model.py:1850-1862`）：`command` / `args` / `env` / `port` / `image`。

Nexent 收到后**不是直接跑 command**，而是拼一条代理命令把它转成 HTTP（`backend/services/remote_mcp_service.py:600-613`）：

```python
full_command = ["python", "-m", "mcp_proxy",
                "--host", "0.0.0.0", "--port", str(port),
                "--transport", "streamablehttp",
                "--", command, *(config.args or [])]
```

约束与细节：
- 只允许 **1 个** `mcpServers` 条目，否则 `McpValidationError`（`remote_mcp_service.py:584-586`）。
- `command` 必填；**`command == "docker"` 被显式拒绝**（`remote_mcp_service.py:589-593`）。
- `authorization_token` 会被注入到容器环境变量 `authorization_token`（`remote_mcp_service.py:595-598`）。
- 镜像默认 `nexent/nexent-mcp:latest`（`backend/consts/const.py:436-437`，`NEXENT_MCP_DOCKER_IMAGE`）。
- 端口必须是宿主机空闲端口：`check_container_port_conflict()`（`remote_mcp_service.py:294-298`）同时查 DB 记录与真实监听；`suggest_container_port()` 在 2000–50000 随机试 1000 次（`:301-311`）。
- 也可"上传镜像 tar 包"启动：`POST /mcp/upload-image[/stream]`（`backend/apps/remote_mcp_app.py:992-1067`），仅 `.tar`、≤1GB（`remote_mcp_service.py:1657-1662`），需 `ENABLE_UPLOAD_IMAGE` 打开（`const.py:438`）。

镜像本体（`deploy/images/dockerfiles/mcp/Dockerfile`）：`python:3.11-slim` + Node.js 20.17.0（:30）+ `pip install mcp-proxy`（可选 `MCP_PROXY_VERSION` 钉版本，:49-59）。**该 Dockerfile 没有 ENTRYPOINT/CMD**，所以命令必须由调用方给（正是上面那条 `mcp_proxy` 命令）。

#### 方式 C：API → MCP（不写 MCP Server）

- 判定：`config_json` 是 dict 且含 `"openapi"` 键（`remote_mcp_service.py:465`、`:998`、`:1490`、`:1557`、`:1060-1063`）。
- 实现：`FastMCP.from_openapi(openapi_spec=..., client=httpx.AsyncClient(base_url=server_url, ...), name=service_name)`（`backend/mcp_service.py:257-261`），一个 OpenAPI 服务 = 一个 FastMCP 实例，动态 `mount` 到租户 MCP（`mcp_service.py:268-274`）。
- 这些工具在 DB 里的 `usage` 统一是 **`outer-apis`**（`tool_configuration_service.py:825`、`remote_mcp_service.py:1089`）。
- 租户级热刷新：`refresh_openapi_services_by_tenant()`（`mcp_service.py:325-389`），通过 `POST /tools/openapi_service/refresh?tenant_id=...`（`:132-151`）触发。

### 1.4 一句话回答"stdio 怎么办"

**Nexent 的 agent 运行时没有 stdio 客户端**——`mcpadapt` + `fastmcp.Client` 只走 HTTP 传输。想在 Nexent 里用 stdio MCP Server（`command`+`args` 那种），**只有一条路**：走"容器化"注册，由 `nexent/nexent-mcp` 镜像里的 `mcp-proxy` 把它桥接成 `http://<host>:<port>/mcp`。

---

## 2. MCP Server 的注册与生命周期

### 2.1 数据库表结构

#### `nexent.mcp_record_t`（核心表）

ORM：`backend/database/db_models.py:1441-1489`（`class McpRecord`）
DDL 来源（多次 migrations 叠加，顺序即演进）：
`deploy/sql/migrations/v1_merged_migrations.sql:99-110`（初始 9 列）、`:178-180`（`status`）、`:867-870`（`container_id`）、`:892-901`（`authorization_token`）；`deploy/sql/migrations/v2.2_merged_migrations.sql:163-170`（`source`/`registry_json`/`config_json`/`enabled`/`tags`/`description`/`container_port`）、`:233-234`（`custom_headers`）；`deploy/sql/migrations/v2.3_merged_migrations.sql:426-429`（`market_id`）；`deploy/sql/migrations/v2.4_merged_migrations.sql:209-224`（`group_ids`/`ingroup_permission`/`shared_fields`）。

合并后的最终形态：

| 列 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `mcp_id` | SERIAL PK | — | 主键 |
| `tenant_id` | VARCHAR(100) | NULL | 租户 |
| `user_id` | VARCHAR(100) | NULL | 创建者 |
| `mcp_name` | VARCHAR(100) | NULL | 服务名。**DB 层无唯一约束**（`idx_mcp_record_t_tenant_name` 非 unique，`v2.2_merged_migrations.sql:185-186`），唯一性由应用层 `check_mcp_name_exists()` 保证（`remote_mcp_db.py:400-414`） |
| `mcp_server` | VARCHAR(500) | NULL | MCP URL；容器方式为 `http://<host>:<port>/mcp` |
| `status` | BOOLEAN | NULL | 连通状态：true/false/null(未知) |
| `container_id` | VARCHAR(200) | NULL | Docker 容器 ID，非容器为 NULL |
| `container_port` | INTEGER | NULL | 宿主机绑定端口 |
| `authorization_token` | VARCHAR(500) | NULL | **明文存库**，喂给 `Authorization` 头 |
| `custom_headers` | JSON | NULL | 自定义 HTTP 头 |
| `source` | VARCHAR(30) | NULL | `local`/`mcp_registry`/`community` |
| `market_id` | INTEGER | NULL | 上架到市场后的记录 ID |
| `registry_json` | JSONB | NULL | 完整 registry 快照，含 `_toolNames`、`tools` |
| `config_json` | JSON | NULL | 容器配置（`mcpServers`）或 OpenAPI JSON |
| `enabled` | BOOLEAN | TRUE | 启用开关 |
| `tags` | TEXT[] | NULL | 标签（GIN 索引） |
| `description` | TEXT | NULL | 描述 |
| `group_ids` | VARCHAR | NULL | 逗号分隔可访问用户组 |
| `ingroup_permission` | VARCHAR(30) | `READ_ONLY` | `EDIT`/`READ_ONLY`/`PRIVATE` |
| `shared_fields` | JSON | NULL | 字段级共享开关，如 `{"serverUrl":true,"authorizationToken":false}` |
| `create_time`/`update_time`/`created_by`/`updated_by`/`delete_flag` | 审计列 | `delete_flag='N'` | **软删除** |

索引/触发器：`idx_mcp_record_t_tenant_delete`、`idx_mcp_record_t_tenant_name`、`idx_mcp_record_t_tenant_server`、`idx_mcp_record_t_tags_gin`（`v2.2_merged_migrations.sql:182-192`）；`update_mcp_record_update_time_trigger` 自动维护 `update_time`（`v1_merged_migrations.sql:142-146`）。

#### 相关表

| 表 | 定义位置 | 作用 |
|---|---|---|
| `nexent.mcp_community_record_t` | `db_models.py:1492-1519`；DDL `v2.2_merged_migrations.sql:78-98` | 社区共享记录，含 `transport_type`(`http/sse/container`)、`review_status`(`pending/approved/rejected/offline`)、`review_type` |
| `nexent.mcp_market_record_t` | `db_models.py:1522-1555`；DDL `v2.3_merged_migrations.sql:447-470` | 市场单表（覆盖所有上架态），`review_status`(`not_shared/pending_review/rejected/shared`)、`download_count`、`source_mcp_id`、`content` |
| `nexent.ag_tool_info_t` | `db_models.py:638-662` | MCP 工具的**目录表**：`name`/`origin_name`/`class_name`/`source`/`usage`/`inputs`/`output_type`/`is_user_selectable`/`is_available` |
| `nexent.ag_tool_instance_t` | `db_models.py:792-813` | **Agent↔工具绑定 + 参数**：`tool_id`/`agent_id`/`params`/`enabled`/`version_no` |
| `nexent.ag_tenant_agent_t` | `db_models.py:665-751` | Agent 主表（工具通过 `ag_tool_instance_t` 关联） |
| `nexent.ag_outer_api_tools` | `v2.0_merged_migrations.sql:187-199` | API→MCP 的服务级元数据，含 `mcp_service_name` |

关键语义：MCP 服务在 `mcp_record_t` 一行，**它的每个工具**在 `ag_tool_info_t` 一行，**绑到某个 Agent** 在 `ag_tool_instance_t` 一行。三张表靠 `usage`(= mcp_name) 和 `tool_id`/`agent_id` 串起来。

### 2.2 是否容器化执行

两种模式并存，由记录内容区分，判定函数 `_is_container_record()`（`backend/services/remote_mcp_service.py:223-243`）：

> 有 `container_id`，**或** `config_json` 是非空 dict（且不含 `openapi` 键）→ 容器记录。

- **非容器**：Nexent 只存 URL，进程外由你自己运维。
- **容器**：`MCPContainerManager`（`backend/services/mcp_container_service.py:27-64`）在初始化时按 `IS_DEPLOYED_BY_KUBERNETES`（`const.py:770`）二选一：
  - Docker：`DockerContainerConfig(docker_socket_path=...)`，实际通过 `/var/run/docker.sock` 操作宿主 daemon。
  - K8s：`KubernetesContainerConfig(namespace=KUBERNETES_NAMESPACE, in_cluster=True)`（`const.py:772`，默认命名空间 `nexent`），Pod 名 `mcp-{name}-{tenant8}-{user8}-{uuid8}`（`k8s_client.py:126`），容器固定名 `mcp-server`（`k8s_client.py:284`）。
- Docker 容器的运行参数（`sdk/nexent/container/docker_client.py:378-396`）：`restart_policy=unless-stopped`、`stdin_open=True`（为 stdio 服务留 stdin）、**本地模式才发布端口**（`ports={f"{container_port}/tcp": host_port}`），容器内则走 Docker 网络 DNS 不发布端口。环境变量注入 `PORT`/`TRANSPORT=streamable-http`/`NODE_ENV=production`（`:369-373`）。
- 容器内监听端口会被自动探测（`_detect_container_port`，`docker_client.py:177-189`、调用点 `:364`），以兼容镜像里硬编码 `--port 3033` 的情况。

**编排归属（容易踩坑）**：`nexent-mcp` 服务本身（`deploy/docker/compose/docker-compose.yml:158-190`）**没有挂载 docker.sock**；挂载了 `/var/run/docker.sock:ro` 的是 `nexent-config`（`:95`）和 `nexent-runtime`（`:135`）。因为 `/mcp/*` 路由挂在 config 服务上（`backend/apps/config_app.py:200-201`），所以**创建/启停 MCP 容器这一步是由 nexent-config 容器发起的**。`:ro` 只读挂载仍可调用 Docker API（API 写操作不经文件写）。

### 2.3 健康检查（4 个粒度，逐级变重）

| 级别 | 函数 / 入口 | 行为 | 超时 |
|---|---|---|---|
| L0 轻量握手 | `test_mcp_connection()` `remote_mcp_service.py:198-216`，HTTP `POST /mcp/test-connection` (`remote_mcp_app.py:811-840`) | 只做 MCP `initialize` 握手，返回 `client.is_connected()` | `MCP_REQUEST_TIMEOUT_SECONDS` 默认 **10s**（`const.py:576-580`） |
| L1 完整可达 | `mcp_server_health()` `:90-106` → `_mcp_protocol_health_check()` `:109-150` | 建连 + `list_tools()`，**能列工具才算健康** | 同上 |
| L2 落库 | `check_mcp_service_health()` `:1392-1458`，HTTP `GET /mcp/healthcheck` (`remote_mcp_app.py:768-803`) | L1 + 把结果写回 `mcp_record_t.status` | 同上 |
| L3 落库(按名/URL) | `check_mcp_health_and_update_db()` `:1349-1389` | 内部调用用 | 同上 |
| 容器就绪 | `_wait_for_service_ready()` `docker_client.py:452-518` | **30 次 × 5s** 重试 `is_connected()` | 最长 ≈150s |
| 容器异步就绪 | `remote_mcp_service.py:637-651`（`wait_for_ready=False` 分支） | **30 次 × 5s** 调 L1 | 最长 ≈150s |
| 容器 enable 路径 | `remote_mcp_service.py:935-949` | **10 次 × 0.5s** 调 L1 | ≈5s |

错误归一化：`_format_mcp_connection_error()`（`remote_mcp_service.py:67-83`）把底层异常链映射成 6 种人话：`MCP connection timeout` / `connection refused` / `authentication failed` / `endpoint not found` / `protocol or endpoint invalid` / `address unreachable`。

### 2.4 失败处理

| 场景 | 行为 | 位置 |
|---|---|---|
| 新增远程 MCP 连不上 | 抛 `MCPConnectionError` → HTTP **503** | `remote_mcp_service.py:356-358`，`remote_mcp_app.py:217-222` |
| 容器 MCP 连不上（非等待模式） | **不报错**，只写 warning，`_toolNames` 留空，后续 `/mcp/refresh-tools` 补 | `remote_mcp_service.py:395-405` |
| 容器启动失败 | `except` 里 **stop 掉已启动的容器**防孤儿，再抛出 | `remote_mcp_service.py:672-679` |
| 上传镜像启动后注册失败 | 停容器再抛 | `remote_mcp_service.py:1739-1751` |
| enable 时端口被占 | 先停旧容器 → 扫同端口孤儿容器并停 → 再检查 → 仍占用抛 `McpPortConflictError`(409) | `remote_mcp_service.py:853-881` |
| enable 后健康检查失败 | 停新容器，`container_id=None`、`status=False` 落库，抛 `MCPConnectionError` | `remote_mcp_service.py:951-966` |
| 工具扫描整体失败 | **不阻断** local/langchain 工具刷新；已启用 MCP 的工具保持"可用"状态，不因瞬时故障消失 | `tool_configuration_service.py:804-832` |
| 删除 MCP | 先停容器 → 若是 API 型则删 OpenAPI 服务并热刷新 → `set_mcp_tools_unavailable()` 让工具对用户不可见并解绑 Agent 草稿 → 软删记录 → 清标签 | `remote_mcp_service.py:1052-1105` |
| 工具调用超时 | 见 §4.4 | |
| 租户配额 | 每租户最多 `MAX_MCP_SERVICES_PER_TENANT`（默认 **1000**，`const.py:569-573`）；插入前用 `pg_advisory_xact_lock` 串行化计数防并发超卖 | `backend/database/remote_mcp_db.py:44-65` |

### 2.5 权限与共享

- 列表接口按用户组过滤（`remote_mcp_service.py:1170-1194`）：`group_ids IS NULL` = 公开；是自己创建的可见；组内成员可见，但 `ingroup_permission='PRIVATE'` 对非创建者隐藏。
- 编辑权限：`CAN_EDIT_ALL_USER_ROLES = {"SU","ADMIN","SPEED","ASSET_OWNER"}`（`const.py:294`）或本人创建 → `EDIT`，否则 `READ_ONLY`（`PERMISSION_READ/EDIT`，`const.py:297-298`）；公开 MCP 所有人可编辑（`remote_mcp_service.py:1211-1228`）。
- 上架市场需审核：`/mcp-tools/community/publish` → `review/approve|reject`（`backend/apps/mcp_management_app.py:168-349`）。
- 审计：`mcp_add` / `mcp_update` / `mcp_delete` / `mcp_enable` / `mcp_disable` 写入 `audit.security` logger（`backend/services/audit_service.py:24`、`:103`）；**凭证字段永不入日志**（`remote_mcp_app.py:198-207`、`:266-272`、`:431-437` 的注释明确说明）。

---

## 3. 从零写一个 MCP Server 并接入 Nexent（完整可执行）

> 目标形态：**Streamable HTTP**（Nexent 最稳、无兼容坑）。全程 6 步。
> 想省事：仓库自带官方 `mcp-builder` 技能（`deploy/docker/assets/official-skills-zip/mcp-builder.zip` 内 `mcp-builder/SKILL.md`），里面有 Python/TS 模板、最佳实践、评测生成器。

### 步骤 0 · 环境

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows
pip install "fastmcp>=2.0" httpx
```

### 步骤 1 · 最小 Server 代码

新建 `data_asset_mcp/server.py`：

```python
"""Multimodal data-asset MCP server - minimal but production-shaped."""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastmcp import FastMCP

mcp = FastMCP(name="data-asset-mcp")

# --- 凭证只从环境变量读，绝不硬编码（见 §7.1）---
API_BASE = os.environ.get("DATA_ASSET_API_BASE", "http://127.0.0.1:9000")
API_TOKEN = os.environ.get("DATA_ASSET_API_TOKEN", "")


def _client() -> httpx.Client:
    headers = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}
    return httpx.Client(base_url=API_BASE, headers=headers, timeout=30.0)


@mcp.tool(
    name="asset_search",
    description=(
        "按关键词检索多模态数据资产目录，返回资产 ID、模态类型、存储位置与标签。"
        "当用户想'找某类数据'时使用。"
    ),
)
def asset_search(
    keyword: str,
    modality: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Args:
        keyword: 检索关键词。
        modality: 可选取值 image/video/audio/text/table/pointcloud。
        limit: 返回条数上限，1-100。
    """
    if not 1 <= limit <= 100:
        return {"error": "limit must be within 1..100", "hint": "传 20 试试"}
    with _client() as c:
        r = c.get("/assets", params={"q": keyword, "modality": modality, "limit": limit})
        r.raise_for_status()
        return r.json()


@mcp.tool(name="asset_get", description="按资产 ID 获取单个数据资产的完整元数据。")
def asset_get(asset_id: str) -> dict[str, Any]:
    with _client() as c:
        r = c.get(f"/assets/{asset_id}")
        if r.status_code == 404:
            return {"error": f"asset {asset_id} not found", "hint": "先用 asset_search 拿到有效 ID"}
        r.raise_for_status()
        return r.json()


if __name__ == "__main__":
    # Nexent 只认 streamable-http / sse；默认端口不要撞 Nexent 自己的 5010/5011/5013/5014/5015
    mcp.run(transport="http", host="0.0.0.0", port=8000, path="/mcp")
```

要点（对应 `mcp-builder` 的最佳实践）：
1. **工具名动词开头、带服务前缀**（`asset_search` 而非 `search`），模型才找得到。
2. **description 写"什么时候用"**，不只是"是什么"——它直接进 Agent 的工具清单。
3. **错误消息要可行动**（返回 `hint`），别只抛 500。
4. 返回结构化 dict，别拼接大段字符串。

> `transport` 参数名取决于 fastmcp 版本：新版用 `"http"`，老版用 `"streamable-http"`。若报错就换另一个。【未验证：仓库未锁定 fastmcp 版本号，`deploy/images/dockerfiles/mcp/Dockerfile` 只钉了 `mcp-proxy`】

### 步骤 2 · 本地起服务并自测

```bash
python -m data_asset_mcp.server
# 期望：监听 http://0.0.0.0:8000/mcp
```

自测（关键——**必须先证明是合规 MCP Server**，否则 Nexent 只会给你 503）：

```bash
pip install fastmcp
python - <<'PY'
import asyncio
from fastmcp import Client

async def main():
    async with Client("http://127.0.0.1:8000/mcp") as c:   # 后缀 /mcp → streamable-http
        print("connected:", c.is_connected())
        for t in await c.list_tools():
            print("-", t.name, "|", (t.description or "")[:60])

asyncio.run(main())
PY
```

能打印出 `asset_search` / `asset_get` 就说明 Server 侧 OK。

### 步骤 3 · 让 Nexent 能访问到

| 部署形态 | URL 写法 |
|---|---|
| Nexent 本机源码运行 | `http://127.0.0.1:8000/mcp` |
| Nexent 用 docker-compose 起 | **必须** `http://host.docker.internal:8000/mcp`（容器内 127.0.0.1 是容器自己） |
| Nexent 与 MCP 同 compose 网络 | `http://<你的服务名>:8000/mcp` |

Server 必须监听 `0.0.0.0`，不能只听 `127.0.0.1`（否则容器进不来）。

### 步骤 4 · 注册进 Nexent

#### 路径 A：前端 —— MCP 空间（推荐，可视化）

1. 左侧导航 **MCP 仓库 / MCP 空间** → **我的 MCP**（页面：`frontend/app/[locale]/mcp-space/my-mcp.tsx`；路由前缀 `/mcp-space`，见 `deploy/sql/migrations/v2.2_merged_migrations.sql:734` 的 `LEFT_NAV_MENU='/mcp-space'`）
2. 点「添加 MCP 服务」（组件 `mcp-space/components/AddMcpServiceCard.tsx`）
3. 选 **「添加MCP服务器」** 页签（`mcp-space/components/add/local/AddMcpServiceLocalSection.tsx:30`，i18n key `mcpConfig.addServer.title` = "添加MCP服务器"，`frontend/public/locales/zh/common.json:1658`）
4. 填：
   - 服务器名称：`data-asset`（**只能英文字母/数字/`_`/`-`**，见 `doc/docs/zh/backend/tools/mcp.md:97`）
   - 服务器URL：`http://host.docker.internal:8000/mcp`
   - Authorization Token：留空或 `Bearer xxx`
   - 自定义 Headers：`{"X-API-Key":"xxx"}`（可选）
5. 保存 → 系统会跑 L1 健康检查，失败直接 503 并返回人话原因

#### 路径 A'：前端 —— Agent 页内弹窗（同一套逻辑）

**智能体开发 → 选 Agent → 「选择智能体的工具」页签 → 右上「MCP配置」按钮**
（按钮文案 `toolManagement.mcp.button` = "MCP配置"，`common.json:1552`；弹窗组件 `frontend/app/[locale]/agents/[agentId]/components/capability/McpConfigModal.tsx`，挂载点 `frontend/app/[locale]/agents/[agentId]/components/agent-capability.tsx:122`）

弹窗里 4 个页签（`McpConfigModal.tsx:880/972/1082/1208`）：
**添加MCP服务器** / **添加容器化MCP** / **上传MCP镜像** / **API转换为MCP**

#### 路径 B：HTTP API（可脚本化 / CI 化）

Base：`http://<nexent-config-host>:5010`（`/mcp` 路由挂在 config 服务，`backend/apps/config_app.py:200-201`）

```bash
curl -X POST "http://127.0.0.1:5010/mcp/add" \
  -H "Authorization: Bearer <你的JWT>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "data-asset",
    "server_url": "http://host.docker.internal:8000/mcp",
    "description": "多模态数据资产目录",
    "tags": ["data-asset", "multimodal"],
    "authorization_token": "Bearer xxx",
    "custom_headers": {"X-API-Key": "yyy"},
    "enabled": true
  }'
```

同族接口（全部来自 `backend/apps/remote_mcp_app.py`，`prefix="/mcp"` 于 `:60`）：

| 方法 | 路径 | 行号 | 用途 |
|---|---|---|---|
| POST | `/mcp/add` | `:163` | 新增远程 MCP / API 型 |
| POST | `/mcp/add-from-config` | `:236` | 新增容器 MCP（同步） |
| POST | `/mcp/add-from-config/stream` | `:317` | 新增容器 MCP（SSE 流式，先回容器信息再回结果） |
| PUT | `/mcp/update` | `:401` | 改配置 |
| DELETE | `/mcp/{mcp_id}` | `:462` | 删（软删 + 停容器） |
| DELETE | `/mcp/container/{container_id}` | `:499` | 只停容器 |
| GET | `/mcp/list` | `:558` | 列表（含 `authorization_token`/`custom_headers`） |
| GET | `/mcp/record/{mcp_id}` | `:602` | 单条 |
| GET | `/mcp/containers` | `:646` | 容器列表 |
| GET | `/mcp/container/{id}/logs` | `:693` | 容器日志 SSE |
| GET | `/mcp/healthcheck?mcp_id=` | `:768` | L2 健康检查 |
| POST | `/mcp/test-connection` | `:811` | L0 握手测试 |
| GET | `/mcp/port/check` / `/mcp/port/suggest` | `:847` / `:877` | 端口 |
| POST | `/mcp/enable` / `/mcp/disable` | `:904` / `:951` | 启停 |
| GET | `/mcp/tools?mcp_id=` | `:79` | **枚举该服务的所有工具** |
| POST | `/mcp/refresh-tools?mcp_id=` | `:123` | 重扫工具并落 `registry_json` |
| POST | `/mcp/upload-image[/stream]` | `:1067` / `:993` | 上传镜像 tar 启动 |

社区/市场族在 `prefix="/mcp-tools"`（`backend/apps/mcp_management_app.py:39`）：`community/list`、`community/mine`、`community/publish`、`community/update`、`community/delete`、`community/review/list|approve|reject`、`community/tags/stats`、`community/{market_id}/download`（`:47-525`）。

> 前端这些 URL 常量集中在 `frontend/services/api.ts:410-432`（`mcp.*`）与 `:497-509`（`mcpTools.*`）。

#### 路径 C：容器化注册（当你的 Server 是 stdio 型）

```bash
curl -X POST "http://127.0.0.1:5010/mcp/add-from-config" \
  -H "Authorization: Bearer <JWT>" -H "Content-Type: application/json" \
  -d '{
    "name": "data-asset-stdio",
    "port": 5031,
    "tags": ["data-asset"],
    "mcp_config": {
      "mcpServers": {
        "data-asset": {
          "command": "python",
          "args": ["-m", "data_asset_mcp.server"],
          "env": {"DATA_ASSET_API_BASE": "http://10.0.0.5:9000", "DATA_ASSET_API_TOKEN": "xxx"}
        }
      }
    }
  }'
```

Nexent 会用 `nexent/nexent-mcp` 镜像起容器，执行 `python -m mcp_proxy --host 0.0.0.0 --port 5031 --transport streamablehttp -- python -m data_asset_mcp.server`，并把容器 URL `http://<host>:5031/mcp` 落库。
返回结构（`remote_mcp_app.py:274-286`）：`service_name` / `mcp_url` / `container_id` / `container_name` / `host_port`。
⚠️ 端口先 `GET /mcp/port/suggest` 拿一个空闲的；`command` 不能是 `docker`。

### 步骤 5 · 把工具挂到 Agent 上

1. **智能体开发** → 选 Agent → **「选择智能体的工具」** 页签
2. 切到 **MCP** 分类（`toolManagement.mcp.title` = "配置MCP服务器"）
3. 找到 `data-asset`，展开工具列表，勾选 `asset_search` / `asset_get`，可配默认参数
4. **保存**（保存会触发 MCP 工具重扫 + `/tool/scan_tool` → `backend/apps/tool_config_app.py:128-135`）

### 步骤 6 · 验证（4 级，逐级加深）

| 级别 | 怎么做 | 通过标准 |
|---|---|---|
| ① Server 自证 | 步骤 2 的 `fastmcp.Client` 脚本 | 能 list_tools |
| ② Nexent 握手 | `POST /mcp/test-connection`（body: `server_url`/`authorization_token`/`custom_headers`，`model.py:2441-2445`） | 返回 `{"success": true}` |
| ③ Nexent 可达 + 列工具 | `GET /mcp/healthcheck?mcp_id=N` 或 `GET /mcp/tools?mcp_id=N` | `health_status="healthy"`；`tools` 里出现你的工具名 |
| ④ 端到端调用 | Agent 工具卡片上的「测试」按钮 → 填参 → 「执行测试」（前端调 `POST /tool/validate`，`tool_config_app.py:172-180` → `validate_tool_impl` → `_validate_mcp_tool_remote` `tool_configuration_service.py:1027-1069`） | 返回真实工具输出 |
| ⑤ 真 Agent 跑 | 在对话里问一句需要该工具的问题 | 观察流里出现 `<MCP_START>` 标记（`run_agent.py:415`）且工具被调用 |

### 步骤 7 · 常见坑速查

| 症状 | 原因 | 修法 |
|---|---|---|
| 503 `MCP connection timeout` | Nexent 在容器里，你写了 `127.0.0.1` | 换 `host.docker.internal` |
| 503 `MCP endpoint not found` | URL 后缀不对 | 必须 `/mcp` 或 `/sse`，`_detect_transport` 靠后缀 |
| 503 `MCP protocol or endpoint invalid` | 起的是普通 FastAPI，不是 MCP Server | 用 `mcp.run()`，别用 `uvicorn app` |
| 健康检查过、但工具列表空 | Server 工具 `meta.nexent_internal=True` 会被跳过（`tool_configuration_service.py:725-726`） | 去掉该 meta |
| 工具名被改写 | 名字里的非 `[A-Za-z0-9_]` 会被替换，且首字符不能是数字。扫描侧用的是第三方 `mcpadapt.smolagents_adapter._sanitize_function_name`（导入于 `backend/services/tool_configuration_service.py:60`，调用点 `:740`）；平台侧另有一份等价实现 `backend/mcp_service.py:198-204` | 用纯小写字母+下划线命名，别以数字开头 |
| 参数类型报错 | 只写了类型注解没写描述，Nexent 会补 `description="see tool description"` 并缺 `type` 时默认 `string`（`tool_configuration_service.py:733-738`） | 把 docstring 写全 |
| 重启 Nexent 后配置还在、容器没了 | 容器 `restart_policy=unless-stopped` 会自愈；但若被手动删，enable 时才有孤儿恢复逻辑（`remote_mcp_service.py:862-881`） | 重新 enable |

---

## 4. 工具从 MCP 暴露给 agent 的完整调用链

### 4.1 全链路图

```
[1] 前端注册 MCP
    McpConfigModal.tsx / AddMcpServiceCard.tsx
      → frontend/services/mcpService.ts:117 addMcpServer() / :449 addMcpConfig()
      → frontend/services/api.ts:412  POST {API_BASE}/mcp/add
─────────────────────────────────────────────────────────────
[2] 后端登记 + 健康检查
    backend/apps/remote_mcp_app.py:163  add_mcp_service_endpoint
      → services/remote_mcp_service.py:408 add_mcp_service()
          · check_mcp_name_exists()                       (:458)
          · _check_mcp_connectivity() → _mcp_protocol_health_check()  (:505, :109)
              建 fastmcp.Client → initialize → list_tools()
          · 工具名写进 registry_json["_toolNames"]        (:507)
      → database/remote_mcp_db.py:19 create_mcp_record()
          · pg_advisory_xact_lock + 配额检查              (:48-65)
          · INSERT nexent.mcp_record_t
─────────────────────────────────────────────────────────────
[3] 工具目录物化（把 MCP 工具变成平台"工具"）
    services/tool_configuration_service.py:787 update_tool_list()
      → :388 get_all_mcp_tools(tenant_id)
          → :672 get_tool_from_remote_mcp_server()   ← 每个已启用 MCP
              建 transport(SSE/StreamableHTTP) → list_tools()
              对每个 tool 造 ToolInfo(source="mcp", usage=<mcp_name>, origin_name=<原名>)
      → update_tool_table_from_scan_tool_list()  写 nexent.ag_tool_info_t
    （触发点：新建 MCP 后 / GET /tool/scan_tool / 保存 Agent / 手动刷新）
─────────────────────────────────────────────────────────────
[4] 用户在 Agent 上勾选
    前端「选择智能体的工具」→ MCP 页签 → 勾选 → 保存
      → POST/PUT /agent/... （backend/agents/create_agent_info.py）
      → nexent.ag_tool_instance_t（tool_id + agent_id + params）
─────────────────────────────────────────────────────────────
[5] 运行时：装配 mcp_host
    agents/create_agent_info.py:2539 filter_mcp_servers_and_tools()
        递归遍历 agent_config.tools + worker_agents，
        收集 source=="mcp" 且 usage 命中的 server URL（去重）
      → :2870-2899 组装 mcp_host[]：
          {"url": ..., "transport": "sse"|"streamable-http",
           "headers": {"Authorization": <token>, **custom_headers}}
        平台自带 outer-apis 额外加
          {"X-Tenant-ID": tenant_id, "X-Nexent-Internal-Token": TOKEN}
      → :2942 AgentRunInfo(mcp_host=..., mcp_request_timeout_seconds=MCP_REQUEST_TIMEOUT_SECONDS,
                           mcp_tool_timeout_seconds=RUNTIME_MCP_TOOL_TIMEOUT_SECONDS,
                           mcp_close_timeout_seconds=RUNTIME_MCP_CLOSE_TIMEOUT_SECONDS)
─────────────────────────────────────────────────────────────
[6] SDK 建会话 + 适配成 smolagents 工具
    sdk/nexent/core/agents/run_agent.py:381-428
        若 mcp_host 非空 → 打 <MCP_START> 标记 → with ManagedMCPToolCollection(...)
      → managed_mcp.py:112 _run_async_session()
          from mcpadapt.core import mcptools          (:72)
          from mcpadapt.smolagents_adapter import SmolAgentsAdapter  (:79)
          对每个 server_parameters 建连接（asyncio.wait_for 限 connect_timeout）
          对每个 mcp_tool：adapter.adapt(call_tool, mcp_tool)  → smolagents Tool
          ToolCollection(tools) 交给 NexentAgent
─────────────────────────────────────────────────────────────
[7] Agent 调用
    nexent_agent.py:543 create_mcp_tool(class_name)
        从 mcp_tool_collection.tools 按 name 找到 tool
        → apply_user_context_to_mcp_tool(tool, user_context)   (:552)
    nexent_agent.py:657-687 create_tool()：source=="mcp" → create_mcp_tool
    managed_mcp.py:159-206 _wrap_tools()：把每个 tool.forward 包一层
        走 ThreadManager 的 "model-tool-io" 泳道，带硬超时
    → session.call_tool(name, arguments)  → 落到远端 MCP Server
```

### 4.2 关键数据流对照

| 环节 | 数据形态 |
|---|---|
| MCP Server 暴露 | `tools/list` → `{name, description, inputSchema, meta}` |
| Nexent 存 DB | `ag_tool_info_t`：`name`(sanitized) / `origin_name`(原样) / `class_name`(=sanitized name) / `source='mcp'` / `usage=<mcp_name>` / `inputs`(JSON-Schema properties 字符串) / `output_type='object'` |
| Agent 配置 | `ToolConfig{class_name, name, description, inputs, output_type, params, source='mcp', usage=<mcp_name>}`（`create_agent_info.py:2167-2177`） |
| SDK 运行时 | `ManagedMCPToolCollection.tools` 里以 `name` 匹配（`nexent_agent.py:546-549`） |

⚠️ **契约要点**：`class_name` 必须等于 MCP Server 里的工具名（经 sanitize 后）。改名会导致 `ValueError(f"{class_name} not found in MCP server")`（`nexent_agent.py:550-551`）。

### 4.3 用户身份注入（工具侧鉴权）

平台**不做**工具级鉴权，只透传身份。约定字段（`sdk/nexent/core/agents/tool_user_context.py:21-28`，官方文档 `doc/docs/zh/user-guide/agent-development/agent-configuration.md:83-90`）：

| 字段名 | 含义 |
|---|---|
| `tenant_id` | 租户 ID |
| `tenant_name` | 租户名 |
| `user_id` | 用户 ID |
| `user_name` | 登录名（当前=邮箱） |
| `user_account` | 用户账号（邮箱） |
| `user_groups` | 用户组名列表 |

机制：只要你的工具 `inputSchema` 里声明了上述任一字段名，平台在调用前会
1. 把该字段**从模型可见的 schema 中删除**（`tool_user_context.py:87`）；
2. 覆盖 `forward` 签名，防模型用 `inspect.signature` 反推字段名（`:31-55`、`:117`）；
3. 调用时**丢弃模型传入的值**，只用会话解析出的可信值（`:96-102`）。

**这是给自研 MCP 工具做数据权限控制的标准姿势**——赛题里非常有用。

### 4.4 超时与取消语义（SDK 侧，务必理解）

| 参数 | 环境变量 | 默认 | 作用 |
|---|---|---|---|
| `mcp_request_timeout_seconds` | `MCP_REQUEST_TIMEOUT_SECONDS` | **10s** | 建连 + initialize 握手 |
| `mcp_tool_timeout_seconds` | `RUNTIME_MCP_TOOL_TIMEOUT_SECONDS` | **60s** | **单次工具调用硬上限** |
| `mcp_close_timeout_seconds` | `RUNTIME_MCP_CLOSE_TIMEOUT_SECONDS` | **5s** | 会话关闭等待 |

- 定义处：`backend/consts/const.py:371-385`（runtime 两项）、`:576-580`（request）；`.env` 样例 `deploy/env/.env.example:94-95`、`:188-190`。
- 工具调用超时后：打 warning 日志 `event=mcp_tool_timeout` → `request_close()` → `manager.cancel(..., mark_stuck_on_timeout=True)` → 抛 `MCPToolTimeoutError`，**会终止当前 Agent run**（`managed_mcp.py:186-203`）。
- 超时错误会向上传播替换最终消息：`CoreAgent` 捕获 `is_mcp_timeout_error`（`sdk/nexent/core/agents/core_agent.py:1649-1650`），`run_agent.py:60-101` 生成中英文超时文案并推到 `FINAL_ANSWER`。
- 归属线程池泳道：`mcp-session`（并发 4，`sdk/nexent/core/concurrency/helpers.py:37`）与 `model-tool-io`。

> 实践建议：自研工具若可能跑 >60s（大文件解析、视频抽帧），要么改 `RUNTIME_MCP_TOOL_TIMEOUT_SECONDS`，要么工具内部**异步化 + 返回 job_id**，让 Agent 轮询。比赛环境别把 60s 当成无限。

---

## 5. 仓库内置 / 推荐的现成 MCP Server 清单

### 5.1 仓库**内置**（开箱即用）

| 名称 | 类型 | 内容 | 可直接复用？ |
|---|---|---|---|
| `outer-apis`（平台托管） | SSE，租户级 | 把任意 OpenAPI 3.x 转成 MCP 工具，URL = `{NEXENT_MCP_SERVER}/mcp/{tenant_id}/sse`（`backend/utils/mcp_url_utils.py:8-15`） | ✅ **最推荐**。已有 REST API 就不用写 MCP Server |
| `nl2a`（内置工具服务） | 进程内 mount | 6 个工具（见下） | ⚠️ 平台自用，`meta.nexent_internal=True` 会被工具扫描跳过（`tool_configuration_service.py:725-726`） |
| 演示工具 `test_tool_name` | 进程内 | `backend/tool_collection/mcp/local_mcp_service.py:19-26`，仅 print + 返回 "success" | ✅ 可当接入模板 |
| `mcp-builder` 技能 | 官方 Skill（zip） | `deploy/docker/assets/official-skills-zip/mcp-builder.zip`；含 `SKILL.md`(9.3KB)、`reference/python_mcp_server.md`(25.8KB)、`reference/node_mcp_server.md`(29.5KB)、`reference/evaluation.md`(22.3KB)、`reference/mcp_best_practices.md`(7.6KB)、`scripts/connections.py`、`scripts/evaluation.py` | ✅✅ **写 MCP Server 直接照它做** |

`nl2a` 的 6 个工具（`backend/tool_collection/mcp/nl2agent_mcp_tools.py:19-51`，注册见 `nl2agent_mcp_service.py:27-62`）：

| 工具 | 职责 |
|---|---|
| `nl2a_search_installed_mcp_tools` | 关键词检索本租户已装 MCP 工具 |
| `nl2a_search_installed_resources` | 按能力需求检索已装 Local/MCP 工具与 Skill，返回 ≤12 候选 |
| `nl2a_search_uninstalled_resources` | 检索可安装的 Skill / MCP 仓库 |
| `nl2a_recommend_resources` | 对候选做推荐（NL2Agent 的自然语言建 Agent 用） |
| `nl2a_save_agent_draft_fields` | 保存 Agent 草稿字段 |
| `nl2a_wrapper` | NL2Agent 外层包装 |

对应的 SDK 侧配置由 `create_nl2agent_mcp_tool_configs()` 生成（`nl2agent_mcp_tools.py:612-701`），注意它们的 `usage` 全部是 `"outer-apis"`。

### 5.2 仓库**推荐**（文档里点名，可直接接）

来源：`doc/docs/zh/mcp-ecosystem/mcp-recommendations.md:9-30`、`use-cases.md`、`overview.md`。

| 名称 | 用途 | 链接 |
|---|---|---|
| GitHub MCP Server | 代码/Issue/PR | https://github.com/github/github-mcp-server |
| Qdrant MCP Vector Server | 向量语义检索 | https://github.com/qdrant/mcp-server-qdrant |
| Anthropic Reference Servers | fetch / git / filesystem 等通用件 | https://github.com/modelcontextprotocol/servers |
| AWS Labs MCP | 云资源 | https://github.com/awslabs/mcp |
| MCP Hub China | 中文精选 MCP 平台 | https://www.mcp-cn.com/ |
| ModelScope MCP 市场 | 1500+ 中文 MCP 服务 | https://modelscope.cn/mcp |
| 高德地图 / 必应中文搜索 / 12306 / 支付宝 / 飞常准 / SequentialThinking / ArXiv / Firecrawl | 具体能力件 | 见 `mcp-recommendations.md:21-30` 表格 |

### 5.3 "可直接复用"结论

1. **有 REST API 就别写 MCP Server** → 走 API→MCP（方式 C），零代码。
2. **要写 Server** → 抄 `mcp-builder` 技能的 Python 模板，按 §3 步骤 1-6 落地。
3. **要拼装能力** → 直接从 ModelScope 市场挑现成的（地图、搜索、爬虫、论文），Nexent 支持 SSE/Streamable HTTP 的都能挂。
4. **通用向量/知识检索** → Qdrant MCP 可直接接，和赛题的"数据资产检索"高度契合。

---

## 6. 针对"多模态异构数据资产认知智能体"赛题的自研 MCP 工具建议

设计原则（来自实读的机制约束）：
- Nexent 单次工具调用硬超时 **60s** → 重活必须异步化（返回 job_id + 轮询工具）。
- 工具名会被 sanitize → **只用小写字母+下划线**，动词开头，带域前缀。
- 需要数据权限 → 在 `inputSchema` 声明 `user_account` / `user_groups`，平台自动注入（§4.3）。
- 返回结构化 JSON，便于 Agent 二次加工。
- 拆"**目录/理解**"与"**重计算**"两类：前者同步快返回，后者异步任务化。

建议清单（15 个工具，按 5 组）：

### A 组 · 数据资产目录与血缘（同步，轻）

| # | 工具名 | 一句话职责 | 输入 | 输出 | 依赖 |
|---|---|---|---|---|---|
| 1 | `asset_search` | 跨模态统一检索数据资产目录 | `keyword:str, modality?:enum, tags?:list, owner?:str, limit:int=20, user_groups?:list` | `{items:[{asset_id,modality,uri,tags,owner,size,created_at}], total}` | 元数据中心（DataHub/自建 PG+ES）；`user_groups` 做行级过滤 |
| 2 | `asset_describe` | 返回单个资产的完整元数据与技术画像 | `asset_id:str` | `{asset_id, modality, schema?, resolution?, duration?, rows?, format, storage, checksum, tags}` | 元数据中心 + 对象存储 HEAD |
| 3 | `asset_lineage` | 查询资产的上游来源与下游消费血缘 | `asset_id:str, direction:enum=up/down/both, depth:int=3` | `{nodes:[...], edges:[{from,to,op}]}` | 血缘库（OpenLineage/Marquez 或自建图） |
| 4 | `asset_stats` | 按模态/时间/标签聚合统计资产分布 | `group_by:enum=modality/tag/owner/date, filters?:obj` | `{buckets:[{key,count,bytes}]}` | 元数据中心（SQL 聚合） |

### B 组 · 多模态理解（同步，中；注意 60s）

| # | 工具名 | 一句话职责 | 输入 | 输出 | 依赖 |
|---|---|---|---|---|---|
| 5 | `image_understand` | 对图像做描述/OCR/目标检测/属性抽取 | `asset_id?:str, image_url?:str, tasks:list=enum[caption,ocr,detect,classify,vqa], question?:str` | `{caption?, texts?:[{text,bbox}], objects?:[{label,score,bbox}], labels?}` | VLM（Qwen-VL / InternVL / GPT-4o）+ 可选 PaddleOCR；**大图先降采样** |
| 6 | `video_understand` | 视频抽帧+时序理解（摘要、事件、字幕） | `asset_id:str, start_sec?:float, end_sec?:float, tasks:list=enum[summary,event,asr,caption], fps?:float` | `{summary, events:[{start,end,label,desc}], transcript?}` | ffmpeg + VLM + ASR(Whisper)；**必须异步**（见 #13） |
| 7 | `audio_transcribe` | 语音转写 + 说话人分离 + 关键词 | `asset_id:str, language?:str, diarize?:bool` | `{text, segments:[{start,end,speaker,text}], keywords:[]}` | ASR（Whisper/funASR）+ pyannote |
| 8 | `doc_parse` | 解析 PDF/Word/Excel/PPT 为结构化文本与表格 | `asset_id?:str, file_url?:str, mode:enum=text/tables/layout, pages?:str` | `{markdown, tables:[{page,cells}], meta:{pages,author}}` | MinerU / pdfplumber / python-docx / openpyxl |
| 9 | `table_profile` | 对结构化表做 schema 推断、质量画像与样例 | `asset_id:str, sample_rows:int=20` | `{columns:[{name,type,null_ratio,distinct,min,max}], pk_candidates:[], sample:[...]}` | DuckDB / pandas / SQLAlchemy |
| 10 | `modality_align` | 跨模态对齐：给图片找相关文本/给文本找相关图 | `anchor_type:enum=image/text/audio, anchor_id?:str, anchor_text?:str, top_k:int=10` | `{matches:[{asset_id, modality, score, reason}]}` | 联合向量库（CLIP/Chinese-CLIP + BGE + 统一 embedding 空间） |

### C 组 · 认知与推理（同步，中）

| # | 工具名 | 一句话职责 | 输入 | 输出 | 依赖 |
|---|---|---|---|---|---|
| 11 | `asset_qa` | 针对指定资产集合的多模态问答（RAG） | `question:str, asset_ids?:list, modality_filter?:list, top_k:int=8` | `{answer, citations:[{asset_id, span, score}]}` | 向量库 + 重排 + VLM/LLM；**必须带 citations**，比赛评分看可解释性 |
| 12 | `asset_summarize` | 对一批异构资产生成统一摘要卡片 | `asset_ids:list, style?:enum=brief/detailed, language?:str` | `{cards:[{asset_id, title, summary, key_points:[]}], overall}` | 复用 #5-#10 的结果缓存 |

### D 组 · 异步任务（解决 60s 超时）

| # | 工具名 | 一句话职责 | 输入 | 输出 | 依赖 |
|---|---|---|---|---|---|
| 13 | `job_submit` | 提交长耗时多模态处理任务，立即返回 job_id | `task_type:enum[ingest,embed,transcode,index,understand], payload:obj, priority?:int` | `{job_id, status:"queued", eta_seconds}` | Celery/RQ/Arq + Redis；**这是绕开 60s 的关键件** |
| 14 | `job_status` | 查询任务进度与结果引用 | `job_id:str, wait_seconds?:int=0` | `{job_id, status:enum[queued,running,succeeded,failed], progress:0-1, result_ref?, error?}` | 同 #13 |

### E 组 · 资产写入与治理（有副作用的，务必加权限校验）

| # | 工具名 | 一句话职责 | 输入 | 输出 | 依赖 |
|---|---|---|---|---|---|
| 15 | `asset_register` | 登记新数据资产（含自动打标与模态识别） | `uri:str, modality?:str, tags?:list, owner?:str, description?:str, user_account?:str, user_groups?:list` | `{asset_id, modality, tags, dedup_hit?:bool}` | 元数据中心写接口 + 去重（checksum）；**用 `user_account`/`user_groups` 做写权限校验** |

#### 落地顺序建议

1. **第一周**：`asset_search` + `asset_describe` + `asset_register` → 打通"目录"闭环，这三件最能立刻演示。
2. **第二周**：`doc_parse` + `image_understand` + `table_profile` → 异构模态"看得懂"。
3. **第三周**：`job_submit`/`job_status` + `video_understand`/`audio_transcribe` → 长任务不被 60s 卡死。
4. **第四周**：`modality_align` + `asset_qa` + `asset_summarize` + `asset_lineage` → "认知"层亮点。

> 若时间紧，**优先把 `asset_search` / `doc_parse` / `image_understand` / `job_submit`+`job_status` 做扎实**，这 5 个就能支撑一个完整 Demo。

---

## 7. 认证与安全

### 7.1 MCP 工具的凭证怎么管

**存储**：`mcp_record_t.authorization_token`（`VARCHAR(500)`，明文）+ `custom_headers`（JSON，明文）——`db_models.py:1467-1476`、DDL `v1_merged_migrations.sql:892-901`、`v2.2_merged_migrations.sql:233-234`。

**下发路径**（三处，都是拼进 HTTP 头）：

| 场景 | 位置 | 头 |
|---|---|---|
| Agent 运行时 | `backend/agents/create_agent_info.py:2890-2898` | `Authorization: <token>` + 全部 `custom_headers` |
| 工具扫描/枚举 | `backend/services/tool_configuration_service.py:690-709` | 同上；且 URL 含 `/mcp/` 时**强制注入** `X-Tenant-ID` + `X-Nexent-Internal-Token: TOKEN` |
| 健康检查/工具测试 | `backend/services/remote_mcp_service.py:97-101`、`:1560-1564` | 同上 |

**容器模式下的凭证**：`authorization_token` 会被塞进容器**环境变量** `authorization_token`（`remote_mcp_service.py:595-598`、`:900-902`），并在启动后用于就绪探针（`docker_client.py:346-349`）。`mcpServers.<x>.env` 里的任何键值也会进容器环境。

**内网互信令牌**：平台自带 MCP（`outer-apis`）用 `TOKEN`（`const.py:467`，读 `TOKEN` 环境变量，默认空串）+ `X-Tenant-ID` 走内部信任；`backend/mcp_service.py:459-461` 的校验逻辑是：

```python
if not authorization and (requested_tenant != tenant_id or not TOKEN or internal_token != TOKEN):
    → 403
```

即：**没有 `Authorization` 头时，必须同时满足「`X-Tenant-ID` == 路径里的 tenant_id」且「`X-Nexent-Internal-Token` == TOKEN」**；带 `Authorization` 时走 JWT 并校验解析出的 tenant 与路径 tenant 一致（`mcp_service.py:462-472`，调 `utils.auth_utils.get_current_user_id`）。

**审计**：`mcp_add` / `mcp_update` / `mcp_delete` / `mcp_enable` / `mcp_disable` 写入 `audit.security` logger；代码注释明确 **`authorization_token`/`custom_headers`/`container_config`/`config_json`/`registry_json` 永不入日志**，只有 `server_url` 入审计（因为改 URL 本身就是外带信道）——`backend/apps/remote_mcp_app.py:198-207`、`:266-272`、`:431-437`。实现在 `backend/services/audit_service.py:103-129`（best-effort，从不抛异常）。

**给自研工具的建议（重要）**：
1. **不要把上游 API Key 放在 Nexent 的 `authorization_token` 里当"业务凭证"**——它是明文入库、明文注入容器的。让它只做"网关鉴权"。
2. 真正的业务凭证（数据库密码、模型 API Key）放 **MCP Server 自己的环境变量/密钥管理**，Server 内部去取。
3. 敏感工具用 `user_account`/`user_groups` 做**服务端**数据权限校验（平台只透传不鉴权，见 `tool_context.py:1-12` 的显式声明："The platform itself performs no authorization for tool calls"）。
4. 用 `group_ids` + `ingroup_permission='PRIVATE'` 控制谁能看到该 MCP 服务（`remote_mcp_service.py:1170-1194`）。

### 7.2 有没有沙箱

**结论：MCP 工具本身没有强沙箱。** 分三层看：

| 层 | 隔离情况 | 证据 |
|---|---|---|
| **远程 MCP（URL 方式）** | **无任何沙箱**。Nexent 只是 HTTP 客户端，工具在对方进程里以对方权限执行 | `run_agent.py:289-351`、`managed_mcp.py:112-157` |
| **容器化 MCP** | **进程级隔离（Docker/K8s），但不是安全沙箱**。`restart_policy=unless-stopped`、`stdin_open=True`，配置里**没有** `--read-only`、`cap_drop`、`no-new-privileges`、内存/CPU limit、非 root user 等加固项 | `sdk/nexent/container/docker_client.py:378-396`；K8s Pod spec 中 `k8s_client.py:284-291` 未设 `security_context` |
| **Agent 代码执行沙箱（另一套）** | 存在，等级由 `sandbox_config` 决定（local/docker，`create_agent_info.py:2907-2940`），镜像见 `deploy/images/dockerfiles/sandbox/Dockerfile` | **但这套沙箱管的是 Agent 生成的代码，不是 MCP 工具** |

**关键机制 `_nexent_execute_on_host`**：`source` 为 `local`/`builtin`/**`mcp`** 的工具会被显式打上"在宿主进程执行"的标记（`sdk/nexent/core/agents/nexent_agent.py:682-686`），而沙箱 env 会把它桥接回宿主（`sdk/nexent/core/agents/sandbox.py:1014-1016` 的 `_is_host_tool`，桥接实现 `_ToolBridge` 于 `:1019+`，用 `secrets.token_urlsafe(32)` + `hmac.compare_digest` 校验 `Bearer` 令牌）。

> 换句话说：**MCP 工具调用永远跑在 Nexent 主进程（或远端/容器），不会因为 Agent 开了 docker 沙箱就被关进去。** 想在比赛里讲"安全"，得自己在 MCP Server 内部做隔离（子进程 + seccomp、容器内降权、只读挂载、eBPF 审计等）。

**平台自身的加固点（可以用来讲）**：
- `/mcp/{tenant_id}/...` 路由双向校验（JWT 或内部令牌 + tenant 一致性），`backend/mcp_service.py:444-492`。
- 每租户 MCP 服务数上限 1000，且用 Postgres advisory lock 防并发超卖（`remote_mcp_db.py:44-65`）。
- 工具参数值域校验（`_validate_tool_param_ranges`，`tool_configuration_service.py:550`）。
- 工具名 sanitize，防止注入非法标识符（`backend/mcp_service.py:198-204`）。
- 容器命令白名单：拒绝 `command == "docker"`（`remote_mcp_service.py:592-593`）——但这**不足以**阻止 `command="sh"` 之类，属于弱校验。
- 用户身份字段对模型不可见 + 防签名反射（`tool_user_context.py:31-55`、`:117`）。
- 软删除 + 删除时解绑 Agent 草稿（`remote_mcp_service.py:1084-1100`）。

### 7.3 风险清单（自评用）

| 风险 | 说明 | 缓解 |
|---|---|---|
| 凭证明文入库 | `authorization_token` / `custom_headers` / 容器 `env` 全明文 | 只放网关令牌；业务密钥留 Server 侧 |
| 容器逃逸面 | MCP 容器无加固，且 `nexent-config` 挂了宿主 `docker.sock` | 给 MCP 容器加 `read_only`/`cap_drop`/非 root/资源限制；限制谁能创建容器型 MCP |
| 无工具级鉴权 | 平台只透传身份 | 工具内用 `user_account`/`user_groups` 自校验 |
| SSRF | `server_url` 由用户填，后端会去连 | 出网白名单/内网段黑名单（仓库内**未见**此类校验）【未验证：未读到 URL 黑名单实现】 |
| 提示注入 → 工具滥用 | 工具返回内容进模型上下文 | 工具输出做长度截断 + 结构化；高危工具加确认步骤 |
| 60s 硬超时 | 长任务被砍 | 异步任务化（#13/#14） |

---

## 附：一页速查

```
MCP 协议服务   :  backend/mcp_service.py      监听 0.0.0.0:5011（SSE）+ 5015（管理 API）
管理/业务 API  :  /mcp/*       → nexent-config :5010   backend/apps/remote_mcp_app.py
                /mcp-tools/*   → nexent-config :5010   backend/apps/mcp_management_app.py
                /tool/*        → nexent-config :5010   backend/apps/tool_config_app.py
传输           :  /sse → sse ;  /mcp → streamable-http ;  其它 → streamable-http
stdio          :  只能靠容器 + mcp-proxy 桥接，镜像 nexent/nexent-mcp:latest
表             :  mcp_record_t / mcp_community_record_t / mcp_market_record_t
                ag_tool_info_t（工具目录） / ag_tool_instance_t（Agent↔工具绑定）
健康检查        :  POST /mcp/test-connection（L0 握手） / GET /mcp/healthcheck（L1+L2 列表+落库）
工具枚举        :  GET /mcp/tools?mcp_id=   ;  POST /mcp/refresh-tools?mcp_id=
超时            :  握手 10s(MCP_REQUEST_TIMEOUT_SECONDS) / 工具 60s(RUNTIME_MCP_TOOL_TIMEOUT_SECONDS) / 关闭 5s
配额            :  每租户 1000 个 MCP 服务(MAX_MCP_SERVICES_PER_TENANT)
身份注入        :  tenant_id/tenant_name/user_id/user_name/user_account/user_groups
写 Server       :  照 deploy/docker/assets/official-skills-zip/mcp-builder.zip 里的 SKILL.md
```

---

### 未验证项汇总

1. **fastmcp 的精确版本**：仓库未在可见文件里锁 `fastmcp` 版本；`MCP_PROXY_VERSION` 是构建参数（`deploy/images/dockerfiles/mcp/Dockerfile:49-59`），未读到 CI 传入值。因此 `mcp.run(transport="http")` vs `"streamable-http"` 的参数名需现场确认。
2. **`MCP_PROXY_VERSION` 的实际取值**：`.github/workflows/auto-build-mcp-dev.yml` 存在但本次未逐行读。
3. **`server_url` 是否有内网/SSRF 黑名单**：未在 `remote_mcp_service.py`/`remote_mcp_app.py` 读到相关校验；`consts/` 下也未读到。判定为"未见"，不等于"确定没有"。
4. **K8s 模式下 Pod 的完整 securityContext / resources**：只确认了 `k8s_client.py:284-291` 的容器定义片段，未整文件读完。
5. **`mcp_service.py` 中 `nexent_mcp.run(transport="sse", port=5011)`（:542）与前面 `uvicorn.run(TenantMCPRouter(), port=5011)`（:540）的关系**：两行都在 `run_mcp_server_with_management()` 里串行执行，`uvicorn.run` 会阻塞，故 `:542` 实际不可达（**疑似死代码**）——但这对租户级 `/mcp/{tenant_id}/sse` 路由无影响（由 `TenantMCPRouter` 提供）。标为 **【未验证·疑似死代码】**。
