# 48 MCP 工具接入设计与实现记录

## 本次目标

本次修改为 HumanChat 增加正式的 MCP 工具接入能力，使项目能够从一个或多个 MCP
Server 发现工具，并把这些工具转换为现有 LangChain / LangGraph 工具链可以使用的
`BaseTool`。

接入后的工具来源为：

```text
本地项目工具 ---------+
                     |
MCP Server 工具 ------+--> ToolProvider --> ToolRegistry --> Graph / CLI
                     |
未来 HTTP 业务工具 ---+
```

本次只接入 MCP Tools，不接入 MCP Resources、Prompts 和 Elicitation。原因是 HumanChat
当前已有成熟的工具循环，而 Resources 和 Prompts 需要单独设计上下文注入、缓存和权限
模型，不应在没有业务入口的情况下混入工具接入步骤。

## 官方能力依据

本次采用 LangChain 官方 `langchain-mcp-adapters`：

```text
https://docs.langchain.com/oss/python/langchain/mcp
https://github.com/langchain-ai/langchain-mcp-adapters
```

官方适配器提供 `MultiServerMCPClient`，能够：

1. 连接 stdio、HTTP、Streamable HTTP、SSE 等 MCP transport。
2. 从多个 MCP Server 加载工具。
3. 将 MCP Tool 转换为 LangChain `BaseTool`。
4. 保留 MCP 结构化内容和多模态内容。
5. 将 MCP 返回的业务错误转换为失败的 ToolMessage。
6. 为不同 Server 的工具名称增加 Server 前缀。

当前官方实现中：

```python
tools = await client.get_tools()
```

是异步接口。它返回的 MCP StructuredTool 也主要通过 coroutine 执行。HumanChat 当前使用
同步的：

```text
ChatRuntime.ask()
CompiledStateGraph.invoke()
ToolNode.invoke()
CLI command handler
```

因此不能只把 `await client.get_tools()` 的结果直接放进现有 ToolRegistry，否则模型真正
调用 MCP 工具时会遇到“工具不支持同步调用”的问题。

## 修改前的项目基础

HumanChat 已经具备适合 MCP 接入的以下结构：

```text
ToolProvider
  -> 负责加载某一来源的 RegisteredTool

ToolRegistry
  -> 聚合 Provider
  -> 校验工具名称和 CLI 命令冲突
  -> 向 Graph 和 CLI 提供统一工具快照

Graph
  -> llm.bind_tools()
  -> ToolNode
  -> 多轮工具循环
  -> 工具调用事件

CLI
  -> /tools 展示
  -> 通过 ToolRegistry 调用具有 CLI 命令的工具
```

因此 MCP 不需要重新实现一套工具系统。正确的接入位置是新增 `McpToolProvider`，让远程
工具转换成 `RegisteredTool` 后进入现有 Registry。

## 总体设计

修改后的依赖方向计划如下：

```text
Settings
  |
  | mcp_enabled / mcp_config_path / mcp_fail_fast
  v
MCP 配置文件
  |
  v
McpConfigLoader
  |  JSON 解析、Schema 校验、环境变量展开
  v
McpToolProvider
  |  每个 Server 独立发现工具
  |  include / exclude 过滤
  |  工具名称前缀
  |  安全策略解析
  v
RegisteredTool[]
  |
  v
ToolRegistry
  |
  +--> Graph / ToolNode
  |
  +--> CLI /tools
```

异步执行链计划如下：

```text
同步 Graph / CLI
  |
  v
MCP StructuredTool.func
  |
  v
McpAsyncBridge
  |
  v
独立后台 asyncio event loop
  |
  v
原始 MCP StructuredTool.coroutine
```

## 详细设计

### 1. MCP 默认关闭

新增环境配置：

```env
HUMANCHAT_MCP_ENABLED="false"
HUMANCHAT_MCP_CONFIG_PATH="config/mcp_servers.json"
HUMANCHAT_MCP_FAIL_FAST="false"
```

默认关闭的原因不是 MCP 不重要，而是 MCP Server 属于外部依赖：

```text
stdio Server 可能需要 Node.js、Python 或本地可执行文件
HTTP Server 可能需要网络和鉴权
不同开发者可用的 Server 不同
配置中可能包含仅存在于本机环境变量里的凭证
```

