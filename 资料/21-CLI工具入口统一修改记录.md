# 21 CLI 工具入口统一修改记录

## 本次目标

本次是“完整工具调用框架化”的第五步，但按照你的要求，本次只做 CLI 工具入口统一，不修改测试文件。

前面四步已经完成：

```text
工具提供层与治理入口
标准 LangGraph 工具循环
工具消息状态隔离
工具错误治理与可观测性
```

但 CLI 中的手动工具命令仍然直接调用底层 Python 函数：

```python
list_project_files(...)
read_project_file(...)
search_project_text(...)
```

这会造成一个问题：Agent Graph 使用的是 `ToolProvider -> LangChain tools -> ToolNode`，而 CLI 调试命令使用的是另一套入口。

本次修改就是把 CLI 的 `/tools`、`/files`、`/read`、`/search` 也接到 `ToolProvider` 上。

## 修改了什么

### 1. `ToolMetadata` 增加 CLI 信息

在 `human_chat/tool_provider.py` 中，`ToolMetadata` 新增：

```python
command: str = ""
usage: str = ""
```

含义是：

```text
command：CLI 中对应的命令，例如 /read
usage：这个命令的用法示例
```

这样 `/tools` 命令展示的内容不再写死在 CLI 里，而是来自工具提供层。

### 2. `ToolProvider` 增加统一查找和调用能力

新增协议方法：

```python
get_tool(name)
get_metadata_by_command(command)
invoke_tool(name, arguments)
```

这样 CLI 不需要知道工具来自哪里，也不需要知道底层函数怎么实现。

CLI 只需要：

```text
根据命令找到工具元数据
把命令参数转换为工具参数
通过 ToolProvider 调用工具
```

### 3. `CompositeToolProvider` 检查重复命令

原来只检查工具名重复。

本次增加 CLI 命令重复检查：

```python
if len(commands) != len(set(commands)):
    raise ValueError("工具命令不能重复。")
```

这对未来接 MCP 或更多工具很重要。

如果两个工具都想占用 `/search`，系统应该在启动阶段直接失败，而不是运行时行为混乱。

### 4. CLI 不再直接导入底层工具函数

修改前：

```python
from human_chat.config import PROJECT_ROOT
from human_chat.tools import list_project_files, read_project_file, search_project_text
```

修改后：

```python
from human_chat.tool_provider import ToolMetadata, create_tool_provider
```

这意味着 CLI 工具命令和 Agent Graph 使用的是同一个工具来源。

### 5. `/tools` 从工具元数据动态展示

修改前：

```python
print("可用工具命令：/files, /read 路径, /search 关键词")
```

修改后：

```python
_print_tool_commands(tool_provider.describe_tools())
```

展示内容包括：

```text
命令
工具描述
工具来源
是否只读
用法
```

这比写死一行命令更适合成熟项目。

### 6. `/files`、`/read`、`/search` 通过统一入口执行

现在 CLI 工具命令会走：

```python
metadata = tool_provider.get_metadata_by_command(action)
arguments = _build_cli_tool_arguments(metadata, ...)
tool_provider.invoke_tool(metadata.name, arguments)
```

这意味着：

```text
/files 调用 list_project_files 工具
/read 调用 read_project_file 工具
/search 调用 search_project_text 工具
```

但 CLI 不再直接依赖底层实现。

## 为什么这样做

### 1. CLI 调试入口应该和 Agent 工具入口一致

如果 CLI 和 Agent 各走一套工具逻辑，就可能出现：

```text
CLI 能读的文件，Agent 不能读
Agent 使用的工具名，CLI 不知道
工具错误处理在 Agent 中生效，但 CLI 不生效
工具元数据更新了，CLI 展示仍然是旧的
```

统一入口后，CLI 看到的就是 Agent 可用的工具。

### 2. 减少重复维护

早期项目中写死 `/files`、`/read`、`/search` 很直观。

但工具越来越多后，如果每新增一个工具都要同时改：

```text
tools.py
tool_provider.py
graph.py
cli.py
README
测试
```

维护成本会很高。

统一到 `ToolProvider` 后，CLI 至少不再需要知道每个工具的底层函数。

### 3. 为 MCP 工具展示做准备

后续如果 MCP 工具接入 `CompositeToolProvider`，理论上 CLI 的 `/tools` 就可以展示不同来源的工具：

```text
local_project
mcp_github
mcp_browser
mcp_database
```

这比在 CLI 里手动维护 MCP 工具列表更合理。

### 4. 工具命令冲突需要提前发现

商业项目里，命令冲突和工具名冲突都会造成排查困难。

例如：

```text
本地工具注册 /search
MCP 搜索工具也注册 /search
```

如果不检查冲突，用户输入 `/search` 时到底调用哪个工具就不清晰。

本次在 `CompositeToolProvider` 中提前检查命令重复，可以让问题在开发阶段暴露。

## 对商业项目的意义

### 1. 工具体系更一致

Agent 自动调用工具和用户手动调试工具，使用同一个提供层。

### 2. 调试体验更接近真实运行

用户通过 CLI 手动调用工具时，调用的是同一套 LangChain-compatible tool。

这让 CLI 更适合作为 Agent 工具调试入口。

### 3. 更容易扩展外部工具

未来接入 MCP、HTTP API、数据库工具时，只要进入 `ToolProvider`，CLI 展示和调用入口也可以逐步复用。

### 4. 更适合权限和审计

工具元数据统一后，后续可以继续加：

```text
角色权限
用户权限
环境开关
危险工具确认
调用日志
```

这些都不应该散落在 CLI 和 Graph 中。

## 当前完成程度

本次完成：

```text
ToolMetadata 增加 CLI 命令和用法信息
ToolProvider 增加工具查找和统一调用方法
CompositeToolProvider 增加命令冲突检查
CLI 工具命令改为通过 ToolProvider 执行
/tools 改为动态展示工具元数据
```

本次没有完成：

```text
测试文件修改
真正接入 MCP 服务器
README 详细重写
工具权限策略落地
```

测试部分是你本次明确要求排除的内容，后续如果需要，可以单独补。

