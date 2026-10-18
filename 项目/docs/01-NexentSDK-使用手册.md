# Nexent SDK 使用手册（纯 SDK · 无 Docker · 可运行）

> **基线**：`第三方/nexent` 源码副本，平台版本 `VERSION = v2.7.0`（`第三方/nexent/VERSION`）。
> **本文所有结论均在 Python 3.11.15 + 本仓库源码上实机跑通后写成**，验证记录见 §9。
> **引用约定**：`文件:行号` 相对仓库根 `第三方/nexent/`。例：`sdk/nexent/core/utils/observer.py:137`。
> **标注约定**：凡我无法从源码确认的，一律标 `【未验证】`。凡实机验证失败的，写在 §9「实地踩坑」。

---

## 0. 结论速览（先看这一段）

| 问题 | 结论 |
|---|---|
| 能不能不启动 Docker / 前后端就跑起来？ | **能**。模型 + Agent + 工具 + 流式观测 100% 在 SDK 内闭环，只依赖一个 OpenAI 兼容 HTTP 端点。已实机验证（§9 探针 A/B/D）。 |
| 装哪个包？ | **必须** `pip install -e sdk`（或 `uv pip install -e sdk`）。**不要** `pip install nexent`——PyPI 上的 `nexent` 与本仓库源码不是同一份代码（§1.1）。 |
| Python 版本 | **恰好 3.11**（`>=3.11,<3.12`）。3.12/3.13 会被 pip 直接拒绝（§1.2）。 |
| 最小可运行代码 | §2，两段，可直接复制运行。 |
| 工具注册形态 | 4 条真实分派路径：`local` / `mcp` / `langchain` / `builtin`（`nexent_agent.py:657-674`）。"Python 函数"要落到 `local` 或直连 `CoreAgent`（§4）。 |
| 记忆能不能纯 SDK 用？ | **协议层能，持久化不能**。`MemoryService` 是纯门面，必须由调用方注入 `backend_store` / `backend_search` 钩子（`memory/service.py:80-104`）。仓库自带的 Postgres+ES 实现全在 `backend/`，不在 SDK（§5、§7）。 |
| 直连 `CoreAgent` 能 `run()` 吗？ | **不能**，除非显式传 `context_runtime`，否则抛 `RuntimeError: CoreAgent requires a context runtime from the agent factory`（`core_agent.py:593` + `context_runtime/contracts.py:29`）。**官方文档 `doc/docs/zh/sdk/basic-usage.md:60-73` 的示例是错的**（§2.3）。 |

---

## 1. 安装

### 1.1 包名与版本：为什么不能从 PyPI 装

| 项 | 本仓库源码 | PyPI `nexent` |
|---|---|---|
| 包名 | `nexent`（`sdk/pyproject.toml:6`） | `nexent` |
| 包版本 | `0.1.2`（`sdk/pyproject.toml:7`） | 最新 `1.8.1` |
| Python 要求 | `>=3.11,<3.12`（`sdk/pyproject.toml:12`） | `>=3.10` |
| 关键依赖 | `smolagents[mcp]==1.23.0`、`mcp>=1.24.0,<1.30`、`fastmcp>=2.14.2,<3.0`、**无 mem0ai**（`sdk/pyproject.toml:34,42,43`） | `smolagents[mcp]==1.23.0`、`mcp<1.23,>=1.19.0`、`fastmcp==2.12.0`、**`mem0ai>=0.1.117`** |

PyPI 版本仍然带 `mem0ai`，而本仓库的内存体系已经把 Mem0 换成了 `nexent.memory` 门面 + 后端钩子：
`sdk/nexent/memory/memory_service.py:1-28` 明确写着 "Legacy import bridge removed by the new Memory architecture"，旧 API `add_memory_in_levels` / `search_memory_in_levels` / `clear_memory` 现在**直接抛 `LegacyMemoryApiRemoved`**。

> **结论**：`pip install nexent` 装到的是旧架构，`from nexent.memory.service import MemoryService` 之类的新 API 很可能对不上。**只能用源码。**

### 1.2 Python 版本

`requires-python = ">=3.11,<3.12"`（`sdk/pyproject.toml:12`）——这是**上界也封死**的写法。实测：

- 3.13 / 3.12 下 `pip install -e sdk` 会被直接拒绝（`Requires-Python >=3.11,<3.12`）。
- 绕过（`--ignore-requires-python`）会让 pip 进入**指数级回溯**：实测在本机跑了 3 分半、CPU 197s 仍未完成解析，最终放弃（见 §9 踩坑 P1）。
- 唯一顺畅路径是 `uv`（它会读到 `[tool.uv] override-dependencies`，`sdk/pyproject.toml:65-74`）。

### 1.3 推荐安装命令（实机验证通过）

```bash
# 0) 拿到 3.11 解释器（本机原本没有 3.11；uv 可一键下载独立 CPython）
uv python install 3.11

# 1) 建虚拟环境
uv venv --python 3.11 .venv-nexent311

# 2) 以可编辑模式装本仓库 SDK（注意是 sdk/ 目录，不是仓库根）
uv pip install -e "第三方/nexent/sdk" --python .venv-nexent311/Scripts/python.exe
```

Windows PowerShell 等价写法：

```powershell
uv python install 3.11
uv venv --python 3.11 .venv-nexent311
uv pip install -e "第三方\nexent\sdk" --python .\.venv-nexent311\Scripts\python.exe
```

只用 pip 也可以，但**必须**是 3.11：

```bash
py -3.11 -m venv .venv-nexent
.venv-nexent/Scripts/python.exe -m pip install -e "第三方/nexent/sdk"
```

仓库官方文档给出的方式是 `uv pip install -e .`（在 `sdk/` 目录下）以及 `uv pip install -e ".[dev]"`，
见 `doc/docs/zh/developer-guide/environment-setup.md:141-161`。**该文档与 pyproject 一致，可以信**；
但同一份文档 §"通用要求" 写的是 "Python 3.11+"（`environment-setup.md:11`），
这与 `pyproject.toml:12` 的 `<3.12` 上界**冲突**——以 pyproject 为准。

### 1.4 依赖坑（逐条）

1. **`[tool.uv] override-dependencies` 只对 uv 生效**（`sdk/pyproject.toml:65-74`）：
   它把 `pymongo / psycopg2-binary / redis / neo4j / chromadb / pinecone-client / weaviate-client`
   全部覆盖为 `999.0.0`（即"禁止安装"）。用 pip 装时这些覆盖不生效，
   某些 extra 会把这些重依赖拖进来。
2. **`pymssql` 要编译**：`sdk/pyproject.toml:53`。Windows 上必须与本机 Python 版本严格匹配 wheel，
   否则需要在有 MSVC 的环境编译。3.13 上大概率没有 wheel。
3. **`elasticsearch==8.17.2` 是硬钉版本**（`sdk/pyproject.toml:22`），与自建 ES 集群版本需对齐。
4. **`openai>=1.69.0` 无上界**（`sdk/pyproject.toml:26`）。本次 uv 实际解析到 **openai 3.28.0**，
   实测兼容（探针 A/B/D 全通过），但这是"上游大版本漂移"的隐患，生产建议自己钉版本。
5. **`pypandoc==1.17`**（`sdk/pyproject.toml:61`）只装 Python 绑定，**运行期转换还需要系统 pandoc 可执行文件**。
   纯 agent 场景不会走到，但 `nexent.data_process` 会。
6. **SDK 原则上不读环境变量**（`第三方/nexent/AGENTS.md` 明确规定），
   但**有 4 处例外**：`exa_search_tool.py:68`、`tavily_search_tool.py:67`、
   `linkup_search_tool.py:65`、`knowledge_base_search_tool.py:139` 都在 `__init__` 里
   `os.getenv("DATA_PROCESS_SERVICE")`。不设这个变量不会报错，只是这几个搜索工具的图片代理字段为 `None`。
7. **`nexent.core.tools` 的 `__init__` 是"全量导入"**（`sdk/nexent/core/tools/__init__.py:1-32`，
   含 `sql_tools` 子包、`ext_components.aidp`）。因为 `nexent_agent.py:26` 有一句
   `from ..tools import *`，**只要 `import nexent.core.agents.run_agent`，这批工具的运行时依赖就全都要装齐**。
   实测 `import nexent.core.agents.run_agent` 之后 `sys.modules` 里已经出现的重依赖：

   ```
   boto3  pymysql  psycopg2  pymssql  paramiko  exa_py  tavily  smolagents
   ```

   **未**被拉入（都是懒加载，只有真用时才 import）：
   `docker`、`kubernetes`、`elasticsearch`、`openpyxl`、`python-pptx`、`pypdf`、`ijson`、
   `ebooklib`、`pypandoc`、`fastmcp`、`mcp`、`mcpadapt`。

   即便如此，**pip 解析阶段仍然要求这些包全部可安装**（它们在 `dependencies` 里），
   做不到"只装一部分"。这是本 SDK 最重的安装负担。


---

## 2. 最小可运行示例

### 2.1 形态 A：`agent_run()`（**推荐**，官方高层入口）

这是 `backend` 实际走的那条路（`run_agent.py:354-484` 的 `_agent_run_thread`），
也是"能拿到完整流式事件"的唯一高层入口。

完整可复制代码 —— `minimal_agent_run.py`：

