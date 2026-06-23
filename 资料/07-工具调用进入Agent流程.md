# 07 工具调用进入 Agent 流程

## 当前状态

HumanChat 现在已经有基础项目工具：

```text
/tools
/files
/read human_chat/graph.py
/search memory
```

这些工具由 `human_chat/tools.py` 提供，且限制在项目目录内，只读、相对安全。

但是这些工具目前只能由用户手动输入命令触发。Agent 自己不会判断什么时候需要读取文件、搜索代码或使用工具。

## 要做什么

本阶段要让工具从“CLI 命令”升级为“Agent 可调用能力”。

也就是说，当用户问：

```text
帮我看看 graph.py 现在有哪些节点
```

Agent 应该能判断：

```text
这个问题需要读取项目文件
```

然后调用工具：

```text
read_project_file("human_chat/graph.py")
```

再基于工具结果回答用户。

第一版只接入已有的安全只读工具：

```text
list_project_files
read_project_file
search_project_text
```

## 为什么这么做

一个成熟 Agent 和普通聊天助手的重要区别是：Agent 不只是“凭记忆回答”，还可以主动获取外部上下文。

对当前项目来说，最重要的外部上下文就是项目代码本身。

如果 Agent 能读取项目文件，它就可以：

```text
解释当前代码
辅助定位问题
根据真实文件内容提出修改建议
减少用户手动复制代码
```

这一步会让 HumanChat 开始具备“工作型 Agent”的雏形。

## 怎么做

建议先在 Graph 中加入两个节点：

```text
decide_tool_use
execute_tool
```

流程从：

```text
prepare_context -> generate_reply -> synthesize_speech
```

逐步演进为：

```text
prepare_context
  -> decide_tool_use
  -> execute_tool 或 generate_reply
  -> generate_reply
  -> synthesize_speech
```

状态中可以增加：

```python
tool_request: dict | None
tool_result: str
```

第一版工具决策可以简单一些，让 LLM 输出结构化结果：

```json
{
  "need_tool": true,
  "tool_name": "read_project_file",
  "arguments": {
    "path": "human_chat/graph.py"
  }
}
```

然后由代码层执行工具，而不是让模型直接执行命令。

需要保持安全边界：

```text
只允许访问项目目录内文件
只允许只读工具
工具参数必须校验
工具调用失败要回退为普通回答
```

## 完成标准

这一阶段完成后，应具备：

```text
Agent 能判断是否需要工具
Agent 能读取项目文件回答问题
Agent 能搜索项目文本回答问题
工具结果会进入最终回复上下文
手动 /files /read /search 命令仍然可用
```
