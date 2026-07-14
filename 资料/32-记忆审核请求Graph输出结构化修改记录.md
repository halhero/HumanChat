# 32 记忆审核请求 Graph 输出结构化修改记录

## 本次目标

本次目标是让 Graph 直接生成结构化的 `MemoryReviewRequest`，并通过 LangGraph interrupt 把审核动作交给 CLI。

之前流程是：

```text
Graph -> memory_candidates: list[dict]
CLI -> create_memory_review_request(memory_candidates)
CLI -> 用户确认
CLI -> 保存长期记忆
```

这说明结构化审核请求是在 CLI 层才生成的。

更合理的做法是：

```text
Graph -> memory_review_request
Graph -> interrupt(memory_review_request)
CLI / UI -> 只负责呈现和收集决策
Graph resume -> 保存已确认长期记忆
```

## 修改了什么

### 1. `ChatState` 替换输出字段并增加保存结果

修改前：

```python
memory_candidates: list[dict[str, Any]]
```

修改后：

```python
memory_review_request: dict[str, Any] | None = None
memory_saved_count: int = 0
```

Graph state 不再暴露裸候选列表。

### 2. Graph 的 `extract_memory` 节点生成审核请求

修改后：

```python
review_request = create_memory_review_request(candidates)
if not review_request.candidates:
    return {"memory_review_request": None}
return {"memory_review_request": _model_to_dict(review_request)}
```

这意味着 Graph 输出已经是稳定请求对象。

### 3. 新增 Graph 审核节点 `review_memory`

Graph 新增节点：

```python
def review_memory(state: ChatState):
    review_request = parse_memory_review_request(state.memory_review_request)
    decision_data = interrupt(...)
    decision = parse_memory_review_decision(decision_data)
    ...
```

这个节点负责：

```text
读取结构化审核请求
触发 LangGraph interrupt
等待 CLI resume 用户决策
把用户确认的文本写入长期记忆
返回 memory_saved_count
```

### 4. CLI 改为处理 Graph interrupt

修改前：

```python
_confirm_memory_candidates(settings, result.get("memory_candidates", []))
```

修改后：

```python
resume_result = _handle_graph_interrupts(runtime, result)
```

CLI 不再直接写入自动提取的长期记忆，只负责把确认结果 resume 回 Graph。

### 5. 新增解析函数

`memory_review.py` 新增：

```python
def parse_memory_review_request(data: dict | MemoryReviewRequest | None) -> MemoryReviewRequest:
    ...

def parse_memory_review_decision(data: dict | MemoryReviewDecision | None) -> MemoryReviewDecision:
    ...
```

它统一处理：

```text
None
dict
MemoryReviewRequest 实例
```

## 为什么这样做

### 1. Graph 应该输出业务语义，而不是临时结构

`memory_candidates` 只是提取节点的中间结果。

`MemoryReviewRequest` 才是交给外部交互层的业务对象。

### 2. CLI 不应该承担结构转换职责

CLI 的职责是：

```text
展示
读取用户输入
把决策交给业务层
```

不应该负责把模型提取结果转换成审核请求。

### 3. 让 human-in-the-loop 成为 Graph 流程的一部分

记忆写入不再是 CLI 的外围副作用，而是 Graph 节点在 resume 后执行的动作。

这符合 LangGraph human-in-the-loop 的设计方式。

### 4. 为 UI 做准备

未来 Web UI 可以直接渲染 interrupt payload：

```json
{
  "candidates": [...],
  "require_confirmation": true
}
```

## 怎么做

Graph 节点：

```python
def extract_memory(state: ChatState):
    candidates = extract_memory_candidates(...)
    review_request = create_memory_review_request(candidates)
    return {"memory_review_request": _model_to_dict(review_request)}
```

审核节点：

```python
decision_data = interrupt(
    {
        "type": "memory_review",
        "request": _model_to_dict(review_request),
    }
)
decision = parse_memory_review_decision(decision_data)
```

CLI 层：

```python
decision = _prompt_memory_review_decision(review_request)
runtime.resume(_model_to_dict(decision))
```

调试输出也改为：

```text
memory_review_candidates=N
```

## 对成熟项目的意义

### 1. 输出契约稳定

Graph 的输出字段更明确，外部调用方不需要理解内部提取节点。

### 2. 交互层可替换

CLI、Web UI、移动端、interrupt 都可以使用同一个审核请求。

### 3. 长期记忆写入更可控

候选记忆和最终写入之间仍然有明确确认环节。

### 4. 保存动作回到 Graph

自动提取记忆的保存动作由 Graph 节点执行，CLI 不再绕过 Graph 写存储层。

## 当前完成程度

本次完成：

```text
ChatState 增加 memory_review_request
Graph extract_memory 输出 MemoryReviewRequest
Graph review_memory 使用 LangGraph interrupt 暂停并等待确认
Runtime 增加 resume 方法
CLI 改为处理 Graph interrupt 并 resume 用户决策
debug 输出改为 memory_review_candidates
新增 parse_memory_review_request
新增 parse_memory_review_decision
```

本次没有做：

```text
Web UI 审核面板
测试覆盖
```