```python
"""最小可运行 Nexent Agent：高层 agent_run() + 1 个本地工具。"""
import asyncio
import json
import os
import tempfile
from threading import Event                                   # AgentRunInfo.stop_event 需要 threading.Event

from nexent.core.agents.agent_model import (
    AgentConfig,        # sdk/nexent/core/agents/agent_model.py:264
    AgentRunInfo,       # sdk/nexent/core/agents/agent_model.py:385
    ModelConfig,        # sdk/nexent/core/agents/agent_model.py:22
    ToolConfig,         # sdk/nexent/core/agents/agent_model.py:133
)
from nexent.core.agents.run_agent import (
    agent_run,                            # sdk/nexent/core/agents/run_agent.py:487  异步生成器
    shutdown_default_agent_thread_manager # sdk/nexent/core/agents/run_agent.py:175 收尾线程池
)
from nexent.core.utils.observer import MessageObserver   # sdk/nexent/core/utils/observer.py:132

WORKSPACE = os.path.join(tempfile.gettempdir(), "nexent-ws")   # 工具只允许在这个目录内读写
os.makedirs(WORKSPACE, exist_ok=True)

# 换成你自己的端点：Ollama 用 http://127.0.0.1:11434/v1
BASE_URL = "http://127.0.0.1:11434/v1"
API_KEY = "ollama"
MODEL = "qwen2.5:7b"


async def main() -> None:
    # ---- 1) 观察者：所有流式事件都从这里出（lang 决定部分提示语/文案语言）----
    observer = MessageObserver(lang="zh")            # observer.py:137  __init__(lang="zh", enable_nl2a_wrapper=False)

    # ---- 2) 模型配置 ----
    model_config = ModelConfig(
        cite_name="local-llm",       # 逻辑别名。AgentConfig.model_name 靠它做匹配（nexent_agent.py:301-310）
        model_name=MODEL,            # 真正发给 /chat/completions 的 model 字段（openai_llm.py:187 的 model_id）
        url=BASE_URL,                # OpenAI 兼容 base_url，不含 /chat/completions（openai_llm.py:257）
        api_key=API_KEY,             # Ollama 不校验；给非空串最省事
        temperature=0.2,             # 默认 0.1（agent_model.py:27）；OpenAIModel 自身默认 0.2（openai_llm.py:187）
        top_p=0.95,                  # agent_model.py:28
        ssl_verify=True,             # http 端点无所谓（agent_model.py:45）
        max_output_tokens=None,      # 单次补全上限；None=用服务端默认（agent_model.py:58）
        timeout_seconds=None,        # 不设则 OpenAIModel 内部 read timeout 兜底 60s（openai_llm.py:293）
    )

    # ---- 3) Agent 配置 ----
    agent_config = AgentConfig(
        name="demo_agent",           # 必填（agent_model.py:279）
        description="minimal agent", # 必填（agent_model.py:281）
        model_name="local-llm",      # 必填，必须等于某个 ModelConfig.cite_name（agent_model.py:290）
        tools=[                      # 必填，可为空列表（agent_model.py:283）
            ToolConfig(
                class_name="CreateFileTool",  # local 源：在 nexent_agent 模块 globals 里查这个名字（nexent_agent.py:352）
                name="create_file",           # 必填，即使类型是 Optional（agent_model.py:135，见 §9 踩坑 P3）
                source="local",               # local | mcp | langchain | builtin（nexent_agent.py:665-674）
                params={"init_path": WORKSPACE},  # 透传给工具构造函数的关键字参数
            )
        ],
        max_steps=5,                 # 默认 15（agent_model.py:284）；注意 max_steps 只在 AgentConfig 里
        prompt_templates=None,       # None → 用 smolagents 自带模板（nexent_agent.py:775）
        provide_run_summary=False,   # agent_model.py:299
    )

    # ---- 4) 一次运行的全部输入 ----
    run_info = AgentRunInfo(
        query="用 create_file 写一个 hello.txt，内容写 Hello，然后告诉我结果",
        model_config_list=[model_config],   # 必填（agent_model.py:388）
        observer=observer,                  # 必填（agent_model.py:389）
        agent_config=agent_config,          # 必填（agent_model.py:390）
        stop_event=Event(),                 # 必填，可外部 set() 实现优雅中断（agent_model.py:407）
        mcp_host=None,                      # 传 URL 列表即启用 MCP，见 §4.3（agent_model.py:391）
        history=None,                       # List[AgentHistory]，见 §2.4（agent_model.py:406）
    )

    # ---- 5) 跑，并消费事件流 ----
    async for raw in agent_run(run_info):     # 每一条都是 JSON 字符串
        msg = json.loads(raw)
        mtype = msg.get("type")
        if mtype == "final_answer":
            print("\nFINAL:", msg.get("content"))
        elif mtype == "tool":
            print("\nTOOL:", msg.get("tool_name"), msg.get("tool_arguments"))
        elif mtype == "execution_logs":
            print("\nOBS:", str(msg.get("content"))[:200])
        else:
            print(".", end="", flush=True)

    await shutdown_default_agent_thread_manager()   # 关掉 SDK 兜底线程池，进程才能干净退出


if __name__ == "__main__":
    asyncio.run(main())
```

**每个参数的含义与来源**（`AgentRunInfo` 常用字段，全部来自 `agent_model.py:385-507`）：

| 字段 | 必填 | 行号 | 说明 |
|---|---|---|---|
| `query` | ✅ | 387 | 用户问题；`CoreAgent.run` 会在其后追加当前时间标记（`core_agent.py:1779-1784`） |
| `model_config_list` | ✅ | 388 | `List[ModelConfig]`，按 `cite_name` 查找 |
| `observer` | ✅ | 389 | `MessageObserver` 实例；模型与 Agent **必须共用同一个** |
| `agent_config` | ✅ | 390 | `AgentConfig` |
| `stop_event` | ✅ | 407 | `threading.Event`；`set()` 后当前步结束即停（`core_agent.py:1974`） |
| `mcp_host` | ❌ | 391 | `list[str \| dict]`，见 §4.3 |
| `mcp_request_timeout_seconds` | ❌ | 399 | MCP **连接+握手**超时，默认回落 10.0（`run_agent.py:48-57`） |
| `mcp_tool_timeout_seconds` | ❌ | 413 | 单次 MCP 工具调用硬超时，默认 60.0 |
| `history` | ❌ | 406 | `List[AgentHistory]`，见 §2.4 |
| `conversation_id` / `user_id` / `tenant_id` | ❌ | 438/439/497 | 传递给工具做租户/用户级鉴权与隔离 |
| `user_context` | ❌ | 440 | 隐藏字段（`exclude=True`），只从可信会话注入，模型看不到 |
| `sandbox_config` | ❌ | 473 | `None` → 用本地 `LocalPythonExecutor`，**不启 Docker** |

### 2.2 形态 B：直连 `CoreAgent`（想完全掌控工具对象时用）

```python
"""直连 CoreAgent：适合注册自定义 Python 函数工具。"""
from smolagents.tools import tool          # smolagents 1.23.0 提供；签名 tool(fn) -> Tool

from nexent.core.agents.context import (   # sdk/nexent/core/agents/context/__init__.py:28-30
    ContextManager, ContextManagerConfig, ManagedContextRuntime,
)
from nexent.core.agents.core_agent import CoreAgent        # core_agent.py:519
from nexent.core.models.openai_llm import OpenAIModel      # openai_llm.py:180
from nexent.core.utils.observer import MessageObserver


@tool
def add(a: int, b: int) -> int:
    """Add two integers.

    Args:
        a: first addend
        b: second addend
    """
    return a + b


observer = MessageObserver(lang="en")

model = OpenAIModel(
    observer=observer,                 # 必填位置参数（openai_llm.py:187）
    model_id="qwen2.5:7b",             # 透传给 smolagents.OpenAIServerModel
    api_key="ollama",
    api_base="http://127.0.0.1:11434/v1",
    temperature=0.2, top_p=0.95,
)

# ⚠️ 关键：不传 context_runtime 会在 run() 时抛 RuntimeError（core_agent.py:593）
context_runtime = ManagedContextRuntime(
    ContextManager(ContextManagerConfig(), max_steps=5),   # manager.py:38 / config.py:12
    items=[],                                              # runtime.py:27-33
)

agent = CoreAgent(
    observer=observer,                 # CoreAgent 的第一个位置参数（core_agent.py:546）
    tools=[add],                       # 透传给 smolagents CodeAgent（core_agent.py:574）
    model=model,
    name="calc_agent",
    description="calculator",
    max_steps=5,
    context_runtime=context_runtime,   # SDK 私有 kwarg，在 core_agent.py:573 被 pop
)

for step in agent.run("what is 2+3?", stream=True, reset=True):   # core_agent.py:1750
    print(type(step).__name__, getattr(step, "output", None))
```

**实测输出**（把端点指向一个 mock 服务）：

```
ActionOutput None
ActionStep None
ActionOutput None
ActionStep None
FinalAnswerStep <final_answer>DONE</final_answer>
```

即 `CoreAgent.run(stream=True)` 产出的 5 个对象里，**只有最后一个 `FinalAnswerStep` 带最终答案**
（`ActionStep.output` 在默认配置下不会被写成最终答案，见 §2.5）。

`CoreAgent.__init__` 的 SDK 私有 kwargs（都在 `core_agent.py:553-573` 被 `pop`，其余原样交给 smolagents）：

| kwarg | 行号 | 默认 |
|---|---|---|
| `display_name` | 553 | `None` |
| `enable_planning` | 554 | `False`（开启后建 `PlanRepo`，需要 redis 才持久化） |
| `redis_client` | 555 | `None`（仅 planning 用） |
| `conversation_id` / `user_id` | 556-557 | `None` |
| `workspace_path` | 558 | `None` |
| `output_protocol` | 559 | `"code_action"`；可选 `"final_envelope"`（`:561-562` 校验） |
| `enable_protocol_repair_retry` | 560 | `False`（**影响最终答案形态，见 §2.5**） |
| `enable_clarification` | 566 | `False` |
| `context_runtime` | 573 | `None` → `UnconfiguredContextRuntime()`（会抛错） |

其余 `**kwargs` 直通 smolagents 1.23.0 `CodeAgent`：实测完整签名
`CodeAgent(tools, model, prompt_templates=None, additional_authorized_imports=None, planning_interval=None, executor=None, executor_type='local', executor_kwargs=None, max_print_outputs_length=None, stream_outputs=False, use_structured_outputs_internally=False, code_block_tags=None, **kwargs)`，
其中 `**kwargs` 落到 `MultiStepAgent`：`instructions / max_steps=20 / add_base_tools=False / verbosity_level / managed_agents / step_callbacks / name / description / provide_run_summary / final_answer_checks / return_full_result / logger`。

> `CoreAgent` 会**强制移除** `final_answer` 工具（`core_agent.py:520-542` 的 `_setup_tools` + `:575-579`），
> 所以 `tool.name == "final_answer"` 的自定义工具会被静默丢弃。

### 2.3 ⚠️ 官方文档示例在当前版本是错的

`doc/docs/zh/sdk/basic-usage.md:60-73` 写的是：

