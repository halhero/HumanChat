# 26 自动记忆提取 Graph 节点化修改记录

## 本次目标

本次是“记忆模块 LangChain / LangGraph 框架化”的第五步：把自动记忆提取从 `ChatRuntime` 外围逻辑移入 LangGraph。

修改前的流程是：

```text
Graph 生成助手回复
Runtime.ask() 读取 Graph 结果
Runtime.ask() 调用 memory_extractor
Runtime 把 memory_candidates 塞回 result
CLI 展示候选记忆并等待用户确认
```

这能工作，但不够框架化。

自动记忆提取本质上是 Agent 运行流程的一部分，它依赖本轮用户问题和助手回复，也会影响后续长期记忆写入。因此它更适合成为 Graph 节点，而不是藏在 Runtime 外面。

修改后的流程是：

```text
generate_reply
  -> extract_memory
  -> synthesize_speech
```

Runtime 只负责调用 Graph，不再负责记忆提取。

## 修改了什么

### 1. `ChatState` 增加 `memory_candidates`

在 `human_chat/schemas.py` 中新增：

```python
memory_candidates: list[dict[str, Any]] = Field(default_factory=list)
```

这让候选长期记忆成为 Graph 状态的一部分。

### 2. 每轮开始时清空候选记忆

在 `prepare_context` 中新增：

```python
"memory_candidates": []
```

这样候选记忆只属于当前轮次，不会因为 checkpointer 状态跨轮残留。

### 3. Graph 新增 `extract_memory` 节点

在 `human_chat/graph.py` 中新增：

```python
def extract_memory(state: ChatState):
    if not settings.memory_extraction_enabled or not state.assistant_text:
        return {"memory_candidates": []}

    try:
        candidates = extract_memory_candidates(llm, state.question, state.assistant_text)
    except Exception:
        logger.exception("Failed to extract memory candidates")
        return {"memory_candidates": []}

    return {"memory_candidates": [_model_to_dict(candidate) for candidate in candidates]}
```

这个节点负责：

```text
判断是否启用自动记忆提取
读取本轮用户问题和助手回答
调用 LangChain structured output 记忆提取器
把候选记忆写入 Graph 状态
失败时记录日志并返回空候选，不影响正常回复
```

### 4. Graph 流程增加记忆提取节点

修改前：

```text
generate_reply -> synthesize_speech
```

修改后：

```text
generate_reply -> extract_memory -> synthesize_speech
```

这样记忆提取成为 Graph 内部正式阶段。

### 5. Runtime 删除记忆提取职责

`human_chat/runtime.py` 中删除：

```text
create_chat_model
extract_memory_candidates
self.memory_llm
_extract_memory_candidates()
result["memory_candidates"] = ...
```

Runtime 现在只返回 Graph 结果。

这让 Runtime 更薄，职责更清楚。

### 6. 保留 CLI 确认流程

本次没有改变 CLI 的用户确认体验。

CLI 仍然通过：

```python
result.get("memory_candidates", [])
```

读取候选记忆，并让用户确认是否保存。

确认流程结构化会在下一步继续处理。

## 为什么这样做

### 1. 自动记忆提取属于 Agent 流程

自动记忆提取不是普通外围逻辑。

它依赖：

```text
用户输入
助手回复
当前角色
当前模型
长期记忆策略
```

它产出的候选记忆也会影响后续长期记忆系统。

因此它应该在 Graph 中作为节点出现。

### 2. Graph 状态更完整

移入 Graph 后，本轮结果中自然包含：

```text
assistant_text
memory_candidates
tool_events
tts_error
messages
```

这些都是本轮 Agent 执行结果的一部分。

### 3. Runtime 变薄

成熟架构中，Runtime 不应该不断承接 Agent 内部能力。

Runtime 更适合做：

```text
准备 thread_id
调用 app.invoke
保存会话元数据
返回 Graph 结果
```

Graph 内部能力应该留在 Graph。

### 4. 为 LangGraph interrupt 做准备

后续如果要让“保存长期记忆”使用 LangGraph interrupt / human-in-the-loop，候选记忆必须先是 Graph 状态。

如果候选记忆还在 Runtime 外部，就很难自然接入 interrupt。

本次就是为下一步结构化确认流程铺路。

## 对成熟项目的意义

### 1. 流程可视化更完整

Graph 能表达：

```text
生成回复
提取候选记忆
语音合成
结束
```

这比 Runtime 外挂逻辑更容易理解和维护。

### 2. 更适合 tracing / debugging

未来接入 LangSmith 或事件流时，记忆提取节点可以被单独观察。

### 3. 更容易测试

记忆提取成为 Graph 节点后，可以针对 Graph 输出测试 `memory_candidates`，而不是测试 Runtime 私有方法。

### 4. 更容易迁移到 human-in-the-loop

候选记忆已经在 Graph 状态里，下一步可以结构化成 review request，再进一步接 interrupt。

## 当前完成程度

本次完成：

```text
ChatState 增加 memory_candidates
prepare_context 清空本轮 memory_candidates
Graph 新增 extract_memory 节点
Graph 流程改为 generate_reply -> extract_memory -> synthesize_speech
Runtime 删除自动记忆提取职责
CLI 确认流程保持兼容
```

本次没有完成：

```text
记忆确认流程结构化
LangGraph interrupt / human-in-the-loop
自动保存长期记忆
记忆候选编辑能力
```

这些会在下一步继续推进。

