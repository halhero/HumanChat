# 18 标准 LangGraph 工具循环修改记录

## 本次目标

本次是“完整工具调用框架化”的第二步：把当前一次性工具调用流程，改造成更接近成熟 Agent 的 LangGraph 工具循环。

修改前的流程是：

```text
prepare_context
  -> call_project_tools_model
  -> execute_project_tools 或 generate_reply
  -> generate_reply
  -> synthesize_speech
```

这个流程只支持模型做一次工具判断，最多执行一次工具，然后直接生成最终回复。

修改后的流程是：

```text
prepare_context
  -> call_agent_model
  -> execute_project_tools 或 generate_reply
  -> call_agent_model
  -> execute_project_tools 或 generate_reply
  -> generate_reply
  -> synthesize_speech
```

这意味着模型可以：

```text
先决定是否调用工具
读取工具结果
根据结果继续判断是否还需要工具
直到信息足够后再生成最终回复
```

## 修改了什么

### 1. `ChatState` 增加工具调用计数

在 `human_chat/schemas.py` 中新增：

```python
tool_call_count: int = 0
```

这个字段用于记录当前问题已经执行了几轮工具调用。

### 2. 增加最大工具调用轮数

在 `human_chat/graph.py` 中新增：

```python
MAX_TOOL_CALL_ROUNDS = 3
```

当前每轮用户问题最多允许执行 3 轮工具调用。

这样做是为了避免模型在工具循环中无限调用工具。

### 3. `prepare_context` 重置本轮工具计数

每次用户发起新问题时：

```python
def prepare_context(state: ChatState):
    return {
        "memory_prompt": memory_store.format_for_prompt(),
        "tool_call_count": 0,
    }
```

这样工具调用计数只针对当前问题，不会沿用上一轮对话的计数。

### 4. 将 `call_project_tools_model` 改为 `call_agent_model`

原来的节点只负责判断是否调用项目工具。

现在改为更通用的 Agent 模型节点：

```python
def call_agent_model(state: ChatState):
    conversation = _latest_tool_conversation(state.tool_messages, state.question)
    ...
    response = tool_llm.invoke(conversation)
    return {"tool_messages": updates}
```

它不再只是一次性工具判断，而是工具循环中的模型思考节点。

### 5. 工具执行后回到模型节点

修改前：

```text
execute_project_tools -> generate_reply
```

修改后：

```text
execute_project_tools -> call_agent_model
```

这是本次最关键的变化。

模型执行工具后，会再次看到工具结果，然后决定：

```text
继续调用工具
或者结束工具循环
```

### 6. 条件边判断是否继续工具循环

新的路由逻辑：

```python
if getattr(last_message, "tool_calls", None) and state.tool_call_count < MAX_TOOL_CALL_ROUNDS:
    return "execute_project_tools"
return "generate_reply"
```

也就是说：

```text
如果模型请求工具，并且没有超过调用上限，就执行工具
否则进入最终回复节点
```

### 7. 最终回复只读取当前问题相关的工具上下文

新增：

```python
_build_tool_user_prompt
_latest_tool_conversation
```

`generate_reply` 会先取出当前问题对应的工具消息，再格式化进最终回答 prompt。

这是为了降低旧工具消息对当前问题的影响。后续第 3 步还会进一步整理工具消息状态隔离。

## 为什么这样做

### 1. 成熟 Agent 不是只调用一次工具

一次性工具调用适合很简单的问题，例如：

```text
读取某一个文件
搜索某一个关键词
```

但稍微复杂一点的任务就可能需要多步工具使用：

```text
先搜索入口函数
再读取相关文件
再搜索被调用的类
最后综合回答
```

如果 Agent 最多只能调用一次工具，就会很快遇到能力上限。

### 2. LangGraph 的优势就在于循环和条件边

LangGraph 不是普通函数编排工具。它的核心价值之一就是可以表达：

```text
节点
状态
条件边
循环
中断
恢复
```

工具调用正是最适合用循环表达的 Agent 行为。

这次修改让 HumanChat 的工具调用更接近 LangGraph 的自然使用方式。

### 3. 工具结果需要被模型再次观察

成熟 Agent 的工具调用模式通常是：

```text
模型提出工具调用
工具返回观察结果
模型读取观察结果
模型决定下一步
```

旧流程中，工具返回后直接进入最终结构化回复节点，中间缺少“模型继续观察和判断”的阶段。

这会让工具调用更像一次外部补充材料，而不是 Agent 推理过程的一部分。

### 4. 工具调用必须有上限

商业系统不能允许模型无限调用工具。

原因包括：

```text
成本不可控
响应时间不可控
可能陷入重复调用
外部工具可能有速率限制
用户体验会变差
```

所以本次加入 `MAX_TOOL_CALL_ROUNDS`，先用一个简单上限保护运行安全。

后续可以继续演进为配置项，例如：

```text
不同环境不同上限
不同用户不同上限
不同工具不同上限
高风险工具更低上限
```

## 对商业项目的意义

### 1. 能处理更复杂的真实任务

商业场景的问题往往不是“一次工具调用”能完成的。

例如：

```text
分析一段功能为什么失败
查找多个文件之间的调用关系
比较配置和实现是否一致
根据项目代码生成修改建议
```

这些都需要 Agent 能够连续使用工具。

### 2. 降低未来接 MCP 的阻力

MCP 工具通常数量更多、类型更丰富。

如果 Graph 仍然是一次性工具调用，那么接入 MCP 后也发挥不出 MCP 的价值。

先把 Graph 改成工具循环，未来 MCP 工具加入后，模型才能真正组合使用多个工具。

### 3. 更容易做观测和调优

工具循环结构清晰后，后续可以更自然地记录：

```text
每一轮模型为什么调用工具
每一轮工具返回了什么
模型是否重复调用同一工具
是否触发工具调用上限
```

这些信息对商业项目排查线上问题非常重要。

## 当前完成程度

本次完成：

```text
一次性工具调用改为工具循环
工具执行后回到模型节点
增加本轮工具调用计数
增加最大工具调用轮数
最终回复优先使用当前问题的工具消息
```

本次没有完成：

```text
彻底清理 tool_messages 的跨轮状态
统一工具错误处理格式
CLI 工具入口统一
MCP 工具接入
```

这些会在后续步骤继续处理。