```python
agent = CoreAgent(observer=observer, tools=[search_tool, kb_tool], model=model, name="my_agent", max_steps=5)
agent.run("你的问题")
```

实测（§9 探针 B 负向对照）：

```
BARE_RUNTIME_ERROR: RuntimeError | CoreAgent requires a context runtime from the agent factory
```

原因：`core_agent.py:593` `self.context_runtime = context_runtime or UnconfiguredContextRuntime()`，
而 `UnconfiguredContextRuntime.prepare_run()` 直接 `raise RuntimeError(_UNCONFIGURED_RUNTIME_ERROR)`
（`context_runtime/contracts.py:185-194`，错误串在 `:29`）；`CoreAgent.run` 第一件事就是调
`self.context_runtime.prepare_run(...)`（`core_agent.py:1807-1810`）。

**修正**：按 §2.2 显式传 `context_runtime`，或干脆走 §2.1 的 `agent_run()`（它内部会建好）。

### 2.4 多轮对话（历史）

```python
from nexent.core.agents.agent_model import AgentHistory      # agent_model.py:360

run_info = AgentRunInfo(
    # ... 其余同上 ...
    history=[
        AgentHistory(role="user", content="你好"),           # role: "user" | "assistant"
        AgentHistory(role="assistant", content="你好，有什么可以帮你？"),
    ],
)
```

`AgentHistory` 会被 `NexentAgent.add_history_to_agent()` 翻译成 smolagents 的
`TaskStep` / `ActionStep` 注入 memory（`nexent_agent.py:1104-1131`）；`history=None` 直接返回（`:1111-1112`）。

### 2.5 最终答案长什么样（**实测**）

`ProcessType.FINAL_ANSWER` 的 `content` 在**默认配置下会保留 `<final_answer>` 信封**：

```
FINAL: <final_answer>2+3=5</final_answer>
```

原因链（都是源码事实）：

- 默认 `enable_protocol_repair_retry=False` + `output_protocol="code_action"`
  ⇒ `legacy_code_action = True`（`core_agent.py:1297-1300`）。
- 该分支走 `parse_code_blobs(model_output)`；解析不出代码块时进入
  `_legacy_parse_fallback`，**把整段模型输出原样当成最终答案**（`core_agent.py:1444-1447` → `:1013-1020`）。
- 严格协议分支（`enable_protocol_repair_retry=True`）才会走
  `classify_model_output`，此时 `<final_answer>…</final_answer>` 会被剥壳
  （`output_protocol.py:392-397` → `:350-369`，赋给 `ExplicitFinalAnswer.answer`，
  再在 `core_agent.py:1482-1493` 作为 `output` 产出）。

**实践建议**：拿最终答案时自己做一次剥壳：

```python
import re
answer = re.sub(r"^\s*<final_answer>([\s\S]*)</final_answer>\s*$", r"\1", raw_final).strip()
```

另外 `MessageObserver.get_final_answer()`（`observer.py:795-805`）也能从队列里捞出 `final_answer`，
但它**只在当前 `message_query` 缓冲区里找**，且 `get_cached_message()`（`:789-793`）会清空缓冲区——
两者混用会互相"抢"消息。

---

## 3. 接本地模型与国内 API

`url` 一律填 **base_url**（OpenAI SDK 会自己拼 `/chat/completions`）。
依据：`openai_llm.py:257` `self.api_base_url = kwargs.get("api_base")`，最终交给 `smolagents.OpenAIServerModel`。

### 3.1 Ollama（本地，OpenAI 兼容端点）

```python
ModelConfig(
    cite_name="ollama",
    model_name="qwen2.5:7b",              # 必须是 ollama list 里的模型名
    url="http://127.0.0.1:11434/v1",      # 注意要带 /v1
    api_key="ollama",                     # Ollama 忽略该值，但不能为空（OpenAI SDK 会校验非空）
    temperature=0.2, top_p=0.95,
    ssl_verify=True,                      # http 明文，无所谓
    max_output_tokens=2048,               # 本地小模型建议显式封顶，避免跑飞
    timeout_seconds=120.0,                # 本地推理慢，给足
)
```

直连 `OpenAIModel` 时对应：

```python
OpenAIModel(observer=observer, model_id="qwen2.5:7b",
            api_key="ollama", api_base="http://127.0.0.1:11434/v1",
            ssl_verify=True, timeout_seconds=120.0, max_output_tokens=2048)
```

要点：

- Ollama 的 `/v1` 分支**不返回 reasoning 字段**，所以不要设 `enable_thinking=True`。
- `OpenAIModel` 内部强制 `client_kwargs["max_retries"] = 0`（`openai_llm.py:303`），
  重试由 SDK 的 `retry_config` 统一管（`:291`），别指望 OpenAI SDK 自己重试。
- 若显式传 `client_kwargs={"http_client": ...}` 则 SDK 不再自建（`openai_llm.py:304-318`）。

### 3.2 DeepSeek

```python
ModelConfig(
    cite_name="deepseek",
    model_name="deepseek-chat",              # 或 deepseek-reasoner
    url="https://api.deepseek.com/v1",
    api_key=os.environ["DEEPSEEK_API_KEY"],  # SDK 不读环境变量，由你的代码读进来传参
    temperature=0.2, top_p=0.95,
    model_factory="openai",                  # 见下方说明
    enable_thinking=False,
    timeout_seconds=120.0,
)
```

- SDK 有 DeepSeek 专用走线识别：`openai_llm.py:1175-1176` 按 hostname `api.deepseek.com` 判定
  `provider_id = "deepseek"`，随后（`:1185-1190`）把 reasoning 映射为
  `effort -> reasoning_effort`、`toggle -> thinking_object`、`budget -> None`。
- 也就是说 **DeepSeek 的 thinking 开关走嵌套 `thinking` 对象**，不是顶层 `enable_thinking`。
  该映射只有在 `reasoning_capability` 带上 `source == "models_dev"` 时才会命中
  （`:1178`）；否则走 `:1209-1225` 的通用回落，此时 `provider_id == "deepseek"` 仍然会把
  `toggle` 设成 `thinking_object`（`:1221-1222`）。
- 对未知/未确认的 provider，SDK **只推断 `reasoning_effort`**，绝不猜 budget/toggle 字段
  （`:1210-1214` 的注释与实现）。

### 3.3 智谱 GLM

```python
ModelConfig(
    cite_name="glm",
    model_name="glm-4-plus",
    url="https://open.bigmodel.cn/api/paas/v4",
    api_key=os.environ["ZHIPU_API_KEY"],
    temperature=0.2, top_p=0.95,
)
```

【未验证】GLM 不在 `_reasoning_wire_profile` 的显式识别名单里，reasoning 参数会走
`reasoning_effort` 通用回落。若网关不接受该字段，会命中 `_is_reasoning_parameter_error`
并被包装成 `ReasoningConfigurationError`（`openai_llm.py:1126-1129`）——此时把
`enable_thinking=False`、`reasoning_effort=None` 即可绕开。

### 3.4 其他可选

| 提供方 | `url` | 备注 |
|---|---|---|
| 硅基流动 SiliconFlow | `https://api.siliconflow.cn/v1` | 仓库文档示例用的就是它（`basic-usage.md:98`） |
| 阿里 DashScope / 百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | SDK 按 hostname `*.aliyuncs.com` 判定 `dashscope`（`openai_llm.py:1173-1174`），reasoning 映射为 `enable_thinking` + `thinking_budget`（`:1179-1184`） |
| ModelEngine | 自填 | 设 `model_factory="modelengine"`；SDK 据此决定 `flatten_messages_as_text` 行为（`openai_llm.py:266-267`，注释见 `:236-239`） |

### 3.5 采样参数与推理参数的 3 个通用开关

| 参数 | 位置 | 作用 |
|---|---|---|
| `temperature` / `top_p` | `ModelConfig:27,28`；`OpenAIModel:187` | 常规采样 |
| `enable_thinking` | `ModelConfig:29`（`OpenAIModel` 无此显式参数，走 `extra_body`/reasoning 通道） | 提供方 thinking 开关 |
| `reasoning_effort` | `ModelConfig:33`，取值 `auto/none/minimal/low/medium/high/xhigh/max` | 只有 `enable_thinking=True` 且值 `!= "auto"` 时才会传给模型（`nexent_agent.py:329-334`） |
| `extra_body` | `ModelConfig:49-57` | **原样 merge 进每次 `chat.completions.create` 的 body**，是接国产网关"私有开关"的万能后门。例：`{"chat_template_kwargs": {"enable_thinking": False}}` |
| `max_output_tokens` | `ModelConfig:58` | 首选名；`max_tokens` 是废弃别名（`:68-76`，校验器在 `:117-130` 会做回填，且 `>=32768` 时按旧"上下文窗口"语义处理成 4096） |

---

## 4. 工具注册的三种形态

分派入口是 `NexentAgent.create_tool()`，按 `ToolConfig.source` 四选一
（`nexent_agent.py:657-674`）：

```python
if   source == "local":     tool_obj = self.create_local_tool(tool_config)      # :349
elif source == "mcp":       tool_obj = self.create_mcp_tool(class_name)         # :543
elif source == "langchain": tool_obj = self.create_langchain_tool(tool_config)  # :539
elif source == "builtin":   tool_obj = self.create_builtin_tool(tool_config)    # :554
else: raise ValueError(f"unsupported tool source: {source}")
```

注册后还会统一打标记 `_nexent_execute_on_host = True`（`:682-686`），
让沙箱把工具调用路由回宿主进程。

### 4.1 形态一：本地工具类（`source="local"`）

**写法**：

```python
ToolConfig(
    class_name="CreateFileTool",   # 必须是 nexent_agent 模块 globals 里存在的名字
    name="create_file",            # 必填（agent_model.py:135）
    source="local",
    params={"init_path": "/tmp/ws"},   # 直接 ** 展开给工具构造函数
)
```

**查找机制**：`nexent_agent.py:352` `tool_class = globals().get(class_name)`。
这个 `globals()` 是 **`nexent_agent.py` 模块自己的命名空间**，由 `nexent_agent.py:26`
的 `from ..tools import *` 填充。因此可用名字 = `sdk/nexent/core/tools/__init__.py:1-32`
导出的那批 + `__all__`（`:34-70`，共 35 个），例如：

