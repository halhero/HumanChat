# 13 短期记忆 Checkpointer 化修改记录

## 本次目标

本次改造对应 `资料/11-框架化改造盘点.md` 中的第二项：短期会话记忆框架化。

目标是让 LangGraph 开始接管会话状态恢复与线程隔离能力，而不是完全依赖 `ChatRuntime` 手动维护 `messages`。

## 修改前的问题

原先 `ChatRuntime` 每轮都会手动传入完整消息历史：

```python
self.app.invoke({
    "question": question,
    "messages": self.messages,
})
```

然后再把返回的 `messages` 写回 session JSON。

这能工作，但属于手动短期记忆管理，没有充分利用 LangGraph 的 checkpoint / thread_id 机制。

## 本次怎么改

### 1. 新增 checkpointer 创建模块

新增：

```text
human_chat/checkpointing.py
```

其中 `create_checkpointer()` 会优先使用：

```python
InMemorySaver
```

如果当前 LangGraph 版本使用旧命名，则回退到：

```python
MemorySaver
```

### 2. Graph 编译支持 checkpointer

`build_graph()` 新增参数：

```python
def build_graph(settings=None, checkpointer=None):
    ...
    return workflow.compile(checkpointer=checkpointer)
```

这样调用方可以选择是否启用 LangGraph checkpoint。

### 3. Runtime 使用 thread_id

`ChatRuntime` 中新增：

```python
self.thread_id = self.session.get("id", "run_once")
self.graph_config = {"configurable": {"thread_id": self.thread_id}}
```

每轮调用改为：

```python
self.app.invoke(graph_input, config=self.graph_config)
```

### 4. 兼容现有 JSON 会话

因为旧会话历史仍然保存在 `data/sessions/*.json`，所以 Runtime 第一次启动旧会话时，会把 JSON 里的 `messages` 作为种子输入到 checkpointer。

之后同一个 Runtime 中的后续调用，只传：

```python
{"question": question}
```

避免把完整历史重复追加到 LangGraph 状态中。

## 为什么不是一步到位删除 JSON messages

这是有意保守的。

当前项目已有会话恢复功能，直接删除 JSON messages 会破坏已有数据。

所以本次是第一阶段：

```text
Graph 开始使用 checkpointer + thread_id
JSON session 继续作为兼容层和元数据层
```

后续可以继续推进：

```text
session JSON 只保存元数据
messages 完全交给 SQLite checkpointer
```

## 修改后的短期记忆流转

当前流程：

```text
CLI 选择 session
  -> ChatRuntime 使用 session["id"] 作为 thread_id
  -> build_graph(..., checkpointer=...)
  -> app.invoke(..., config={"configurable": {"thread_id": session_id}})
  -> LangGraph 管理同一 thread_id 下的状态
```

## 后续注意

本次使用的是内存 checkpointer。它能让代码结构开始对齐 LangGraph，但进程退出后仍需要 JSON 兼容层恢复历史。

后续更成熟的做法是：

```text
使用 SQLite/Postgres checkpointer
session_store 只保存会话元数据
Graph 状态由数据库 checkpointer 持久化
```

