# Triage 前置改造第三步：执行召回查询收敛与回归记录

日期：2026-11-17。

## 1. 本步目标

本步完成 Triage 前置改造的最后一项数据流调整：执行阶段的长期记忆 Retriever 只接收已经完成上下文化的 `resolved_request`，不再把原始近期对话全文拼入检索查询。

近期对话没有被删除。它继续提供给后续 LLM，用于判断召回候选是否适用于当前任务。

## 2. 修改前的真实行为

Graph 已经把 `state.resolved_request` 传给 `MemoryContextBuilder`，但 Builder 内部会重新构造：

```text
近期用户消息
近期助手消息
...
resolved_request
```

然后把整段文本交给 Retriever。

同一份近期对话之后还会进入候选适用性判断 payload。因此它同时参与了：

1. BM25 和向量召回；
2. LLM 对候选的适用性审核。

这种方式在项目还没有请求理解层时有补偿价值。例如原始问题只有“继续”，拼接近期对话可以提供检索主题。

现在 `understand_request` 已经负责把近期指代、省略和必要的跨会话信息整理为自包含请求，继续拼接旧对话会让两个层次重复处理上下文。

## 3. 修改后的执行召回

`MemoryContextBuilder.build(question, recent_context)` 中的 `question` 实际是 Graph 传入的 `state.resolved_request`。

现在 Retriever 直接使用：

```python
recall = self.retriever.recall(question)
```

完整语义如下：

```text
执行检索查询：resolved_request

候选适用性审核：
  resolved_request
  + recent_context
  + recalled candidates
```

## 4. 为什么不再把近期对话拼进查询

### 4.1 避免词面主题污染

FTS/BM25 会使用查询中的词项。近期对话如果同时包含已经结束的 MCP、数据库和工具审批话题，这些词仍可能召回旧主题记忆并占用 Top-K。

### 4.2 避免向量语义混合

embedding 会为整段输入生成一个向量。多段不同主题的近期消息可能把向量拉向多个方向，降低它与当前完整任务的相似度。

### 4.3 建立明确的请求理解契约

`resolved_request` 的职责就是成为忠实、自包含、可独立用于检索和执行的语义视图。如果 Retriever 仍依赖原始近期历史才能工作，这个契约就没有真正成为后续流程的权威输入。

## 5. 为什么近期对话仍进入适用性审核

召回只解决“哪些记录可能相关”，并不等于这些记录当前可以直接使用。

近期对话仍可以帮助审核模型判断：

- 用户是否刚刚否定了旧要求；
- 某条记忆是否属于当前项目或范围；
- historical 候选是否只是过去状态；
- 当前表达是否已经改变了目标；
- 某条约束是否真正适用于本次任务。

所以本步不是丢弃近期对话，而是把它从“候选生成条件”收敛为“候选适用性判断依据”。

## 6. 两种召回用途的最终关系

修改完成后，一个需要跨会话补全的回合可能发生两次不同目的的召回：

```text
Triage 召回
  query   = memory_query
  purpose = triage
  limit   = 5
  作用    = 帮助理解用户在说什么

执行召回
  query   = resolved_request
  purpose = answer（默认）
  作用    = 为回答或执行选择可用记忆上下文
```

两次召回使用相同底层 Retriever，但查询、候选范围和后续审核目的不同。

Triage 候选不会直接复用为执行上下文，也不会写入 ChatState。

## 7. 最终请求理解数据流

```text
原始用户消息 + 有界近期对话
        ↓
understand_request
        ├─ resolved
        ├─ clarify
        └─ search_memory
             ↓
        一次 purpose=triage 的小型召回
             ↓
        resolved 或 clarify

resolved_request
        ↓
purpose=answer 的执行召回
        ↓
resolved_request + recent_context + candidates
        ↓
适用性审核
        ↓
主 Agent / ReAct 工具循环
```

原始用户消息仍然单独保存并进入主 Agent，`resolved_request` 没有替换 SourceMessage、消息历史或长期记忆证据。

## 8. 测试如何证明职责分离

`tests/test_memory_context.py` 现在使用一个完整的 `resolved_request` 和一段包含其他话题的近期上下文。

测试分别确认：

```text
Retriever 实际查询 == resolved_request
```

以及：

```text
适用性 LLM payload 仍包含同一 recent_context
```

这样不是只验证返回结果，而是直接验证数据被送到了哪个组件。

## 9. 修改文件

| 文件 | 修改内容 |
| --- | --- |
| `human_chat/memory/context.py` | 执行 Retriever 只使用传入的完整请求 |
| `tests/test_memory_context.py` | 分别验证 Retriever 查询和 LLM 审核 payload |

## 10. 本步聚焦验证

首先运行 MemoryContextBuilder 测试：

```powershell
python -m pytest tests/test_memory_context.py -q `
  -p no:cacheprovider `
  --basetemp=.validation-triage-execution-query-20261117
```

结果：

```text
1 passed in 2.01s
```

随后运行本阶段直接相关的 Retriever、请求理解、Graph、审批恢复、上下文和请求装配测试：

```powershell
python -m pytest `
  tests/test_memory_retrieval.py `
  tests/test_request_understanding.py `
  tests/test_memory_context.py `
  tests/test_graph.py `
  tests/test_memory_integration.py `
  tests/test_memory_request.py `
  -q -p no:cacheprovider `
  --basetemp=.validation-triage-final-20261117
```

结果：

```text
44 passed, 1 warning in 6.00s
```

覆盖的直接行为包括：

- 三选一结构化 Triage；
- resolved 和 clarify 不触发长期记忆召回；
- search_memory 只召回一次；
- 第二次 Triage 不能继续召回；
- Triage 使用 current/historical、限量 5 且不加入 global scope；
- memory_query 和候选不进入 ChatState；
- 执行召回只使用 resolved_request；
- 近期对话继续进入候选适用性审核；
- 原始用户消息保持不变；
- 普通回答、工具循环和工具上限回复；
- 跨应用实例工具审批恢复不重复请求理解；
- 选中的执行记忆仍能进入主 Agent 请求。

唯一警告来自现有 Starlette `TestClient` 对 `httpx` 兼容入口的弃用提示，与本阶段修改无关。

## 11. 明确未完成的内容

本阶段没有实现：

- ReAct 与 Plan-Execute 路由；
- Planner、Executor、Replanner 或 Verifier；
- Triage 专用模型配置；
- 自动重试、fallback 或循环召回；
- 父记录分组、版本配额和专用重排；
- Triage 候选复用为执行上下文；
- Profile、TaskState、WorkState 或任务数据库；
- 近期历史模块迁移；
- 长期记忆写入、数据模型和索引结构调整。

自动测试证明的是结构化契约、调用次数和数据流。真实模型能否在复杂自然语言中稳定区分 resolved、search_memory 和 clarify，仍需要后续使用有代表性的真实输入进行 Prompt 效果评估。