`CreateFileTool` / `ReadFileTool` / `DeleteFileTool` / `ListDirectoryTool` / `MoveItemTool` /
`CreateDirectoryTool` / `DeleteDirectoryTool` / `TerminalTool` / `ExaSearchTool` / `TavilySearchTool` /
`LinkupSearchTool` / `KnowledgeBaseSearchTool` / `StoreMemoryTool` / `SearchMemoryTool` /
`AnalyzeTextFileTool` / `AnalyzeImageTool` / `AnalyzeAudioTool` / `AnalyzeVideoTool` /
`ParallelExecutorTool` / `DownloadFromS3Tool` / `UploadToS3Tool` / `CreatePlanTool` /
`UpdatePlanStepTool` / `MySqlTool` / `PostgreSqlTool` / `MsSqlTool` / `ExaSearchTool` / …

**构造签名（实测抓取，含行号）**：

| 工具 | 构造参数（`sdk/nexent/core/tools/...`） |
|---|---|
| `CreateFileTool` | `init_path: str = Field(default="/mnt/nexent")`, `observer` (exclude) — `create_file_tool.py:52-54` |
| `ReadFileTool` | 同 `CreateFileTool` 形态 — `read_file_tool.py` |
| `TerminalTool` | `init_path="~"`, `observer`, `ssh_host="nexent-openssh-server"`, `ssh_port=22`, `ssh_user` **(必填)**, `password` **(必填)** — `terminal_tool.py:77` |
| `ExaSearchTool` | `exa_api_key` **(必填)**, `observer`, `max_results=3 (1..100)`, `image_filter=True` — `exa_search_tool.py:53` |
| `TavilySearchTool` | `tavily_api_key` **(必填)**, `observer`, `max_results=3`, `image_filter=True` — `tavily_search_tool.py:52` |
| `KnowledgeBaseSearchTool` | `top_k=3`, `index_names: List[str]` **(必填)**, `search_mode="hybrid"`, `rerank=False`, `rerank_model_name=""`；`observer/embedding_model/rerank_model/vdb_core` 均为 `exclude=True`，**不能通过 `params` 传** — `knowledge_base_search_tool.py:73` |
| `StoreMemoryTool` | 见 §5.4（参数全部走 `metadata`，不走 `params`） |
| `SearchMemoryTool` | 同上 |

> ⚠️ `TerminalTool` 强制走 SSH（默认连 `nexent-openssh-server` 容器），
> **纯 SDK 场景不可用**。想在本地跑 shell，请自己包一个工具（形态二）。

**特殊分支**：`create_local_tool` 对若干工具有硬编码特判，会**改写你传的参数**：

- `KnowledgeBaseSearchTool`：剔除 `vdb_core / embedding_model / observer / rerank_model / display_name_to_index_map`，
  改从 `tool_config.metadata` 取（`nexent_agent.py:356-388`）。
- `DifySearchTool` / `DataMateSearchTool`：剔 `observer / rerank_model`（`:389-396`）。
- `RAGFlowSearchTool`：额外剔 `rerank / rerank_model_name`（`:397-407`）。
- `AnalyzeTextFileTool` / `AnalyzeImageTool` / `AnalyzeAudioTool` / `AnalyzeVideoTool`：
  `observer / llm_model|vlm_model / storage_client / data_process_service_url / validate_url_access` **全部从 metadata 取**（`:414-434`）。
- `StoreMemoryTool` / `SearchMemoryTool`：完全不看 `params`，见 §5.4（`:435-472`）。
- `DownloadFromS3Tool` / `UploadToS3Tool`：参数名固定，从 metadata 取 `minio_client / user_id / tenant_id`（`:513-521`）。
- 其他工具：`tool_class(**params)`，事后若实例有 `observer` 属性则覆盖为本次 observer（`:522-525`）。

### 4.2 形态二：Python 函数（两种落地方式）

#### 方式 2a（推荐）：直连 `CoreAgent`，用 smolagents 的 `@tool`

`smolagents.tools.tool` 的签名就是 `tool(tool_function: Callable) -> Tool`（实测），
返回的是 `Tool` 实例，可直接塞进 `CoreAgent(tools=[...])`。完整代码见 §2.2。

这个方式**绕过了 `ToolConfig`/`nexent_agent.globals()` 的限制**，是最干净的"自定义函数即工具"路径。
实测：`add(a=2, b=3)` 被模型成功调用，`ActionStep.observations == 'Execution logs:\n5\n'`（§9 探针 B）。

#### 方式 2b：注入模块 globals 后走 `ToolConfig(source="local")`

因为查找用的就是 `nexent_agent` 模块的 `globals()`（`nexent_agent.py:352`），
你可以把自己的工具类挂进去，从而复用 `agent_run` 这条高层路径：

```python
import nexent.core.agents.nexent_agent as na
from smolagents.tools import Tool

class MyEchoTool(Tool):
    name = "my_echo"
    description = "Echo text back"
    inputs = {"text": {"type": "string", "description": "text to echo"}}
    output_type = "string"

    def __init__(self, prefix: str = "echo"):
        super().__init__()
        self.prefix = prefix

    def forward(self, text: str) -> str:
        return f"{self.prefix}:{text}"

na.MyEchoTool = MyEchoTool          # 注入；名字必须与 ToolConfig.class_name 一致

ToolConfig(class_name="MyEchoTool", name="my_echo", source="local", params={"prefix": "hi"})
```

> 这依赖 `globals()` 的实现细节，属于"可用但脆"的做法——升级 SDK 时要回归。

#### 方式 2c：LangChain 工具对象

```python
ToolConfig(
    class_name="MyLangchainTool",   # 仅作标识；实际对象从 metadata 取
    name="my_lc_tool",
    source="langchain",
    metadata=<你的 langchain BaseTool 实例>,   # 注意：metadata 直接就是那个对象，不是 dict
)
```

依据：`nexent_agent.py:539-541`

```python
def create_langchain_tool(self, tool_config: ToolConfig):
    tool_obj = tool_config.metadata
    return Tool.from_langchain(tool_obj)
```

> ⚠️ `ToolConfig.metadata` 的声明类型是 `Optional[Dict[str, Any]]`（`agent_model.py:143`），
> 这里却被当成任意对象用。pydantic 默认不做严格校验，实测可行；但类型标注与实际用法不符。
> 【未验证】我没有实机跑过这条路径（需要额外的 langchain 依赖）。

### 4.3 形态三：MCP 工具（**已实机验证**）

**两步**：① 在 `AgentRunInfo.mcp_host` 给服务地址；② 在 `AgentConfig.tools` 里用
`source="mcp"` 按**工具名**登记。

```python
AgentConfig(
    name="mcp_agent", description="mcp agent", model_name="mock", max_steps=5,
    tools=[ToolConfig(class_name="mcp_echo", name="mcp_echo", source="mcp")],
    #      ^^^^^^^^^^^^^^^^^^^^^^^ 必须与 MCP 服务端注册的工具名完全一致
)

AgentRunInfo(
    # ...
    mcp_host=["http://127.0.0.1:8931/mcp"],   # 字符串：按 URL 后缀自动判 transport
    mcp_request_timeout_seconds=20.0,          # 连接+握手超时
    mcp_tool_timeout_seconds=60.0,             # 单次调用超时（默认值）
)
```

**transport 自动判定**（`run_agent.py:269-286`）：URL 以 `/sse` 结尾 → `sse`；
以 `/mcp` 结尾 → `streamable-http`；**其他一律回落 `streamable-http`**。

**dict 形式**（需要鉴权/代理时，`run_agent.py:304-351`）：

```python
mcp_host=[{
    "url": "https://mcp.example.com/mcp",
    "transport": "streamable-http",      # 显式指定时必须是 "sse" 或 "streamable-http"，否则 ValueError
    "authorization": "Bearer xxx",       # 与 headers 同时给时，authorization 覆盖 Authorization 键
    "headers": {"X-Tenant": "t1"},
    "bypass_proxy": True,                # 忽略 HTTP(S)_PROXY 环境变量（内部建 trust_env=False 的 httpx client）
    # "httpx_client_factory": <callable>,  # 也可自定义；必须可调用，否则 ValueError
}]
```

**运行时行为**（`run_agent.py:414-460`）：
`mcp_host` 非空时会用 `ManagedMCPToolCollection` 起一个 MCP session（`managed_mcp.py:19-34`），
通过 `mcpadapt.smolagents_adapter.SmolAgentsAdapter(structured_output=False)` 把 MCP tool 适配成
smolagents Tool（`managed_mcp.py:76-81`），并且**每个工具的 `forward` 都被包成带超时的托管任务**
（`managed_mcp.py:159-206`，超时抛 `MCPToolTimeoutError`）。
同时会先往 observer 推一条 `AGENT_NEW_RUN`，内容为字面量 `<MCP_START>`（`run_agent.py:415`）。

**实测结果（探针 D）**：用 `fastmcp` 起了一个 `mcp_echo` 工具，
`execution_logs` 里出现 `echo:ping`，最终答案正常返回，消息类型序列：

```
agent_new_run, agent_new_run, step_count, model_attempt_control, model_output_thinking ×2,
model_attempt_control, parse, tool, execution_logs, token_count, step_count, ... , final_answer
```

> 登记 MCP 工具时 `ToolConfig.name` 也必须给（pydantic 必填，`agent_model.py:135`）。
> `create_mcp_tool` 只按 `class_name` 去 `mcp_tool_collection.tools` 里找（`nexent_agent.py:543-552`），
> 找不到就 `ValueError(f"{class_name} not found in MCP server")`。

### 4.4 形态四：`builtin`（内置/系统工具，普通用户一般用不到）

`source="builtin"` 走 `create_builtin_tool`（`nexent_agent.py:554-655`），
只认这几个**硬编码类名**：`RunSkillScriptTool`、`ReadSkillMdTool`、`ReadSkillConfigTool`、
`DownloadFromS3Tool`、`UploadToS3Tool`、`CreatePlanTool`、`UpdatePlanStepTool`、
`CreateScheduledTaskProposalTool`。其余一律 `ValueError(f"Unknown builtin tool: {class_name}")`。
这些工具的参数大量来自 `metadata` 与 `NexentAgent` 的运行时字段（tenant/user/workspace），
**独立 SDK 场景基本无法自给自足**。