没有 MCP 配置的用户仍应能够使用本地工具和聊天功能。用户明确启用 MCP 后，如果配置
文件不存在或结构错误，应在启动阶段给出清晰错误，而不是静默忽略。

### 2. MCP 配置文件与应用环境分离

计划新增：

```text
config/mcp_servers.example.json  # 可提交的示例
config/mcp_servers.json          # 本地真实配置，Git 忽略
```

环境变量只决定 MCP 是否启用、配置文件在哪里以及连接失败是否中止应用。Server 列表、
transport、工具筛选和安全策略属于结构化数据，放在 JSON 文件中更适合校验和维护。

配置顶层结构：

```json
{
  "version": 1,
  "servers": {
    "server_name": {
      "enabled": true,
      "connection": {},
      "include_tools": [],
      "exclude_tools": [],
      "default_policy": {},
      "tool_policies": {},
      "startup_timeout_seconds": 15,
      "tool_timeout_seconds": 60
    }
  }
}
```

`connection` 内部保持官方 `MultiServerMCPClient` 的连接格式，不在 HumanChat 中重新
发明 transport 协议。例如：

```json
{
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "${MCP_ROOT}"]
}
```

或：

```json
{
  "transport": "http",
  "url": "https://example.com/mcp",
  "headers": {
    "Authorization": "Bearer ${MCP_TOKEN}"
  }
}
```

### 3. 严格配置校验

配置模型计划校验：

```text
version 必须是当前支持的版本
Server 名称只能包含安全的字母、数字、下划线和连字符
stdio 必须提供 command 和 args
HTTP / SSE / WebSocket 必须提供 url
include_tools 与 exclude_tools 不能包含同一个工具
启动和调用超时必须为正数
未知配置字段直接报错
```

使用 `extra="forbid"` 的原因是 MCP 配置包含权限和连接信息。拼错
`requires_confirmation` 后静默采用默认值，可能产生与配置者预期不同的安全行为。

### 4. 环境变量占位符

MCP 配置中的字符串支持：

```text
${VARIABLE_NAME}
```

HumanChat 在把连接交给官方适配器前递归展开 connection 中的字符串。未定义的变量应
阻止该 Server 加载，并且错误信息只显示变量名，不打印完整连接配置，避免日志泄漏 token。

真实凭证因此可以留在 `.env` 或系统环境中，而不是写进 JSON 并提交 Git。

### 5. 每个 Server 独立加载

不使用一次性：

```python
await client.get_tools()  # 同时加载所有 Server
```

而是逐个 Server 创建客户端并发现工具。原因包括：

1. 能准确记录哪个 Server 加载失败。
2. 一个 Server 故障时，可以保留其他健康 Server 的工具。
3. `source` 可以准确记录为 `mcp:<server_name>`。
4. 每个 Server 可以使用不同的过滤规则、超时和 ToolPolicy。
5. 配置中的 fail-fast 行为可以在 Server 粒度实现。

`HUMANCHAT_MCP_FAIL_FAST=false` 时，单个 Server 失败只记录错误并继续启动核心聊天；设为
`true` 时，任何启用的 MCP Server 加载失败都会中止 Runtime 创建，适合要求工具必须
可用的生产部署。

### 6. 工具名称必须带 Server 前缀

所有 MCP 工具使用官方 `tool_name_prefix=True`：

```text
weather_search
github_create_issue
filesystem_read_file
```

不允许使用没有 Server 前缀的远程工具名，原因是不同 MCP Server 经常暴露同名的
`search`、`read`、`list`。名称前缀同时解决：

```text
ToolRegistry 冲突
模型无法区分工具来源
日志无法定位 Server
安全策略无法准确匹配
```

### 7. 工具白名单和黑名单

每个 Server 支持：

```text
include_tools：非空时，只加载列出的原始 MCP 工具名
exclude_tools：从已经包含的工具中排除指定名称
```

策略匹配使用 Server 返回的原始名称，不使用添加前缀后的名称。这样配置不会因为修改
Server 显示名而变得难以阅读。

如果 include_tools 或 tool_policies 引用了 Server 实际不存在的工具，应认为配置存在
错误。工具权限配置不能悄悄失效。

### 8. MCP ToolPolicy

MCP Tool annotations 可以包含 `readOnlyHint`，但该字段只是 Server 提供的提示，不能
单独作为应用安全边界。

