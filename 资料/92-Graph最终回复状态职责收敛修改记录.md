# Graph 最终回复状态职责收敛修改记录

日期：2026-11-17。

## 1. 本次修改解决什么问题

本次修改只处理 Graph 内部两个已经确认的问题：

1. 近期上下文辅助函数的命名没有清楚表达其内部辅助性质；
2. 最终助手回复依赖 `tool_messages[-1]`，导致普通回复、工具后的最终回复和澄清回复都被混入 ReAct 工具轨迹。

此前的主要数据流是：

```text
普通回复 / 工具后的回复 / 澄清回复
  -> 先放入 tool_messages
  -> finalize_reply 读取 tool_messages[-1]
  -> 提取文本写入 assistant_text
  -> 提交到 messages 和流式接口
```

这种实现虽然能够返回结果，但状态职责不清楚：

- `tool_messages` 既保存工具协议消息，又临时充当最终回复容器；
- `finalize_reply` 只能根据轨迹最后一条消息猜测本轮最终回复；
- 澄清路径没有执行工具，却被迫构造一段假的工具消息序列；
- `assistant_text` 已经是接口层读取的最终回复字段，却只在最后一步才从另一份状态中派生出来。

本次没有增加新字段，而是让现有 `assistant_text` 承担它已经适合承担的职责。

## 2. 修改后的状态职责

Graph 中与本次修改直接相关的三份状态现在分别承担明确职责：

| 状态 | 职责 |
| --- | --- |
| `tool_messages` | 保存 ReAct 执行所需的用户输入、AI tool call 和对应 ToolMessage |
| `assistant_text` | 保存本回合唯一、权威的最终助手回复文本 |
| `messages` | 保存已经由 `finalize_reply` 正式提交到会话历史的用户消息和助手消息 |

修改后的统一流程是：

```text
最终回复生产节点
  -> assistant_text
  -> finalize_reply
  -> 原始用户消息 + 最终助手消息
  -> messages / SSE message.completed
```

`finalize_reply` 不再读取 `tool_messages[-1]`，也不再从工具轨迹推断哪条消息应该展示给用户。

## 3. 内部辅助函数命名调整

原函数：

```python
bounded_recent_context(state)
```

修改为：

```python
_build_recent_context_payload(state)
```

增加下划线是为了明确它是 `build_graph()` 内部辅助函数，不是 LangGraph 节点。`build` 和 `payload` 则说明它负责把有界近期消息转换成请求理解和记忆上下文共同使用的字典载荷。

本次只更新 `understand_request` 和 `build_memory_context` 两个真实调用方，没有保留旧名称转发层，也没有改变历史选择、token 预算、参数或返回值语义。

## 4. 普通 Agent 回复如何处理

`call_agent_model` 现在根据模型是否返回 tool call 区分两种结果。

### 4.1 模型请求调用工具

如果响应包含 `tool_calls`：

```text
AI tool-call message -> tool_messages
```

它继续进入工具执行或工具审批流程。即使该响应同时带有一些自然语言文字，也不能提前把这些文字当作最终回复，因为工具任务还没有完成。

### 4.2 模型已经给出最终回答

如果响应不包含 `tool_calls`：

```text
模型响应文本 -> assistant_text
```

最终 AI 响应不再追加到 `tool_messages`。路由看到 `assistant_text` 后进入 `finalize_reply`，由统一提交节点创建正式的助手消息。

因此，不调用工具的普通回复不会再产生工具轨迹；经过工具调用后的最终自然语言回答也不会与 tool call、ToolMessage 混在一起。

## 5. 澄清路径如何处理

旧的 `reply_clarification` 会构造：

```text
tool_messages = [原始 HumanMessage, 澄清 AIMessage]
```

这段数据并不是工具轨迹，只是为了满足 `finalize_reply` 从最后一条工具消息取文本的旧实现。

现在 `reply_clarification` 只写入：

```python
{"assistant_text": state.clarification_question}
```

随后继续复用原有 `finalize_reply`。原始用户消息仍从 `input_source` 创建并提交，不会因为移除假的工具消息而丢失。

最终路径保持为：

```text
understand_request
  -> reply_clarification
  -> finalize_reply
  -> finish_turn
```

它仍然跳过记忆上下文构建、主 Agent、ToolNode 和工具审批。用户回答澄清问题后，仍会作为新的用户回合重新经过请求理解。

## 6. 工具调用与上限回复如何处理

正常工具循环继续在 `tool_messages` 中保存协议完整的轨迹：

```text
HumanMessage
  -> AIMessage(tool_calls)
  -> ToolMessage
  -> 再次调用 Agent
```