---

## 5. 记忆（Memory）

### 5.1 总体架构：SDK 只做协议，不做存储

`sdk/nexent/memory/service.py:1-26` 的模块 docstring 把边界写得非常清楚：

> - The SDK layer **must not** depend on PostgreSQL, Elasticsearch or any other database/storage implementation.
> - All persistence and vector retrieval for internal memory is delegated to the backend layer.
> - This facade is therefore intentionally thin: it validates inputs against the access policy,
>   computes idempotency keys, optionally generates embeddings via an injected embedding model,
>   builds the protocol-level request/result models, and dispatches the actual I/O to a
>   configurable backend hook (`backend_store` / `backend_search`).

**所以"打开记忆"= 给 `MemoryService` 注入两个异步钩子。**

### 5.2 四级隔离怎么用

代码里实际有**两套**"分层"概念，别混淆：

**(a) `MemoryLayer` 枚举只有 3 个值**（`sdk/nexent/memory/models.py:10-11`）：

```python
class MemoryLayer(str, Enum):
    TENANT = "tenant"; USER = "user"; AGENT = "agent"
```

**(b) 检索结果按 4 个桶组织**（`MemorySearchContext`，`models.py:169-191`）：

| 桶 | 字段 | 来源层 | 检索方式 |
|---|---|---|---|
| 租户长期 | `tenant_long_term` | `TENANT` | **全量注入，不做向量检索**（`policy.py:164`） |
| 用户长期 | `user_long_term` | `USER` | **全量注入**（`policy.py:164`） |
| Agent 短期 | `agent_short_term` | `AGENT` | **向量相似检索**（`policy.py:167`） |
| 外部 | `external` | 外部 provider（Mem0/A800…） | 走 `external_search_hook` |

**真正的"四级隔离"是隔离键四元组**，落在每一条 `MemoryRecord` 上
（`models.py:24-27`）：`tenant_id` / `user_id` / `agent_id` / `conversation_id`。

**读写权限矩阵**（`policy.py:39-48`，方法在 `:50-100`）：

| 角色 | 可写层 | 可写类型 |
|---|---|---|
| Agent（`store_memory` 工具） | **仅 `AGENT`** | **仅 `SHORT_TERM`** |
| Agent（`search_memory` 工具） | 读 `TENANT` + `USER` + `AGENT` | `SHORT_TERM` + `LONG_TERM` |
| Dreaming（深度睡眠提升） | 仅 `USER` | 仅 `LONG_TERM` |

越权写入会抛 `PermissionError`（`service.py:138-141`）。**实测**：

```
POLICY_BLOCK: OK -> Agent cannot write to layer=MemoryLayer.USER, type=MemoryType.LONG_TERM
```

**存储/检索策略**（`policy.py:103-224`）：

- `MAX_STORES_PER_RUN = 3`（`policy.py:107`），与 `StoreMemoryTool.max_stores_per_run = 3`（`store_memory_tool.py:138`）一致。
- `VECTOR_INDEXED_LAYERS = {AGENT}`、`PG_ONLY_LAYERS = {TENANT, USER}`（`policy.py:112,115`）。
- `DEFAULT_TOP_K = 5`、`MAX_TOP_K = 100`、`DEFAULT_THRESHOLD = 0.65`（`policy.py:155-161`）。
- 默认检索层 = `[TENANT, USER, AGENT]`（`policy.py:100`）。

### 5.3 依赖什么后端（向量库？嵌入模型？）

| 能力 | 需要什么 | 依据 |
|---|---|---|
| Agent 短期记忆检索 | **Elasticsearch kNN + PostgreSQL** | `backend/services/memory_retrieval_service.py:9` 注释 + `:27-32` 导入 `database.memory_long_term_db / memory_record_db / memory_retrieval_hit_db` 与 `services.memory_index_service` |
| Tenant/User 长期记忆 | **PostgreSQL**（全量加载，不用向量） | `policy.py:115,164` |
| 查询/内容向量化 | **任意 OpenAI 兼容 `/v1/embeddings`** | `memory/embedding_model.py:80-118` |
| Pinecone/Weaviate/Chroma/Mem0 | **不需要**（被 `[tool.uv] override-dependencies` 显式禁装，`sdk/pyproject.toml:65-74`） | — |

**嵌入模型客户端**（`memory/embedding_model.py:80-118`）：

```python
from nexent.memory import get_embedding_client, EmbeddingModelInfo

embedding_model = get_embedding_client(
    model_name="bge-m3",            # 模型名
    dimension=1024,                 # 维度，参与 ES index 命名
    base_url="http://127.0.0.1:11434/v1",   # 任意 OpenAI 兼容 embeddings 端点
    api_key="ollama",
    model_repo=None,                # 给了就拼成 "{repo}/{name}" 发给 API（SiliconFlow 需要）
    ssl_verify=True,
)
```

`EmbeddingModelInfo.get_index_name()` 决定 ES 索引名（`embedding_model.py:57-72`）：

```
mem_{model_repo}_{model_name}_{dimension}
mem_{model_name}_{dimension}          # model_repo 缺省时
```

> **换嵌入模型 = 自动换索引**（旧索引自然退休）——这是 SPEC 的设计意图，
> 见 `embedding_model.py:63-65` 注释。

**ES 连接配置载体**是 `MemoryConfig`（`models.py:148-166`），字段含
`es_host / es_port / es_api_key | es_username+es_password / embed_model_*`，
以及 `get_index_name()`（`:162-166`）。注意：**SDK 里没有任何代码消费这个类**——
它是留给后端的配置 DTO。

### 5.4 怎么把记忆接到 Agent 上（实测可用）

`StoreMemoryTool` / `SearchMemoryTool` 的参数**全部从 `ToolConfig.metadata` 注入，
完全不读 `params`**（`nexent_agent.py:435-472`）：

```python
from nexent.core.agents.agent_model import ToolConfig
from nexent.memory import MemoryService, get_embedding_client   # 你的 svc / embedding

memory_svc = MemoryService(
    embedding_model=get_embedding_client("bge-m3", 1024, "http://127.0.0.1:11434/v1", "ollama"),
    embedding_model_info=None,          # 可选，仅用于推导 ES 索引名
    backend_store=my_store_hook,        # async (payload: dict) -> dict
    backend_search=my_search_hook,      # async (payload: dict) -> list[dict]
)

common_meta = {
    "tenant_id": "t1", "user_id": "u1", "agent_id": "a1", "conversation_id": 42,
    "memory_service": memory_svc,
    "memory_config": {},                # 可选
    "embedding_configured": True,       # False 会让工具直接返回"embedding 未配置"
    "memory_user_config": None,         # 可选，MemoryUserConfig
}

tools = [
    ToolConfig(class_name="StoreMemoryTool",  name="store_memory",  source="local", params={}, metadata=common_meta),
    ToolConfig(class_name="SearchMemoryTool", name="search_memory", source="local", params={},
               metadata={**common_meta, "memory_context_service": None}),  # 可选，见下
]
```

**钩子契约**（`service.py:49-51, 80-104`）：

```python
BackendStoreHook  = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]     # 至少回 {"memory_id": ...}
BackendSearchHook = Callable[[Dict[str, Any]], Awaitable[List[Dict[str, Any]]]]
```

`backend_store` 收到的 payload 结构见 `service.py:289-315`
（`memory_id / tenant_id / user_id / agent_id / conversation_id / layer / memory_type / content / idempotency_key / embedding`）。

**⚠️ 实测坑（真 bug）**：`backend_search` 返回的每一项里，`memory_id` **必须是 int**。

- `MemorySearchResult.memory_id: Optional[int]`（`models.py:75`）
- 但 `service.py:337` 原样透传：`memory_id=item.get("memory_id", item.get("id", ""))`

若你返回 UUID 字符串（`MemoryRecord.memory_id` 默认就是 uuid4 字符串，`models.py:23`），
会炸在：

```
pydantic_core.ValidationError: 1 validation error for MemorySearchResult
memory_id
  Input should be a valid integer, unable to parse string as an integer
```

**且即使不返回该键也会炸**（回落成 `""`，同样过不了 int 校验）。
纯 SDK 用户请在钩子里自己映射成整数（真实后端返回的是 PG 自增主键，天然是 int）。

**未注入 `MemoryService` 时的降级行为**（实测，两种都**不会抛异常**，只是返回提示串）：

```
STORE_TOOL_UNWIRED:  Failed to store memory: MemoryService is not configured. Pass a MemoryService instance when constructing StoreMemoryTool.
SEARCH_TOOL_UNWIRED: Memory search failed. Continuing without memory results.
```

依据：`store_memory_tool.py:186-200`、`search_memory_tool.py` 的失败分支。

**另外**：`MemoryService.search_memory` 有 3 个静默短路（`service.py:232-246`）——
① `layers` 里没有 `AGENT` → 返回空列表；② 没有 embedding 且无法算 → 返回空列表；
③ `backend_search is None` → 返回空列表。**所以"搜不到"不一定代表没数据。**

**`memory_context_service`**：`SearchMemoryTool` 的另一个可选注入点
（`search_memory_tool.py:104-114`）。给了它会把检索委托给 `MemoryContextService.build_context`，
从而启用 Phase 4 检索流水线（normalize → score fusion → temporal decay → MMR → token budget，
`PipelineConfig` 见 `models.py:225-235`）。**类在 backend，不在 SDK**：
`backend/services/memory_context_service.py:88,124`。

### 5.5 外部记忆 provider

`nexent.memory.providers` 提供 `BaseMemoryProvider` / `SearchableMemoryProvider` /
`IngestibleMemoryProvider` / `ExternalHttpProvider` 和一个进程级注册表
（`providers/registry.py:19-122`，`get_provider_registry()` 在 `:102-111`）。

```python
from nexent.memory import get_provider_registry, ExternalHttpProvider

get_provider_registry().register(ExternalHttpProvider(...))
```

【未验证】`ExternalHttpProvider` 的构造参数与协议细节我没有实机跑。

### 5.6 旧 API 已删除（别照着老博客写）

