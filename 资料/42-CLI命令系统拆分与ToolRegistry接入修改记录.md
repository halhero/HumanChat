# 42 CLI 命令系统拆分与 ToolRegistry 接入修改记录

## 本次目标

本次修改解决原 `cli.py` 同时承担应用启动、会话选择、输入模式、命令路由、工具参数解析、Graph interrupt、调试输出和聊天循环的问题。

修改后 CLI 成为一个按职责拆分的包，命令通过 Registry 精确分发，工具命令从 ToolRegistry 自动生成。

## 修改前的问题

原 `cli.py` 超过四百行，并维护多套平行判断：

```text
MEMORY_COMMAND
INPUT_COMMAND
DEBUG_COMMAND
TOOL_COMMANDS
startswith 分支
按工具名称判断参数
```

这会导致：

1. `/memoryx` 也可能被 `startswith("/memory")` 当成记忆命令。
2. ToolRegistry 新增工具后，还要修改 `TOOL_COMMANDS`。
3. 新增 MCP 工具时，还要在 `_build_cli_tool_arguments()` 增加工具名称分支。
4. CLI 的主循环难以独立测试。

## 修改内容

### 1. `cli.py` 改为 CLI 包

新结构：

```text
human_chat/cli/__init__.py
human_chat/cli/app.py
human_chat/cli/commands.py
human_chat/cli/interrupts.py
human_chat/cli/debug.py
```

`human_chat.cli` 仍然导出 `chat_loop` 和 `run_once`，因此 `main.py` 的公开入口保持不变。

### 2. `app.py` 只负责应用流程

职责包括：

```text
加载设置
启动和停止可选 TTS 服务
选择会话
打开 Runtime
选择初始输入模式
运行聊天循环
展示一轮结果
```

它不再知道每个命令的内部参数规则。

### 3. 新增 `CliContext`

命令共享的可变运行上下文包含：

```text
runtime
input_provider
debug_enabled
```

输入模式和调试状态不再是主循环中的零散局部变量，命令可以通过明确上下文更新它们。

### 4. 新增 `CliCommandRegistry`

每个命令表示为：

```python
CliCommand(name, usage, handler)
```

Registry 启动时检查命令是否重复，分发时只精确匹配第一个 token。

因此：

```text
/memory -> 匹配
/memory add ... -> 匹配并传入剩余参数
/memoryx -> 不匹配
```

### 5. 内置命令统一注册

以下命令进入统一 Registry：

```text
/memory
/input
/debug
/tools
```

主循环不再为每个命令维护独立 `if startswith` 分支。

### 6. 工具命令从 ToolRegistry 自动生成

CLI 遍历：

```python
tool_registry.registrations()
```

只要 RegisteredTool 提供 `CliCommandSpec`，对应命令就会自动加入 CLI Registry。旧 `TOOL_COMMANDS` 常量被删除。

### 7. 工具参数从 args_schema 推导

旧代码按工具名称硬编码：

```text
read_project_file -> path
search_project_text -> query
```

新规则为：

```text
零字段 schema：命令不接受参数
单字段 schema：剩余文本作为该字段值
多字段 schema：剩余文本必须是 JSON 对象
```

参数通过 Pydantic args_schema 校验后才调用工具。未来 MCP 工具无需在 CLI 中增加名称判断。

### 8. 工具确认策略进入执行流程

如果 `ToolPolicy.requires_confirmation=true`，CLI 会在调用前要求用户确认。工具安全策略和 Agent/CLI 使用同一注册信息。

### 9. Interrupt 与调试输出独立

`interrupts.py` 负责：

```text
提取 LangGraph interrupt
构造记忆审核决定
调用 runtime.resume
```

`debug.py` 只负责 Graph 返回状态的诊断展示。未来 Web UI 可以替换 CLI 交互，而无需复制主循环。

## 为什么这样设计

CLI 是一种用户界面，不应该成为第二个业务服务层。记忆规则属于 MemoryService，工具规则属于 ToolRegistry，Graph 恢复属于 Runtime。

命令系统只负责把用户输入解析为对这些服务的调用。这样新增 UI、MCP 工具或内置命令时，不需要继续扩大同一个文件。

## 对成熟项目的意义

1. 命令精确匹配，消除前缀误判。
2. 工具命令随 ToolRegistry 自动出现。
3. 工具参数由 args_schema 统一校验。
4. CLI 主循环职责缩小，更容易测试。
5. Interrupt 处理可被未来 Web/API 适配器替换。
6. 输入模式和调试状态进入明确上下文。
7. 公共 `human_chat.cli` 导入路径保持兼容。

## 测试覆盖

新增测试验证：

```text
命令只匹配完整 token
记忆命令通过 Context 调用 MemoryService
工具命令由 ToolRegistry 自动生成
零参数和单参数工具可以通用执行
工具参数使用 Pydantic schema 校验
debug 命令更新 Context 状态
```

## 本轮六阶段重构完成情况

本轮已经完成：

```text
Session 模型与 Repository 收口
Checkpointer 和 Runtime 生命周期管理
MemoryRepository item 语义统一
LangGraph Store 正式接入和迁移入口
ToolRegistry 与 Provider 统一注册
CLI 命令系统拆分
```

这些修改完成的是所审核第 6 至 13 点的架构重构，不代表整个 HumanChat 项目已经完成。工具安全边界、标准 LangGraph 消息流和 Graph 节点拆分仍属于下一轮需要继续处理的高优先级内容。