再次调用 Agent 得到无 tool call 的最终回答后，该文本写入 `assistant_text`，而不是追加到轨迹。

达到工具调用上限时，系统仍然会为每个尚未执行的 tool call 创建对应的 ToolMessage。这个步骤不能删除，因为 LangChain/OpenAI 工具协议要求每个 tool call 都有匹配结果。之后由非工具绑定模型生成的最终说明写入 `assistant_text`。

因此本次修改只分离“内部执行轨迹”和“最终用户回复”，没有重写现有 ReAct 循环。

## 7. 工具审批和恢复为什么不受影响

工具审批中断发生时，checkpoint 中保存的是：

```text
tool_messages = [HumanMessage, AIMessage(tool_calls)]
assistant_text = ""
```

用户批准或拒绝后，Graph 从原来的中断节点恢复：

```text
approve / reject
  -> ToolMessage
  -> call_agent_model
  -> assistant_text
  -> finalize_reply
```

恢复路径不会重新经过 `initialize_turn` 和 `understand_request`，所以：

- 请求理解仍然只执行一次；
- 原始 tool call 仍在 checkpoint 中；
- ToolNode 仍然读取相同的 `tool_messages`；
- 前端审批协议和 `Command(resume=...)` 没有变化；
- 最终回复仍通过原有 `message.completed` 事件发送。

## 8. 为什么继续使用 assistant_text

本次没有新增 `final_reply`、`reply_message` 等平行状态，也没有重命名 `assistant_text`。

原因是现有 `assistant_text` 已经具有真实消费者：

- `ConversationService` 从 `finalize_reply` 更新中读取它；
- SSE 的 `message.completed` 使用它作为助手消息正文；
- Graph 测试和调用结果也以它表示本轮最终文本。

问题不在字段名称或类型，而在于旧实现把它放在流程末端从 `tool_messages` 二次推导。现在只是把它提前到真正产生最终回复的节点写入，并让 `finalize_reply` 直接消费这个权威值。

## 9. 修改文件

| 文件 | 修改内容 |
| --- | --- |
| `human_chat/graph.py` | 重命名内部上下文辅助函数；分离工具轨迹和最终回复；让澄清、普通回复、工具后回复及上限回复统一生产 `assistant_text` |
| `tests/test_graph.py` | 验证普通回复和澄清不再写入工具轨迹，工具后的最终回复不混入 ReAct 消息，并覆盖工具调用上限回复 |
| `tests/test_memory_integration.py` | 加强跨应用实例工具审批恢复断言，确认恢复后提交正确的用户消息和最终助手回复 |

## 10. 聚焦验证

首先运行三条 Graph 路径和审批恢复测试。Graph 测试通过，但审批恢复测试在创建系统默认 pytest 临时目录时遇到 Windows 权限错误，测试尚未进入业务代码。随后指定仓库内独立 `--basetemp` 并禁用 pytest cache，重新执行同一组测试：

```powershell
python -m pytest `
  tests/test_graph.py::test_direct_reply_reuses_first_model_response `
  tests/test_graph.py::test_clarification_is_committed_without_memory_agent_or_tools `
  tests/test_graph.py::test_tool_result_stays_in_same_model_conversation `
  tests/test_memory_integration.py::test_http_tool_review_restart_preserves_tool_only_contract `
  -q -p no:cacheprovider `
  --basetemp=.validation-final-reply-state-20261117
```

结果：

```text
4 passed, 1 warning in 2.51s
```

然后运行本次状态流直接影响的两个测试文件：

```powershell
python -m pytest `
  tests/test_graph.py `
  tests/test_memory_integration.py `
  -q -p no:cacheprovider `
  --basetemp=.validation-final-reply-state-final-20261117
```

结果：

```text
14 passed, 1 warning in 3.67s
```

唯一警告来自现有 Starlette `TestClient` 对 `httpx` 兼容入口的弃用提示，与本次修改无关。

## 11. 明确未修改范围

本次没有修改：

- `recent_history()`、`recent_history_before()` 及其模块归属；
- 历史 token 预算和配置；
- `ChatState` 字段结构；
- 长期记忆模型、召回、索引和持久化；
- ReAct 工具执行和审批协议；
- 前端、公开 API 和 SSE 事件结构；
- 此前全项目冗余审查中的其他待处理问题。

通用历史选择能力目前仍位于 memory 专用模块。该问题在项目中属于更普遍的模块职责归属问题，已经明确延期，后续应在完成全项目依赖审查后统一制定方案，不能只移动单个函数。