`memory/memory_service.py` 现在整个文件只有 3 个抛异常的桩
（`:11-28`）：`add_memory_in_levels` / `search_memory_in_levels` / `clear_memory`
一律抛 `LegacyMemoryApiRemoved`。旧的多级拼接（`user_agent` 之类）也不再支持——
`store_memory_tool.py:52-56` 明确写了 "a sub-agent cannot write to a parent agent's memory and vice versa"。

---

## 6. 流式输出与可观测性

### 6.1 消息通道：`MessageObserver`

三个入口（`sdk/nexent/core/utils/observer.py`）：

| 方法 | 行号 | 用途 |
|---|---|---|
| `MessageObserver(lang="zh", enable_nl2a_wrapper=False)` | 137 | 构造；`lang` 影响部分文案与模板选择 |
| `observer.get_cached_message()` | 789-793 | **取走并清空**当前缓冲区的 `list[str]`（每项是 JSON 字符串） |
| `observer.get_final_answer()` | 795-805 | 在当前缓冲区里找 `type == "final_answer"` 的 `content`（**不清空**） |
| `observer.add_message(agent_name, process_type, content, **kwargs)` | 577-658 | 手工推事件；`kwargs` 支持 `tool_name / tool_arguments / tool_call_id / agent_id / agent_name / error_code / retryable` |
| `observer.add_model_new_token(new_token)` | 371-399 | 模型流式 token 入口，内部按 `<think>` 切分思考/正文 |
| `observer.add_model_reasoning_content(reasoning_content)` | 781-787 | 推理内容 → `MODEL_OUTPUT_DEEP_THINKING` |
| `observer.add_subagent_start(...)` / `add_subagent_end(...)` | 669-719 / 721-779 | 子 Agent 嵌套边界 |
| `observer.tool_call_context(tool_call_id)` | 660-667 | 把后续输出归属到某次工具调用 |

**事件 JSON 结构**（`Message.to_json()`，`observer.py:827-854`）：

```json
{
  "type": "tool",
  "content": "",
  "tool_name": "create_file",
  "tool_arguments": {"file_path": "hello.txt", "content": "..."},
  "tool_call_id": "3f1c...",
  "agent_id": "...",
  "agent_name": "demo_agent",
  "depth": 1,
  "invocation_id": "...",
  "attempt_id": "..."
}
```

只有非空字段才会出现（`depth == 0` 不输出，`:847-848`）。`metadata` 里的键会被平铺进顶层（`:853`）。

### 6.2 `ProcessType` 全集（`observer.py:39-76`）

| 值 | 含义 |
|---|---|
| `model_output_thinking` | 模型流式正文 |
| `model_output_deep_thinking` | 模型流式思考（`<think>` 内） |
| `model_output_code` | 模型流式代码 |
| `model_attempt_control` | 隐藏的 begin/rollback/commit 边界（metadata 里带 `phase`/`attempt_id`/`attempt`） |
| `step_count` | 当前 ReAct 步号 |
| `parse` | 代码解析结果 |
| `execution_logs` | 代码执行输出（工具调用的"观察"也在这里） |
| `agent_new_run` | 一次运行的开始（含 MCP 时的 `<MCP_START>`） |
| `agent_finish` | 子 Agent 结束 |
| `human_interaction` | 终端提问表单 |
| `final_answer` | 最终答案 |
| `error` / `warning` / `other` | 错误 / 可恢复问题 / 其他 |
| `token_count` | 每步 token 统计 |
| `history_summary` | 新建的上下文压缩检查点 |
| `search_content` / `picture_web` / `card` | 搜索结果 / 联网图片 / 卡片 |
| `tool` | 工具调用开始（含 `tool_name`、`tool_arguments`、`tool_call_id`） |
| `nl2a` / `nl2a_state` | NL2Agent 结构化输出 / 工作流状态 |
| `skill_artifact` / `file_artifact` | 技能产物 / 已上传文件 |
| `memory_search` | 记忆检索状态 |
| `max_steps_reached` | 达到步数上限 |
| `verification` | 分层 ReAct 自校验状态 |
| `plan` / `plan_step_update` | 规划 JSON / 单步状态更新 |
| `automation_proposal` | 定时任务提案卡片 |
| `subagent_start` / `subagent_end` | 子 Agent 嵌套边界 |

**实测消息序列（探针 A，本地工具调用）**：

```
agent_new_run, step_count, model_attempt_control, model_output_thinking ×4,
model_attempt_control, parse, tool, card, execution_logs, token_count,
step_count, model_attempt_control, model_output_thinking ×2, model_attempt_control,
token_count, final_answer
```

### 6.3 工具事件是谁发的

**不在工具内部发**。`core_agent.py:484-516` 的 `_wrap_tool_for_observer` 在每次
`tool.forward(...)` 前统一发一条 `ProcessType.TOOL`，携带
`tool_name` + `tool_arguments`（由 `_observer_call_arguments`，`:384-433`，通过 AST 扫描调用点解析）
+ 新生成的 `tool_call_id`，随后在 `observer.tool_call_context(tool_call_id)` 里执行工具本体——
**所以工具内部推送的 `card`/`search_content` 会自动带上同一个 `tool_call_id`**。

工具开发规范见 `sdk/nexent/core/tools/README.md:78-178`（以及更新版 `doc/docs/zh/sdk/core/tools.md:131-338`）：
类属性 `name / description / description_zh / inputs / init_param_descriptions / output_type / category / tool_sign`，
构造参数用 `pydantic.Field(...)` 声明，`observer` 用 `Field(..., exclude=True)`。
`ToolSign` / `ToolCategory` 枚举在 `core/utils/tools_common_message.py:7` 与 `:53`。

### 6.4 两种消费姿势

**姿势 1（推荐）——`agent_run()` 异步生成器**（`run_agent.py:487-556`）：
后台线程跑 agent，主协程轮询 `observer.get_cached_message()` 并 `yield` 每条 JSON。
注意它自带节流：缓冲区 < 8 条时 `await asyncio.sleep(0.05)`，否则 `sleep(0.1)`（`:526-530`）。

**姿势 2——`CoreAgent.run(stream=True)` 同步迭代**（`core_agent.py:1750`）：
产出 `ActionOutput` / `ActionStep` / `FinalAnswerStep`。实测序列：

```
['ActionOutput', 'ActionStep', 'ActionOutput', 'ActionStep', 'FinalAnswerStep']
```

> ⚠️ `NexentAgent.agent_run_with_observer` 消费这个流时会**跳过所有非 `ActionStep` 的产出**
> （`nexent_agent.py:1221-1222`），并取**最后一个 `ActionStep` 的 `.output`** 作为最终答案（`:1326`）。

### 6.5 OpenTelemetry 可观测性（可选依赖）

```python
from nexent.monitor import MonitoringConfig, get_monitoring_manager

get_monitoring_manager().configure(MonitoringConfig(       # monitoring.py:541-551
    enable_telemetry=True,          # 默认 False（monitoring.py:399）
    service_name="my-agent",        # 默认 "nexent-backend"
    provider="langfuse",            # otlp | phoenix | langfuse | langsmith（:422-426）
    otlp_endpoint="http://127.0.0.1:6006",
    otlp_protocol="http",           # "grpc" 时 phoenix/langfuse/langsmith 会被强制回 http（:480-484）
    export_traces=True, export_metrics=True,
    trace_content_mode="summary",   # summary | metrics | full（:450-454）
))
```

- 依赖 OTel 包，`enable_telemetry=True` 但未装时会**自动降级为 False 并打 warning**（`monitoring.py:465-470`）。
  安装：`pip install "nexent[performance]"`（extra 定义在 `sdk/pyproject.toml:85-93`）。
- 另有环境变量开关 `_enabled = os.getenv(...)`（`monitoring.py:2627-2632`）——这是 SDK 内少数读 env 的地方。
- Agent 侧埋点入口：`start_agent_run`（`:857`）、`trace_agent_step`（`:909`）、`record_model_call`（`:2141`）。

---

## 7. 无 Docker 运行可行性结论（逐条给依据）

### 7.1 ✅ 纯 SDK 就能用（已实机验证）

| 能力 | 依据 | 验证 |
|---|---|---|
| 模型调用（任意 OpenAI 兼容端点） | `openai_llm.py:180-350`，只需 `api_base` + `api_key` | 探针 A/B/D |
| Agent 主循环（ReAct / code_action） | `core_agent.py:519` `CoreAgent(CodeAgent)`，自己实现 `run/_run_stream` | 探针 A/B |
| 工具执行 | 本地 `LocalPythonExecutor`，`sandbox_config=None` 时默认走它（`agent_model.py:473-481` 注释） | 探针 A/B/D |
| 流式事件（思考/工具/执行日志/最终答案） | `observer.py` 全内存队列，无外部依赖 | 探针 A/B/D |
| 直接调用本地工具类（含写文件） | `nexent_agent.py:349-537`；`CreateFileTool` 实测写出文件 | 探针 A |
| MCP 工具（streamable-http / sse） | `run_agent.py:414-460` + `managed_mcp.py`；只需网络可达 | 探针 D（fastmcp 实测） |
| 记忆**协议层**（策略、幂等键、embedding 计算、请求/结果模型） | `memory/service.py:106-277` | 探针 C |
| 记忆**降级**（没后端时不崩） | `store_memory_tool.py:186-200` 返回提示串 | 探针 C |
| 上下文压缩 / ContextManager | 纯 Python，`context/manager.py`，prompt 从打包的 YAML 读（`core/prompts/__init__.py:14-31`） | 探针 A/B 隐式 |
| OTel 观测（可选） | `monitor/monitoring.py`，不装 OTel 包则自动关闭 | 【未验证】 |

### 7.2 ❌ 必须依赖后端服务（逐条依据）

