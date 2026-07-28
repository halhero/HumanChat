# 41 ToolRegistry 与 Provider 统一注册修改记录

## 本次目标

本次修改解决工具定义、治理元数据和 CLI 命令分别维护的问题，并删除当前只有一个 Provider 却在每次调用时逐个搜索的 Composite 实现。

修改后，Provider 只在应用启动时加载一次工具，ToolRegistry 建立稳定索引，Graph 与 CLI 共享同一个 Registry。

## 修改前的问题

旧 `LocalProjectToolProvider` 同时维护：

```text
LangChain Tool 列表
工具名称
工具描述
CLI 命令
CLI 用法
权限字段
运行时查找
```

工具名称和描述已经存在于 LangChain Tool，却又在 `ToolMetadata` 中手写一遍，修改一处后容易与另一处不一致。

`CompositeToolProvider` 当前只有一个 Local Provider，但每次 `get_tool()` 都重新创建工具字典，每次 CLI 工具命令又重新创建整个 Provider。

## 修改内容

### 1. 新增 `RegisteredTool`

一个注册项组合：

```text
tool：真实 LangChain BaseTool
source：工具来源
policy：只读、确认等治理规则
cli：可选 CLI 命令配置
```

名称和描述通过属性直接读取：

```python
registration.tool.name
registration.tool.description
```

不再在 Local Provider 中重复书写。

### 2. 新增 `ToolPolicy`

治理字段从描述性元数据中独立出来：

```text
read_only
requires_confirmation
```

后续增加写工具或 MCP 工具时，Graph 和 CLI 可以读取同一份安全策略。

### 3. 新增 `CliCommandSpec`

CLI 相关配置只有：

```text
command
usage
```

没有 CLI 入口的工具可以不提供该字段，但仍然能够被 Agent 使用。

### 4. Provider 只负责加载

新协议缩小为：

```python
load_tools() -> list[RegisteredTool]
```

Provider 不再承担运行时查找、调用和命令索引。未来 Local、MCP 或插件 Provider 只需把外部工具转换为统一注册项。

### 5. 新增 `ToolRegistry`

Registry 在构造时一次性建立：

```text
name -> RegisteredTool
CLI command -> RegisteredTool
```

同时检查工具名称和 CLI 命令是否重复。冲突会在应用启动时暴露，不会等到 Agent 正在执行时才发现。

### 6. 元数据改为派生视图

`ToolMetadata` 暂时保留供 CLI 展示，但它通过：

```python
ToolMetadata.from_registration(registration)
```

生成。名称、描述、来源和策略的事实来源都是 `RegisteredTool`，不再手写两份。

### 7. Graph 与 CLI 共享 Registry

`open_chat_runtime()` 只创建一次 ToolRegistry，然后：

```text
传给 build_graph() 绑定工具
保存到 ChatRuntime 供 CLI 使用
```

CLI 工具命令不再调用 `create_tool_provider()` 创建新对象。

### 8. 删除 CompositeToolProvider

多 Provider 聚合改为：

```python
ToolRegistry.from_providers(providers)
```

聚合只发生一次。Registry 建好后，工具调用与来源数量无关。

## 为什么这样设计

Provider 是外部工具来源，Registry 是应用内部索引。这是两个不同职责：

```text
Provider：我能提供哪些工具
Registry：系统当前注册了哪些工具，怎样按名称或命令找到它们
```

把运行时查找留在 Composite Provider 中，会让每个 Provider 都参与每次调用，也使 CLI 和 Graph 很难共享同一份稳定工具快照。

## 对成熟项目的意义

1. 工具名称和描述只有一个事实来源。
2. Provider 只加载一次，避免重复创建远程 MCP 连接。
3. 名称与命令冲突在启动阶段失败。
4. Graph 与 CLI 使用完全相同的工具集合。
5. 工具安全策略进入统一注册模型。
6. 增加 MCP Provider 时不需要修改 Registry 调用逻辑。

## 测试覆盖

新增测试验证：

```text
Provider 只加载一次
名称和命令索引正确
元数据来自 LangChain Tool
Registry 能统一调用工具
重复名称被拒绝
重复 CLI 命令被拒绝
```

## 下一步

下一步拆分 CLI：删除硬编码 `TOOL_COMMANDS` 和按工具名称解析参数的逻辑，建立命令注册与分发机制，让 ToolRegistry 中的 CLI 工具自动进入命令系统。
