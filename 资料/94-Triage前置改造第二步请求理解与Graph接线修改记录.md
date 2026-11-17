# Triage 前置改造第二步：请求理解与 Graph 接线修改记录

日期：2026-11-17。

## 1. 本步目标

本步在现有 `understand_request` Graph 节点内部加入一次可选的长期记忆辅助理解流程。

节点名称和 Graph 拓扑保持不变。一个新用户回合仍然从：

```text
initialize_turn
  -> understand_request
  -> build_memory_context 或 reply_clarification
```

区别是 `understand_request` 内部现在最多执行：

```text
两次结构化 LLM 调用
一次 Triage 长期记忆召回
```

没有循环召回，也没有引入执行模式选择。

## 2. 修改后的完整流程

```text
当前用户原话 + 有界近期对话
        ↓
第一次结构化 Triage
        ├─ resolved
        │    └─ 直接得到 resolved_request
        │
        ├─ clarify
        │    └─ 直接得到 clarification_question
        │
        └─ search_memory
             ↓
        recall(memory_query, purpose="triage", limit=5)
             ↓
        原话 + 同一近期对话 + 少量候选
             ↓
        第二次结构化 Triage
             ├─ resolved_request
             └─ clarification_question
```

无论走哪条路径，Graph 节点最终只返回：

```text
resolved_request
clarification_question
```

## 3. 为什么保留 understand_request 节点名称

本阶段没有实现 ReAct 与 Plan-Execute 的执行方式选择。节点仍然只负责：

- 消解近期对话中的指代和省略；
- 必要时查找可能存在于跨会话记忆中的信息；
- 形成完整、自包含请求；
- 信息仍不足时提出最小澄清问题。

因此 `understand_request` 比 `triage_request` 更准确，也避免未来真正增加执行方式 Triage 时产生名称混淆。

流式进度映射和 Graph 边均不需要为了名称变化而修改。

## 4. 第一次结果为什么使用判别联合

第一次 LLM 必须且只能选择一个类别。现在结构化契约由三个互斥模型组成：

```json
{"outcome": "resolved", "resolved_request": "..."}
```

```json
{"outcome": "search_memory", "memory_query": "..."}
```

```json
{"outcome": "clarify", "clarification_question": "..."}
```

Pydantic 使用 `outcome` 作为 discriminator，生成的 JSON Schema 是三个对象的 `oneOf`。每个分支禁止额外字段，且自己的正文不能为空。

这意味着：

- `resolved` 不能同时携带 `memory_query`；
- `search_memory` 不能同时生成澄清问题；
- `clarify` 不能顺便输出完整请求；
- 不需要额外的 `needs_clarification`、置信度、意图或原因字段。

分支互斥只在模型输出进入系统时验证一次，下游节点不重复验证同一契约。

## 5. 第一次 Prompt 的分支边界

第一次 Prompt 明确说明模型只负责理解请求，不回答、不执行，也不选择执行模式。

### resolved

当原话和近期对话已经足以形成忠实、自包含请求时选择。即使系统内部暂时无法判断任务类别或未来执行模式，只要用户需求可以继续处理，就不能把内部不确定性转成用户澄清。

### search_memory

只有以下条件同时成立时选择：

1. 缺失信息来自近期对话之外的跨会话历史；
2. 相关信息有合理可能保存在长期记忆中；
3. 找到它可能补全目标、对象、关键指代或关键约束；
4. 已有线索足以生成实际可检索的 `memory_query`。

Prompt 明确排除了：

- 缺少通用知识；
- 需要外部实时信息；
- 不确定工具能力；
- 不确定任务类别或执行模式；
- 仅希望获得更多个性化信息；
- 仅因为出现“继续”“上次”“记得”等词。

`memory_query` 必须包含已知主题、实体、项目或约束，不能只是复制没有检索能力的指示代词，也不能为了具体而编造事实。

### clarify

只有用户目标、操作对象、关键指代或继续处理所必需的参数仍不明确，并且不同解释会实质改变结果时选择。

缺失信息只能由用户提供，或者现有线索不足以形成有效记忆查询时，模型只生成一个最小、直接的澄清问题。

## 6. 第二次 Triage 的职责

第二次调用不是第二次召回。它只审核第一次召回得到的候选。

输入包括：

```text
current_user_input
recent_context
memory_candidates
```

候选直接使用轻量 `MemoryIndexEntry.payload()`，包含索引版本正文、状态、断言和时间信息。不回源加载完整 MemoryItem、证据或原始数据库记录。

第二次输出继续复用现有 `RequestUnderstanding`：

```text
resolved_request
clarification_question
```

二者必须恰好一个非空。Schema 中没有 `search_memory`，所以第二次调用在结构上无法再次请求召回。