| 能力 | 缺什么就跑不了 | 依据 |
|---|---|---|
| **记忆持久化/向量检索** | PostgreSQL + Elasticsearch + 后端索引服务 | `sdk/nexent/memory/service.py:1-26` 明确把持久化推给后端；`backend/services/memory_retrieval_service.py:9,27-32` 导入 `database.memory_*_db`、`services.memory_index_service` |
| **`MemoryContextService.build_context`（Phase 4 流水线）** | 后端服务类 | `backend/services/memory_context_service.py:88,124`（**SDK 内不存在该类**） |
| **知识库检索 `KnowledgeBaseSearchTool`** | 向量库客户端 `vdb_core` + embedding 模型 + `DATA_PROCESS_SERVICE` | `knowledge_base_search_tool.py:73,139`；`vdb_core/embedding_model` 是 `exclude=True`，只能从 metadata 注入 |
| **Plan 持久化（`enable_planning=True`）** | Redis（不给就退回本地内存态） | `agent_model.py:468-472` 注释；`core_agent.py:616` `PlanRepo(redis_client=...)` |
| **文件上传/下载（MinIO / S3）** | MinIO/S3 凭据 | `upload_to_s3_tool.py`、`download_from_s3_tool.py`；`agent_model.py:482-488` |
| **`TerminalTool`** | SSH 可达的 `nexent-openssh-server` | `terminal_tool.py:77` 默认 `ssh_host="nexent-openssh-server"` |
| **Skill 工具族** | 后端技能目录 + agent/tenant 元数据 | `nexent_agent.py:569-612`（`local_skills_dir` 等来自 metadata/params） |
| **北向 API / 会话管理 / 前端 SSE** | FastAPI 服务 + PG | `backend/northbound_service.py` 等（`doc/docs/zh/developer-guide/environment-setup.md:113-119`） |
| **Docker/WASM 沙箱隔离** | Docker daemon | `agent_model.py:345-357` `sandbox_policy`；`core/agents/sandbox.py:2725` 等 `docker.from_env()`。**不配就是本地执行器，不需要 Docker** |
| **Celery / 异步任务** | 【未验证】SDK 内未见 Celery 引用；`agent_run` 用的是进程内 `ThreadManager`（`core/concurrency/manager.py`） | — |

### 7.3 ⚠️ "看起来像依赖后端、其实不依赖"

- **`nexent.core.tools` 的整包导入**：`nexent_agent.py:26` `from ..tools import *` 拉入 30+ 工具模块，
  实测会把 `boto3 / pymysql / psycopg2 / pymssql / paramiko / exa_py / tavily` 一起 import 进 `sys.modules`。
  但这**只是装包与 import 负担**，**不会真去连 Docker / K8s / MinIO / ES**：
  `docker`、`kubernetes`、`elasticsearch` 实测**不在** `sys.modules` 里（懒加载），
  `container/docker_client.py`、`k8s_client.py` 只在显式构造客户端时才连。
  探针 A/B/D 全程未起任何服务。
- **`MemoryConfig`**：定义在 `models.py:148-166`，但 **SDK 内无任何消费方**，纯 DTO。
- **`elasticsearch==8.17.2`**：是 pip 硬依赖（`sdk/pyproject.toml:22`），但实测 **agent 路径根本不会 import 它**
  （`nexent.core.agents.run_agent` import 后 `sys.modules` 里没有 `elasticsearch`）。
  真正的建连与 kNN 全在后端（`backend/services/memory_retrieval_service.py:9,27-32`）。

### 7.4 一句话总结

> **"模型 + Agent + 工具 + 流式观测"这一类能力 100% 在 SDK 内闭环，只要求一个 OpenAI 兼容 HTTP 端点；
> "记忆持久化 / 知识库 / 计划持久化 / 对象存储 / 技能 / 平台 API"这一类能力，SDK 只提供接口或客户端，
> 真正的实现在 `backend/` + PG/ES/Redis/MinIO 里——不启动它们，这些能力要么降级（记忆工具返回提示串），
> 要么直接抛 `ValueError`（比如 `KnowledgeBaseSearchTool` 缺 `vdb_core`）。**

---

## 8. 依赖清单与安装命令

### 8.1 直接依赖（`sdk/pyproject.toml:19-63`，逐条抄录）

```
cryptography>=42.0.0            aiofiles>=24.1.0             elasticsearch==8.17.2
exa_py==1.14.0                  httpx[socks]>=0.28.1         numpy>=1.26.4
openai>=1.69.0                  pydantic[email]>=2.11.1      python-dotenv>=1.1.0
PyYAML>=6.0.1                   Requests>=2.32.3             rich>=13.9.4
setuptools>=75.1.0              websockets>=14.2             smolagents[mcp]==1.23.0
Pillow>=10.0.0                  aiohttp>=3.1.13              jieba>=0.42.1
boto3>=1.37.34                  botocore>=1.37.34            python-multipart>=0.0.20
mcpadapt>=0.1.13                mcp>=1.24.0,<1.30            fastmcp>=2.14.2,<3.0
docker>=7.0.0                   kubernetes>=29.0.0           tiktoken>=0.5.0
tavily-python                   linkup-sdk                   paramiko>=3.4.0
pymysql>=1.1.0                  psycopg2-binary>=2.9.9       pymssql>=2.2.11
openpyxl>=3.1.5                 orjson>=3.11.5,<4            pypdf==6.9.1
python-pptx==1.0.2              ijson==3.5.0                 langchain-text-splitters==1.1.2
ebooklib==0.20                  pypandoc==1.17               charset-normalizer>=3.4.0
```

（`linkup-sdk` 在 `:48` 和 `:50` 出现了两次，是源码里的重复项，无害。）

### 8.2 可选 extra（`sdk/pyproject.toml:76-96`）

| extra | 内容 | 用途 |
|---|---|---|
| `quality` | `ruff>=0.9.0`, `pytest>=8.1.0` | 开发 |
| `data_process` | `unstructured[all-docs]`, `unstructured-inference==1.2.0` | 文档解析 |
| `performance` | `opentelemetry-*`（api/sdk/semantic-conventions/instrumentation/fastapi/requests/exporter-otlp） | §6.5 的 OTel |
| `dev` | `nexent[quality, data_process, performance]` | 全都要 |

### 8.3 `[tool.uv] override-dependencies`（`sdk/pyproject.toml:65-74`）

```
pymongo==999.0.0  psycopg2-binary==999.0.0  redis==999.0.0  neo4j==999.0.0
chromadb==999.0.0  pinecone-client==999.0.0  weaviate-client==999.0.0
```

`999.0.0` 是不存在的版本号 ⇒ **显式禁装**。**只有 uv 会读这段配置**（pip 不读），
所以用 uv 安装时这些库不会被拖进来。

### 8.4 实机解析结果（`uv pip freeze` 共 169 个包，关键版本）

```
smolagents==1.23.0        mcp==1.29.1              mcpadapt==0.1.19
fastmcp==2.14.7           openai==3.28.0           pydantic==2.14.0
elasticsearch==8.17.2     boto3==1.43.111          docker==7.2.0
kubernetes==37.0.1        pymssql==2.4.4           psycopg2-binary==2.9.13
pypandoc==1.17            ijson==3.5.0             tiktoken==0.14.0
numpy==2.4.6              httpx==0.28.1            rich==15.0.0
jieba==0.42.1             langchain-text-splitters==1.1.2   ebooklib==0.20
orjson==3.13.0            pypdf==6.9.1             python-pptx==1.0.2
```

> 注意 `openai==3.28.0`（pyproject 只写 `>=1.69.0`）。实测在 openai 3.x 上功能正常，
> 但这是无上界依赖的典型风险点，生产请自行钉版本。

### 8.5 一键安装脚本（Windows PowerShell，已实测）

```powershell
# 前提：已安装 uv（本项目验证用 uv 0.11.26）
uv python install 3.11
uv venv --python 3.11 .venv-nexent311
uv pip install -e "第三方\nexent\sdk" --python .\.venv-nexent311\Scripts\python.exe

# 校验
.\.venv-nexent311\Scripts\python.exe -c @"
import nexent, smolagents
from nexent.core.agents.run_agent import agent_run
from nexent.core.agents.core_agent import CoreAgent
from nexent.core.models.openai_llm import OpenAIModel
from nexent.memory import MemoryService
print('ok', nexent.__file__, smolagents.__version__)
"@
```

镜像加速（仓库文档 `environment-setup.md:47-71` 给的源）：

```bash
uv pip install -e "第三方/nexent/sdk" --index https://pypi.tuna.tsinghua.edu.cn/simple \
    --index https://mirrors.aliyun.com/pypi/simple/
```

---

## 9. 验证记录

**环境**：Windows · Python **3.11.15**（uv 下载的独立 CPython）·
`uv pip install -e 第三方\nexent\sdk` · 169 个包 · **未启动任何 Docker / Postgres / ES / FastAPI**。

**探针脚本**（可复跑）：`_nexent_probe/mock_llm.py`（纯 stdlib 的 OpenAI 兼容 SSE mock server）、
`e2e_a_agent_run.py`、`e2e_b_core_agent.py`、`e2e_c_memory.py`、`e2e_d_mcp.py`。

