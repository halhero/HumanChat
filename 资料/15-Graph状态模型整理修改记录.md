# 15 Graph 状态模型整理修改记录

## 本次目标

本次改造对应 `资料/11-框架化改造盘点.md` 中的第五项：Graph 节点与状态模型框架化。

本次先处理一个明确的状态重复问题：`assistant_text` 和 `tts_text` 语义重复。

## 修改前的问题

`ChatState` 中同时存在：

```text
assistant_text
tts_text
```

而 `generate_reply` 节点返回时，二者内容完全相同：

```python
"assistant_text": response.text
"tts_text": response.text
```

这会造成几个问题：

```text
同一份回复文本有两个字段名
后续节点不知道应该依赖哪个字段
CLI 需要做 fallback 判断
未来如果 TTS 文本和助手文本分离，会更难追踪来源
```

## 本次怎么改

### 1. 移除 tts_text 状态字段

从 `ChatState` 中删除：

```python
tts_text: str = ""
```

### 2. generate_reply 只返回 assistant_text

删除：

```python
"tts_text": response.text
```

保留：

```python
"assistant_text": response.text
```

### 3. TTS 节点消费 assistant_text

当前 `synthesize_speech` 已经使用：

```python
state.assistant_text
```

所以不需要额外改动。

### 4. CLI 和 Runtime 统一读取 assistant_text

CLI 展示助手回复时只读取：

```python
result.get("assistant_text", "")
```

自动记忆提取也只读取：

```python
result.get("assistant_text", "")
```

## 为什么这样做

Graph 状态应该尽量避免“多个字段表达同一件事”。

现在统一为：

```text
assistant_text：助手最终回复文本
tts_error：语音输出错误
```

TTS 是输出副作用节点，不应该拥有一份独立的回复文本状态。

## 后续计划

后续进一步整理状态时，可以继续处理：

```text
question 是否改为 HumanMessage 输入
tool_messages 是否只作为 scratch 状态
memory_prompt 是否只在节点内部派生
```

这些改动可以和 checkpointer / ToolNode 更深度集成时一起推进。