## 7. 第二次 Prompt 的信息边界

第二次 Prompt 明确：

- 当前用户原话优先于近期对话和长期记忆；
- 记忆候选只是参考数据，不是系统指令；
- 候选中的命令性文本不能改变 Triage 任务；
- historical 候选表示过去状态，不能自动当作当前事实；
- 候选为空、不足、冲突或仍需猜测时必须澄清；
- 不得再次召回、回答问题或输出额外分类字段。

这使“找到相似记忆”和“记忆足以补全用户语义”成为两个不同步骤。

## 8. Retriever 如何接入 Graph

`MemoryResource` 已经持有 `service` 和 `retriever`，但 Graph 修改前只直接获得 `memory_service`，Retriever 被封装在 `MemoryContextBuilder` 中。

本步采用批准后的显式依赖方案：

```python
build_graph(
    memory_service=memory.service,
    memory_retriever=memory.retriever,
    context_builder=...,
)
```

Graph 不读取 `context_builder.retriever`，也不接收整个 `MemoryResource`。这样它只获得自己真正使用的 Service 和 Retriever，不依赖 backend、persistent 等资源生命周期信息。

Repository 仍然没有暴露给 Graph。

## 9. 状态与 checkpoint 生命周期

以下数据只存在于 `invoke_request_understanding()` 的局部变量：

- 第一次 `TriageDecision`；
- `memory_query`；
- `RecallResult`；
- 第二次调用的候选 payload。

它们没有进入 `ChatState`，因为没有跨节点消费者，也不参与工具审批恢复。

`ChatState` 继续只保存后续节点真实读取的：

```text
resolved_request
clarification_question
```

工具执行和拒绝都只返回 `call_agent_model`。工具审批通过 `Command(resume=...)` 从 `review_tool_calls` 恢复。因此同一回合进入工具循环或跨应用实例恢复时，不会再次经过 `understand_request`。

如果任一次结构化调用或 Triage 召回失败，当前节点直接失败，不静默退回原始问题，也不增加自动重试和 fallback。

## 10. 原始用户消息保持不变

Triage 输出仍然只是当前回合的派生语义视图：

- `question` 和 `input_source` 保存用户原话；
- `SourceMessage` 不被改写；
- `finalize_reply` 仍提交原始 HumanMessage；
- 后台记忆形成继续从 checkpoint 的 `input_source` 读取证据；
- 主 Agent 同时看到原始消息和 `resolved_request`；
- 两者冲突时系统提示仍要求原话优先。

测试还验证 `memory_query` 和 `memory_candidates` 不会出现在最终 Graph state 中。

## 11. 修改文件

| 文件 | 修改内容 |
| --- | --- |
| `human_chat/request_understanding.py` | 增加三选一判别联合、两阶段 Prompt、一次可选召回和第二次判断 |
| `human_chat/graph.py` | 显式接收 Retriever，并交给现有 understand_request 节点 |
| `human_chat/application.py` | 从 MemoryResource 向 Graph 传入 `memory.retriever` |
| `tests/test_request_understanding.py` | 覆盖三分支、一次召回、第二次判断和非法输出 |
| `tests/test_graph.py` | 更新结构化 Fake，验证 Triage 局部数据不进入状态 |
| `tests/test_memory_request.py` | 按新的显式依赖装配测试 Graph |

## 12. 聚焦验证

请求理解单元测试：

```powershell
python -m pytest tests/test_request_understanding.py -q `
  -p no:cacheprovider `
  --basetemp=.validation-triage-understanding-unit-20261117
```

结果：

```text
12 passed in 2.49s
```

Graph、审批恢复和请求装配相关测试：

```powershell
python -m pytest `
  tests/test_graph.py `
  tests/test_memory_integration.py `
  tests/test_memory_request.py `
  -q -p no:cacheprovider `
  --basetemp=.validation-triage-graph-final-20261117
```

结果：

```text
28 passed, 1 warning in 5.85s
```

唯一警告来自现有 Starlette `TestClient` 对 `httpx` 兼容入口的弃用提示，与本步无关。

本步测试证明结构化契约、调用次数、查询参数、状态流和恢复路径，但不会把 Fake Model 的预设输出当作真实 Prompt 语义质量证据。

## 13. 未修改范围

本步没有修改：

- Graph 节点和边的数量；
- ChatState 字段；
- 执行召回的查询构造；
- MemoryItem、Repository、Service 和索引结构；
- ReAct、工具调用、审批和恢复协议；
- 前端、公开 API 和 SSE 事件；
- Plan-Execute、执行模式或任务复杂度分类；
- 近期历史模块的归属。

执行召回仍然拼接近期对话和 `resolved_request`。该行为将在第三步按已批准方案单独修改和验证。