HumanChat 的最终策略按以下顺序解析：

```text
MCP annotation
  -> Server default_policy 覆盖
  -> 单工具 tool_policies 覆盖
  -> 生成现有 ToolPolicy
```

未知工具默认：

```text
read_only = false
requires_confirmation = true
```

即“不知道是否只读”时按可能写入处理。明确标记为只读的工具默认可以直接执行；明确的
单工具配置可以进一步收紧或放宽。

### 9. Graph 工具确认

当前 `ToolPolicy.requires_confirmation` 只在 CLI 工具命令中生效，Graph 的 ToolNode 会
直接执行模型发起的工具调用。接入 MCP 后，这个缺口必须补齐。

计划增加 Graph 节点：

```text
call_agent_model
  |
  | tool_calls 中存在 requires_confirmation
  v
review_tool_calls
  |
  +--> 用户批准 -> execute_project_tools
  |
  +--> 用户拒绝 -> reject_tool_calls -> call_agent_model
```

`review_tool_calls` 使用 LangGraph `interrupt()` 输出结构化请求。CLI 展示：

```text
工具名称
工具来源
是否只读
经过敏感字段遮盖的参数
```

敏感键名如 `token`、`password`、`secret`、`authorization`、`api_key` 不能原样出现在确认
界面或调试资料中。

拒绝后 Graph 为每个工具调用生成失败 ToolMessage，让模型知道用户拒绝，而不是让消息链
停留在一个没有对应 ToolMessage 的 AI tool call 上。

### 10. 同步与异步桥接

直接在每次 MCP 调用时执行 `asyncio.run()` 存在以下问题：

```text
每次创建和销毁 event loop
在已有 event loop 的环境中无法调用
异步资源生命周期分散
未来 Web / async Runtime 难以兼容
```

因此计划建立一个由 Runtime Context Manager 管理的 `McpAsyncBridge`：

```text
Runtime 打开
  -> 创建后台线程
  -> 在线程中创建一个 asyncio event loop
  -> MCP 工具发现和调用提交到这个 loop

Runtime 关闭
  -> 停止 loop
  -> 等待线程退出
  -> 释放异步资源
```

官方 MCP StructuredTool 的 coroutine 保留，同时为现有同步 ToolNode 添加 func 包装。这样：

```text
现有 Graph.invoke() 可以同步执行
现有 CLI invoke_tool() 可以同步执行
原始 coroutine 仍可供未来异步 Runtime 使用
```

### 11. 超时控制

每个 Server 单独配置：

```text
startup_timeout_seconds：工具发现最大等待时间
tool_timeout_seconds：单次工具调用最大等待时间
```

超时后取消提交到后台 event loop 的 Future，并把错误交给现有 ToolNode 错误处理。这样
一个失去响应的 MCP Server 不会无限挂住聊天循环。

### 12. CLI 展示范围

MCP 工具默认没有单独的斜杠命令，避免把几十个远程工具自动扩展成 CLI 命令空间。它们
仍然会出现在 `/tools` 中，并标记为：

```text
source = mcp:<server_name>
Agent 可用
只读或可写
是否需要确认
```

本地工具继续保留 `/files`、`/read`、`/search` 等显式 CLI 入口。

### 13. 错误与可观测性

启动阶段日志记录：

```text
启用的 MCP Server 数量
每个 Server 加载的工具数量
被 include / exclude 过滤的数量
失败 Server 的名称和异常类型
最终 Registry 中 MCP 工具总数
```

日志不能输出完整 connection、headers、env 或用户参数。

工具执行事件继续进入现有 `tool_events`。事件状态判断除现有错误前缀外，还要识别
LangChain ToolMessage 的 `status="error"`，以兼容官方 MCP 适配器的错误格式。

## 计划新增或修改的文件

### 新增

```text
human_chat/mcp_config.py
  MCP JSON Schema、读取、校验和环境变量展开

human_chat/mcp_provider.py
  McpAsyncBridge、Server 工具发现、同步适配、策略解析

human_chat/tool_resources.py
  本地 Provider 与 MCP Provider 的 Runtime 生命周期组装

human_chat/tool_review.py
  Graph 工具确认请求、决定和参数遮盖模型

config/mcp_servers.example.json
  stdio 与 HTTP Server 示例
```

### 修改

