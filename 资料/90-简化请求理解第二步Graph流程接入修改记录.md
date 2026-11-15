# 简化请求理解第二步：Graph 流程接入修改记录

日期：2026-11-15。

## 1. 本步完成的行为

请求理解现在进入每个新用户回合的 Graph 前部：

```text
START
  -> initialize_turn
  -> understand_request
  -> clarification?
       是 -> reply_clarification -> finalize_reply -> finish_turn
       否 -> build_memory_context -> call_agent_model
            -> 现有工具/审批循环 -> finalize_reply -> finish_turn
```

原有 ReAct 工具循环、工具审批节点和 `Command(resume=...)` 恢复路径没有重写。

## 2. 为什么拆分 prepare_context

旧 `prepare_context` 同时负责：

- 规范化 `input_source`；
- 清空回合临时工具状态；
- 选择近期历史；
- 构造长期记忆上下文。

请求理解必须发生在记忆召回之前，因此这些职责不能继续留在同一个节点。现在拆为：

### initialize_turn

只规范化原始输入来源并重置本回合临时状态，包括：

```text
resolved_request
clarification_question
memory_context
tool_messages / tool review / tool count
assistant_text
```

它不调用模型，也不召回记忆。

### understand_request

调用第一步建立的唯一请求理解边界，写入：

```text
resolved_request
clarification_question
```

### build_memory_context

只有不需要澄清时才执行，并把 `resolved_request` 作为当前检索请求交给既有 `MemoryContextBuilder`。长期记忆的索引、混合召回、适用性选择和预算逻辑均未修改。

## 3. 原话与派生请求的数据流

用户原话仍由 `SourceMessage` 保存，并在 `finalize_reply` 中作为真正的 `HumanMessage` 写入消息历史：

```text
question / input_source / HumanMessage = 用户原话
resolved_request                 = 本回合派生语义视图
```

主 Agent 的输入包含：

1. 原始 `HumanMessage`；
2. system message 中的 JSON 派生请求块。

派生块明确说明它不是第二条用户指令，发生冲突时原话优先。项目没有把 `resolved_request` 伪装成另一条 `HumanMessage`。

记忆后台形成仍只从 checkpoint 中的 `input_source` 读取原始用户内容，因此派生请求不会成为证据文本或引用原文。

## 4. 澄清路径为什么复用 finalize_reply

`reply_clarification` 不调用主 Agent，而是把已经生成的澄清问题包装成内部 `AIMessage`。随后既有 `finalize_reply` 统一完成：

- 提取可显示文本；
- 写入原始用户消息；
- 创建带来源信息的助手消息；
- 进入原有 `finish_turn`。

这样澄清是正常对话回合，不需要 interrupt，不进入工具审批，也没有第二套消息提交逻辑。

## 5. 每个新回合只理解一次

`understand_request` 只有一条入口：

```text
START -> initialize_turn -> understand_request
```

工具执行和拒绝都只返回 `call_agent_model`。工具审批恢复从中断的 `review_tool_calls` 节点继续。因此同一回合的工具循环和恢复不会重新经过 START，也不会再次调用请求理解模型。

## 6. 有界近期历史

请求理解与记忆上下文都使用现有 `memory_history_tokens` 预算。为了避免新增节点导致第二次全历史分组，`recent_history()` 改为从消息尾部按对话组向前读取，并在预算不再容纳下一组时立即停止。

这没有改变所选历史的语义，只改变访问方向和停止时机；没有新增历史状态字段、摘要层或全会话扫描。

## 7. 测试覆盖

本步增加或加强的测试验证：

- 召回器实际收到 `resolved_request`；
- 主 Agent 同时看到原始 HumanMessage 和 system 派生语义块；
- input_source 与最终消息仍保留原话；
- 澄清路径不调用 context builder、主 Agent 或工具；
- 澄清问题通过正常消息提交；
- 非法请求理解输出在进入召回前失败；
- 工具循环和跨进程审批恢复只理解一次；
- 近期历史只访问 token 预算覆盖的尾部。

## 8. 未修改范围

流式协议的节点名称映射从已删除的 `prepare_context` 更新为 `understand_request`，对外仍发送相同的 `thinking / 正在理解你的问题` 事件，不改变公开事件结构。除此之外，本步没有修改公开 API、前端、长期记忆数据模型与数据库、worker/job、召回算法、工具策略或审批协议，也没有引入 TaskState、配置开关、重试和 fallback。
