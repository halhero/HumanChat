# 03 Agent 流程能力增强

## 目标

当前 LangGraph 流程是固定的 `chat -> generate_speech`。本阶段要把流程拆成更清楚的节点，让 Graph 真正承担 Agent 编排职责。

## 设计目的

- 将“生成回复”和“语音输出”之外的上下文准备、记忆更新、工具调用入口逐步放入 Graph。
- 让后续功能以节点形式接入，而不是堆在 CLI 或 runtime 中。
- 让流程更容易调试和扩展。

## 计划代码

Graph 可以逐步演进为：

```text
prepare_context
  -> generate_reply
  -> maybe_update_memory
  -> synthesize_speech
```

状态中增加字段：

```python
memory_prompt: str
assistant_text: str
memory_candidates: list[str]
```

第一版先做轻量拆分：

```python
workflow.add_node("generate_reply", generate_reply)
workflow.add_node("synthesize_speech", synthesize_speech)
```

## 完成标准

- Graph 节点命名更贴近职责。
- 回复文本字段统一，避免 `tts_text` 同时承担“回复文本”和“TTS 文本”的语义。
- 后续可自然接入记忆更新和工具节点。