```text
human_chat/config.py
  增加 MCP 环境配置

human_chat/runtime.py
  使用 open_tool_registry() 管理 MCP 异步资源生命周期

human_chat/graph.py
  通用工具提示、工具确认节点、拒绝 ToolMessage、MCP 错误状态

human_chat/schemas.py
  增加工具确认临时状态

human_chat/cli/interrupts.py
  处理 tool_review interrupt，并支持连续 interrupt

human_chat/cli/commands.py
  /tools 展示 Agent-only MCP 工具和策略

requirements.txt
  增加 langchain-mcp-adapters

.env.example
  增加 MCP 开关与配置路径

.gitignore
  忽略真实 MCP 配置文件

README.md
  增加启用、配置和安全策略说明
```

## 不在本次范围内

```text
MCP Resources 自动注入 Prompt
MCP Prompts 选择和模板合并
MCP Elicitation 交互
OAuth 浏览器授权流程
长连接 Stateful ClientSession
MCP 工具动态热重载
Web UI 中的工具确认组件
按用户动态注入 MCP 凭证
```

这些能力需要独立的产品入口或身份系统。当前优先完成稳定的工具发现、调用、治理和错误
隔离，不提前实现没有消费者的框架。

## 为什么这套设计适合成熟项目

1. MCP 是 ToolProvider 的一种实现，不侵入 Graph 的工具来源选择。
2. 本地工具和远程工具继续使用同一个 ToolRegistry。
3. 外部 Server 失败不会默认拖垮核心聊天功能。
4. 生产环境可以通过 fail-fast 要求 MCP 必须可用。
5. Server 名称前缀和 Registry 双重检查避免工具冲突。
6. 白名单、黑名单和单工具策略形成最小权限入口。
7. 未知写入能力默认需要用户确认。
8. 敏感参数不会直接显示在确认界面。
9. 后台 event loop 使同步 Runtime 能稳定调用异步 MCP 工具。
10. 超时和错误事件防止外部工具无限阻塞。
11. 配置文件与凭证环境变量分离，避免秘密进入 Git。
12. Runtime 关闭时会释放异步桥接资源。
13. 未来迁移 async Runtime 时仍可复用原始 MCP coroutine。

## 验证计划

按照本次要求，不新增测试文件。

实现完成后执行：

```text
python -m compileall -q human_chat
python -m pytest -q
git diff --check
```

此外进行不写入项目测试文件的运行时验证：

```text
MCP 关闭时只加载本地工具
示例配置能够通过 Schema 校验
缺失环境变量能够给出脱敏错误
MCP StructuredTool 获得同步 func 包装
ToolRegistry 能看到带 Server 前缀和 source 的 MCP 工具
Graph 能识别 requires_confirmation 策略
```

## 实际实现结果

代码已经按照本资料的设计完成，最终实现包括：

### 1. MCP 配置入口

`Settings` 已增加：

```text
mcp_enabled
mcp_config_path
mcp_fail_fast
```

对应环境变量已经加入 `.env.example`。默认值仍为关闭，因此没有本地 MCP 配置的用户不会
在启动时连接外部 Server。

### 2. 配置 Schema 与秘密展开

`human_chat/mcp_config.py` 已实现：

```text
JSON 读取错误定位
Pydantic extra=forbid
Server 名称校验
transport 必填字段校验
工具筛选冲突和重复校验
策略工具名称校验
正数超时校验
${VARIABLE_NAME} 递归展开
相对 stdio cwd 按配置目录解析
```

未定义的环境变量只报告变量名和 Server 名，不输出 connection、headers 或 env 内容。

### 3. MCP Provider 与异步桥接

`human_chat/mcp_provider.py` 已实现：

```text
McpAsyncBridge
McpToolProvider
Server 独立发现
fail-fast / skip-failed 两种启动策略
官方 tool_name_prefix
include / exclude 过滤
MCP annotation 与应用策略合并
同步 func 与原始 coroutine 双入口
启动和调用超时
事件循环关闭时 async generator 与 executor 清理
```

每个成功加载的 Server 会记录发现、注册和过滤的工具数量。失败日志只使用 Server 名称，
不会主动打印连接配置。

### 4. Runtime 生命周期

`human_chat/tool_resources.py` 新增 `open_tool_registry()`。Runtime 现在按以下顺序打开：