| 探针 | 覆盖 | 结果 |
|---|---|---|
| **A** | `agent_run()` + `ToolConfig(source="local", class_name="CreateFileTool")` | ✅ **PASS**。文件真被写出（内容 `Hello from Nexent SDK`）；消息类型序列完整（含 `tool`/`card`/`execution_logs`/`final_answer`） |
| **B** | 直连 `CoreAgent` + `@tool` 修饰的 Python 函数 | ✅ **PASS**。`ActionStep.observations == 'Execution logs:\n5\n'`；负向对照确认裸 `CoreAgent` 抛 `RuntimeError: CoreAgent requires a context runtime from the agent factory` |
| **C** | `MemoryService` + 自定义钩子 + 策略 + 两个记忆工具 | ✅ **PASS**。写入/检索/`PermissionError` 拦截/未接线提示 全部符合预期 |
| **D** | MCP over streamable-http（`fastmcp` 起 `mcp_echo`） | ✅ **PASS**。执行日志出现 `echo:ping`，最终答案正常 |
| **E** | **把本文档里的代码块原样抠出来执行**（`_nexent_probe/run_doc_snippets.py`：正则提取 ```python 块 → 只替换本地端点常量 → 子进程运行） | ✅ **PASS**。§2.1 与 §2.2 两段**逐字未改**跑通。<br>· §2.1 真实调用了 `create_file` 并落盘：`OBS: {"status": "success", "file_path": "hello.txt", ...}`，最终答案 `<final_answer>DONE</final_answer>`<br>· §2.2 产出 `ActionOutput, ActionStep, ActionOutput, ActionStep, FinalAnswerStep` |

> 探针 E 是"可直接运行"的最强证据：文档里的代码不是手写示意，而是**从 `.md` 里抠出来就能跑**。
> 复跑：`python _nexent_probe/run_doc_snippets.py`。

### 实地踩坑（全部可复现）

| 编号 | 现象 | 根因 | 处置 |
|---|---|---|---|
| **P1** | 用 **pip**（Python 3.13/3.12）装 `-e sdk`：解析器空转 3.5 分钟、CPU 197s 无进展 | `requires-python = ">=3.11,<3.12"`（`pyproject.toml:12`）导致约束紧张，pip 回溯爆炸 | **改用 uv + 真 3.11**，秒级完成 |
| **P2** | 官方文档 `basic-usage.md:60-73` 的 `CoreAgent(...).run(...)` 直接抛 `RuntimeError` | `core_agent.py:593` 默认注入 `UnconfiguredContextRuntime`（`contracts.py:185-194`） | 显式传 `context_runtime=ManagedContextRuntime(ContextManager(ContextManagerConfig(), max_steps=N))` |
| **P3** | `ToolConfig(class_name=..., source="local")` 报 `name: Field required` | `ToolConfig.name` 写的是 `Optional[str] = Field(description="Tool name")`，**没有默认值**（`agent_model.py:135`） | 永远显式传 `name=` |
| **P4** | `backend_search` 钩子返回 UUID 字符串 ⇒ `ValidationError: memory_id Input should be a valid integer` | `MemorySearchResult.memory_id: Optional[int]`（`models.py:75`）vs `service.py:337` 原样透传 | 钩子里把 `memory_id` 映射成 int（真后端天然是 PG 自增主键） |
| **P5** | 直接 `StoreMemoryTool()` 调用 `forward()` 报 `'FieldInfo' object has no attribute 'store_memory'` | 直接构造时 pydantic 的 `Field(...)` 哨兵没被解析，`self.tenant_id` 等仍是 `FieldInfo` | **必须**经 `NexentAgent.create_tool(ToolConfig(..., metadata={...}))` 构造——SDK 自己在 `nexent_agent.py:435-472` 就是这么绕过的 |
| **P6** | `ProcessType.FINAL_ANSWER` 的 content 带 `<final_answer>` 信封 | 默认 `enable_protocol_repair_retry=False` ⇒ 走 legacy 解析、整段输出即答案（`core_agent.py:1297-1300, 1444-1447, 1013-1020`） | 自行剥壳，或开启严格协议（§2.5） |
| **P7** | 终端里 `uv venv` / `uv python install` 输出把 pwsh 判成 stderr，`$LASTEXITCODE = 1` | uv 往 stderr 写进度信息 | 不是失败；以产物为准（`.venv-nexent311\Scripts\python.exe` 能跑即成功） |

### 明确的 `【未验证】` 清单

1. `source="langchain"` 路径（`nexent_agent.py:539-541`）——需要额外 langchain 依赖，未跑。
2. GLM / DashScope / ModelEngine 的实际联网调用——无 API key，未跑。
3. `MonitoringConfig(enable_telemetry=True)` + OTel 导出——未装 `[performance]` extra。
4. `ExternalHttpProvider` 等外部记忆 provider 的构造与协议。
5. `sandbox_config`（Docker/WASM）路径——本轮结论恰恰是"不启 Docker"，故未验证。
6. `[未验证] Celery`：SDK 侧未见引用，但未对整个仓库做穷尽检索。

---

## 附录 A：关键签名速查（行号均可直接跳转）

```python
# sdk/nexent/core/utils/observer.py
MessageObserver(lang="zh", enable_nl2a_wrapper=False)                        # :137
ProcessType.<NAME>                                                            # :39-76
Message(message_type, content, tool_name=None, tool_arguments=None,           # :809
        agent_id=None, agent_name=None, depth=0, tool_call_id=None,
        invocation_id=None, attempt_id=None, metadata=None)

# sdk/nexent/core/agents/agent_model.py
ModelConfig(cite_name, api_key="", model_name, url, temperature=0.1, top_p=0.95,   # :22
            enable_thinking=False, reasoning_effort=None, ssl_verify=True,
            model_factory=None, extra_body=None, max_output_tokens=None,
            timeout_seconds=None, concurrency_limit=None, prompt_cache=None, ...)
ToolConfig(class_name, name, description=None, inputs=None, output_type=None,      # :133
           params=None, source="local", usage=None, metadata=None, labels=None)
AgentConfig(name, description, tools, model_name, max_steps=15, ...)               # :264
AgentRunInfo(query, model_config_list, observer, agent_config, stop_event, ...)    # :385
AgentHistory(role, content)                                                        # :360

# sdk/nexent/core/agents/nexent_agent.py
NexentAgent(observer, model_config_list, stop_event, mcp_tool_collection=None,     # :232
            redis_client=None, sandbox_config=None, minio_client=None,
            conversation_id=None, user_id=None, tenant_id=None, workspace_path=None,
            workspace_run_id=None, minio_files=None, cancellation_scope=None,
            user_context=None)
  .create_model(model_cite_name)                                                   # :301
  .create_tool(tool_config)                                                        # :657
  .create_single_agent(agent_config, _managed_context=False, *,
                       context_items_override=None, _sandbox_tree_context=None)    # :739
  .set_agent(agent)                                                                # :1739
  .add_history_to_agent(history)                                                   # :1104
  .agent_run_with_observer(query, reset=True, additional_args=None)                # :1182

# sdk/nexent/core/agents/core_agent.py
CoreAgent(observer, prompt_templates=None, verification_config=None, *args, **kwargs)  # :544
  .run(task, stream=False, reset=True, images=None,
       additional_args=None, max_steps=None, return_full_result=None)              # :1750

# sdk/nexent/core/agents/run_agent.py
agent_run(agent_run_info, thread_manager=None, execution=None, deferred_run=None)  # :487  (async gen)
shutdown_default_agent_thread_manager(timeout=15.0)                                # :175

# sdk/nexent/core/agents/context/{manager,config,runtime}.py
ContextManager(config=None, max_steps=None)                                        # manager.py:38
ContextManagerConfig(token_threshold=10000, context_window_tokens=10000,          # config.py:12
                     keep_recent_steps=4, compaction_trigger_ratio=0.8, ...)
ManagedContextRuntime(context_manager, items=None)                                 # runtime.py:27

# sdk/nexent/core/models/openai_llm.py
OpenAIModel(observer=MessageObserver, temperature=0.2, top_p=0.95, ssl_verify=True,  # :187
            reasoning_effort=None, reasoning_budget_tokens=None,
            reasoning_capability=None, model_factory=None, display_name=None,
            extra_body=None, max_output_tokens=None, max_tokens=None,
            timeout_seconds=None, retry_config=None, cancellation_scope=None,
            concurrency_limit=None, concurrency_key=None,
            connect_timeout_seconds=10.0, write_timeout_seconds=30.0,
            pool_timeout_seconds=10.0, *args, **kwargs)
  .__call__(messages, stop_sequences=None, response_format=None,
            tools_to_call_from=None, **kwargs) -> ChatMessage                   # :352

# sdk/nexent/memory/service.py
MemoryService(embedding_model=None, embedding_model_info=None,                      # :80
              backend_store=None, backend_search=None)
  .store_memory(content, tenant_id, user_id, agent_id, conversation_id=None,
                layer=MemoryLayer.AGENT, memory_type=MemoryType.SHORT_TERM,
                idempotency_key=None, embedding=None) -> StoreMemoryResult      # :106
  .search_memory(query, tenant_id, user_id, agent_id, conversation_id=None,
                 layers=None, top_k=5, threshold=0.65,
                 embedding=None) -> List[MemorySearchResult]                    # :188
get_memory_service() / reset_memory_service()                                       # :351 / :359

# sdk/nexent/memory/embedding_model.py
EmbeddingModelInfo(model_name, dimension, base_url, api_key, model_repo=None, ssl_verify=True)  # :37
get_embedding_client(model_name, dimension, base_url, api_key,
                     model_repo=None, ssl_verify=True)                              # :80

# sdk/nexent/core/agents/output_protocol.py
classify_model_output(output, *, protocol, finish_reason=None, logger=None)         # :372
    -> ExecutableAction | ExplicitFinalAnswer | NonterminalThought

# smolagents 1.23.0（实测 introspect）
tool(tool_function: Callable) -> Tool
CodeAgent(tools, model, prompt_templates=None, additional_authorized_imports=None,
          planning_interval=None, executor=None, executor_type='local',
          executor_kwargs=None, max_print_outputs_length=None, stream_outputs=False,
          use_structured_outputs_internally=False, code_block_tags=None, **kwargs)
  # **kwargs -> MultiStepAgent(instructions, max_steps=20, add_base_tools=False,
  #   verbosity_level, managed_agents, step_callbacks, name, description,
  #   provide_run_summary, final_answer_checks, return_full_result, logger)
Tool.from_langchain(langchain_tool) -> Tool
```

## 附录 B：模块速查地图

| 你要做的事 | import 路径 |
|---|---|
| 观察者 / 事件类型 | `nexent.core.utils.observer`（也可从 `nexent.core` 直接拿 `MessageObserver, ProcessType`，`core/__init__.py:1-3`） |
| 配置模型 | `nexent.core.agents.agent_model` |
| 高层流式运行 | `nexent.core.agents.run_agent.agent_run` |
| Agent 工厂 | `nexent.core.agents.nexent_agent.NexentAgent` |
| 裸 Agent | `nexent.core.agents.core_agent.CoreAgent` |
| LLM | `nexent.core.models.openai_llm.OpenAIModel`（`nexent.core.models` 也导出，`models/__init__.py:1`） |
| 工具集合 | `nexent.core.tools`（35 个导出，`__init__.py:34-70`） |
| 记忆 | `nexent.memory` |
| 上下文运行时 | `nexent.core.agents.context` |
| 可观测性 | `nexent.monitor` |

> ⚠️ `nexent.core.agents.agent_context` 是**已废弃的兼容路径**，import 时会发 `DeprecationWarning`
> 并声明 `v2.4.0` 移除（`agents/agent_context/__init__.py:8-13`）。请用 `nexent.core.agents.context`。
