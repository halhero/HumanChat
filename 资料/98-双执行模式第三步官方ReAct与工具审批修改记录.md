# 双执行模式第三步：官方 ReAct 与工具审批修改记录

日期：2026-11-21。

## 1. 本步的目标

本步用 LangChain `create_agent` 完整取代 HumanChat 原来手写的“模型输出工具请求 -> Graph 节点执行工具 -> 再调模型”循环。

现在普通请求路径是：

```text
build_memory_context
        |
        v
run_react_agent
        |
        v
finalize_reply
```

`run_react_agent` 只负责三件事：

1. 复用现有 `assemble_request` 构建角色、历史、原始用户消息和记忆上下文；
2. 把全新 messages 输入交给进程内只创建一次的 `react_agent`；
3. 从 Agent 最后一条消息提取最终文本，写入权威的 `assistant_text`。

Agent 内部 AI/Tool 消息不回写父 `ChatState`，因此对话历史仍然只保存最终的用户消息和助手回复。

## 2. Agent 为什么在 `build_graph` 中创建

`react_agent` 在 `build_graph` 构建期创建一次，然后被节点闭包捕获。这样做的目的是：

- 不在每个用户回合重复编译 Agent Graph；
- 工具集合与当前运行时 `ToolRegistry` 一致；
- 不新增 Agent factory、manager 或第二层包装；
- 每次 `.invoke()` 仍使用全新 messages，不在不同回合之间暗中积累 Agent 记忆。

子 Agent 不配置自己的 checkpointer 或 store。节点把父 Graph 的 `RunnableConfig` 原样传给 Agent，因此官方 HITL 暂停仍属于同一 thread 的父 checkpoint。

## 3. 工具上限交给官方 middleware

ReAct Agent 的 middleware 只有规格指定的两个：

```python
HumanInTheLoopMiddleware(...)
ToolCallLimitMiddleware(
    run_limit=settings.react_tool_call_limit,
    exit_behavior="continue",
)
```

`continue` 的含义是：超额工具请求被转换为错误 ToolMessage，Agent 仍有机会根据已取得的信息生成最终回复。

因此删除了旧的固定轮数常量、额外限额 Prompt 和专门的“达到上限后再调一次模型”节点。现在只有 `react_tool_call_limit` 一个权威上限，不再保持两套计数逻辑。

## 4. 工具审批如何变化

旧实现由 HumanChat 自己生成：

```json
{"type": "tool_review", "request": {}, "approved": true}
```

现在由 `HumanInTheLoopMiddleware` 生成官方 batched payload，恢复时也必须为每个 action 提供同顺序 decision：

```json
{"decisions": [{"type": "approve"}]}
```

或：

```json
{
  "decisions": [
    {"type": "reject", "message": "用户拒绝执行该工具调用。"}
  ]
}
```

`resume_action_count` 只保存当前 batched 请求的 action 数量，用于恢复时生成等长 decisions。它不暴露到前端 payload。

`tool_review.py` 仍保留真正有用的职责：根据注册表取工具来源与安全属性，递归脱敏参数，构建前端展示批次。旧的 `approved: bool` 解析、手写中断生成和确认检测已删除。

## 5. `ChatState` 为什么现在可以精简

旧状态中的下列字段只为手写 ReAct 循环服务：

```text
tool_messages
tool_call_count
tool_events
tool_limit_reached
tool_review_request
tool_review_approved
```

官方 Agent 已接管内部消息和工具计数，这些字段不再有生产者或消费者，因此与旧节点一起删除。

同时加入规格已确定的六个 Plan 状态字段，并在新回合的 `initialize_turn` 中统一重置。这些字段的真正生产者和路由会在下一个 Plan-Execute 阶段接通。

## 6. 恢复协议的职责边界

- `conversation/protocol.py` 识别 Graph interrupt，并生成稳定的前端展示数据；
- `ConversationService` 根据 review kind 把用户决定转换为 LangGraph 恢复值；
- API route 只传递完整 `ReviewDecisionRequest`，不知道 `Command` 或 middleware 内部结构；
- `HumanChatApplication` 提供高层的 interrupt 转换方法，不向上层暴露 `ToolRegistry`。

后端协议模型已按最终规格允许 `tool | user_input | plan_budget`。本阶段只有 `tool` 会被 ReAct 实际生产；其余两类会随下一阶段的 `ask_user` 和计划额度节点变成可达路径。

## 7. 删除的旧实现

生产代码已删除：

- `call_agent_model`；
- `execute_project_tools`；
- `review_tool_calls` / `reject_tool_calls`；
- `generate_limit_reply` / `mark_tool_limit_reached`；
- `route_after_agent_model` 与相关 edges；
- `ToolNode`、`llm.bind_tools(...)` 和手写 ToolMessage 注入；
- `MAX_TOOL_CALL_ROUNDS` 和 `TOOL_LIMIT_PROMPT`。

`rg` 检查确认这些名称与旧状态在 `human_chat` 生产代码中已无残留。

## 8. 直接验证

```powershell
.\.validation-python-313b\Scripts\python.exe -m pytest \
  tests\test_graph.py::test_direct_reply_reuses_first_model_response \
  tests\test_graph.py::test_tool_result_stays_in_same_model_conversation \
  tests\test_graph.py::test_tool_limit_reply_uses_final_reply_state \
  tests\test_graph.py::test_protected_tool_uses_official_review_resume_protocol \
  tests\test_graph.py::test_cancelled_tool_review_does_not_return_from_checkpoint \
  -q --basetemp=data\pytest-tmp-phase2b
```

结果：`6 passed`。参数化的工具审批用例分别证明：

- `approve` 恢复后真实工具函数只执行一次；
- `reject` 恢复后真实工具函数不执行；
- 两条路径都恢复同一 turn，最终回复只提交一次。

由于本步删除了 Graph 中的整套旧工具路由，又运行了完整 `tests/test_graph.py`，结果为 `15 passed`。

本地 Pytest 缓存目录仍有无法写入警告，测试使用了仓库内已忽略的临时目录，不影响业务结果。

## 9. 本步未扩展的内容

- 没有实现第三种执行模式；
- 没有保留旧 ReAct 兼容开关；
- 没有自定义审批或限额 middleware；
- 没有使用当前 LangChain 版本不存在的 `interrupt_mode` 参数；当前版本默认就是本任务要求的 batched 行为；
- 没有实现 Plan-Execute 节点，下一步会在同一父 Graph 内接入。