```text
Checkpointer
  -> MemoryResource
  -> ToolRegistry / MCP AsyncBridge
  -> Graph
```

Graph 和 CLI 使用 Registry 的时间始终位于异步桥接器有效期内；退出聊天 Context 后，后台
event loop 会停止并等待线程结束。

### 5. Graph 工具治理

Graph 已增加：

```text
review_tool_calls
reject_tool_calls
route_after_tool_review
```

只要一批 tool_calls 中有工具要求确认，就会生成 `tool_review` interrupt。批准后进入原有
ToolNode，拒绝后生成具有对应 `tool_call_id` 的失败 ToolMessage，再让模型根据拒绝结果
继续回答。

批准和拒绝都会计入工具轮数，防止模型反复请求同一个被拒绝工具而绕过最大轮数限制。

### 6. CLI 连续 Interrupt

`handle_graph_interrupts()` 已从单次处理改为循环处理。现在以下流程可以连续完成：

```text
工具确认 interrupt
  -> Runtime.resume()
  -> 工具执行与最终回答
  -> 记忆确认 interrupt
  -> Runtime.resume()
  -> Graph 完成
```

旧实现只处理首次返回中的 interrupt，工具确认后出现的记忆确认可能被遗漏。本次在 MCP
接入时一并修正。

### 7. 参数与事件脱敏

工具确认请求和 `tool_events.arguments` 都通过同一个脱敏函数处理。以下键名及其组合不会
显示原值：

```text
authorization
cookie
credential
password
secret
token
api_key
access_key
```

脱敏只影响展示和事件副本，不修改真正传给 ToolNode 的工具参数。

### 8. CLI 工具展示

`/tools` 现在展示 Registry 中全部工具，而不仅是具有 CLI 命令的工具。展示内容包括：

```text
工具名称
描述
source
只读 / 可写
需确认 / 无需确认
CLI 用法或“仅供 Agent 调用”
```

MCP 工具默认不自动创建斜杠命令，但模型可以正常调用。

## 实际验证结果

安装并验证的适配器版本：

```text
langchain-mcp-adapters 0.3.2
```

使用官方公开 LangChain Docs MCP Server：

```text
https://docs.langchain.com/mcp
```

完成真实联调，发现的工具包括：

```text
langchain_docs_search_docs_by_lang_chain
langchain_docs_query_docs_filesystem_docs_by_lang_chain
langchain_docs_submit_feedback
```

实际验证：

```text
MCP HTTP 工具发现成功
所有工具具有 Server 前缀
source 正确记录为 mcp:<server>
StructuredTool 同时保留 func 和 coroutine
同步 tool.invoke() 返回标准 LangChain content blocks
真实 MCP Tool 经 HumanChat Graph 和 ToolNode 执行成功
Graph 工具事件 status=success
需要确认的工具批准后执行
需要确认的工具拒绝后不执行，事件 status=denied
api_key 在 interrupt payload 和 tool_events 中均显示为 [REDACTED]
事件循环关闭后没有未等待 async generator 警告
```

项目检查结果：

```text
python -m pytest -q
37 passed

python -m compileall -q human_chat
通过

python -m pip check
No broken requirements found.

git diff --check
通过
```

按照本次明确要求，没有向项目增加新的测试文件。真实 MCP 和工具确认验证通过临时命令
完成，没有在仓库中留下测试 Server、真实 MCP 配置或凭证。

## 最终涉及文件

### 新增文件

```text
human_chat/mcp_config.py
human_chat/mcp_provider.py
human_chat/tool_resources.py
human_chat/tool_review.py
config/mcp_servers.example.json
资料/48-MCP工具接入设计与实现记录.md
```

### 修改文件

```text
.env.example
.gitignore
README.md
requirements.txt
human_chat/config.py
human_chat/runtime.py
human_chat/graph.py
human_chat/schemas.py
human_chat/tool_provider.py
human_chat/cli/commands.py
human_chat/cli/debug.py
human_chat/cli/interrupts.py
```

## 后续扩展边界

下一阶段如果需要 MCP Resources、Prompts、OAuth、Elicitation 或 Stateful Session，应分别
建立清晰的产品入口。当前的 `McpToolProvider` 已经为这些功能保留独立 MCP 配置与 Runtime
生命周期位置，但没有让未使用能力进入 Graph 状态或业务接口。
