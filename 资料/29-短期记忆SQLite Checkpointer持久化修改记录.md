# 29 短期记忆 SQLite Checkpointer 持久化修改记录

## 本次目标

本次目标是把短期对话记忆从“进程内可用”推进到“可跨进程恢复”。

之前项目已经把 LangGraph graph 编译时接入了 checkpointer：

```python
graph.compile(checkpointer=checkpointer)
```

并且 Runtime 会把当前会话 ID 作为 `thread_id` 传给 LangGraph：

```python
{"configurable": {"thread_id": self.thread_id}}
```

但 `create_checkpointer()` 仍然默认创建内存 checkpointer。这样会导致一个问题：

```text
同一次进程运行中能记住上下文
程序重启后短期对话状态消失
```

这不符合成熟 Agent 项目对短期状态恢复、interrupt/resume、工具调用状态延续的要求。

## 修改了什么

### 1. 配置新增 `checkpoint_path`

`Settings` 新增：

```python
checkpoint_path: Path = PROJECT_ROOT / "data" / "checkpoints" / "langgraph.sqlite"
```

对应环境变量：

```env
HUMANCHAT_CHECKPOINT_PATH="data/checkpoints/langgraph.sqlite"
```

这样 checkpoint 文件位置不再硬编码，后续可以按环境切换。

### 2. `create_checkpointer(settings)` 优先使用 SQLite

修改后：

```python
self.checkpointer = checkpointer or create_checkpointer(settings)
```

`create_checkpointer()` 会优先尝试：

```python
from langgraph.checkpoint.sqlite import SqliteSaver
```

如果依赖存在，就使用 SQLite checkpoint 文件。

如果依赖不存在，就降级为内存 checkpointer，并写日志说明状态不会跨进程保留。

### 3. 依赖增加 `langgraph-checkpoint-sqlite`

`requirements.txt` 新增：

```text
langgraph-checkpoint-sqlite
```

这是 LangGraph SQLite checkpointer 的官方配套包。

## 为什么这样做

### 1. 短期记忆属于 Graph 状态

短期对话上下文、工具调用中间状态、interrupt 暂停点，本质上都属于 LangGraph graph state。

成熟项目不应该由 CLI 或 Runtime 手写保存这些状态，而应该交给 LangGraph checkpointer。

### 2. SQLite 是合适的本地开发后端

SQLite 的优点是：

```text
不需要额外数据库服务
适合本地开发
可以跨进程保留状态
迁移到 Postgres 前成本低
```

所以当前阶段先使用 SQLite，比直接上 Postgres 更符合项目阶段。

### 3. 保留降级能力

如果运行环境暂时没有安装 `langgraph-checkpoint-sqlite`，项目不会因为缺依赖直接崩溃，而是降级到内存 checkpointer。

这对早期项目很重要，因为它能降低配置失败时的排查成本。

## 怎么做

核心代码在 `human_chat/checkpointing.py`：

```python
def create_checkpointer(settings: Settings | None = None):
    if settings is not None:
        sqlite_checkpointer = _create_sqlite_checkpointer(settings.checkpoint_path)
        if sqlite_checkpointer is not None:
            return sqlite_checkpointer

    return _create_memory_checkpointer()
```

SQLite 创建流程：

```python
path.parent.mkdir(parents=True, exist_ok=True)
connection = sqlite3.connect(str(path), check_same_thread=False)
checkpointer = SqliteSaver(connection)
```

如果 checkpointer 暴露 `setup()` 方法，则主动调用：

```python
setup = getattr(checkpointer, "setup", None)
if callable(setup):
    setup()
```

这样兼容不同版本的 LangGraph checkpointer 实现。

## 对成熟项目的意义

### 1. 为 interrupt/resume 打基础

LangGraph human-in-the-loop 依赖 checkpointer 保存暂停点。

如果 checkpointer 只是内存态，那么程序重启后无法继续之前的人工确认流程。

### 2. Runtime 职责更薄

Runtime 只负责调用 graph，不再承担短期记忆持久化细节。

### 3. 状态来源更清晰

短期状态来自 checkpointer。

会话 JSON 只保存元数据。

长期记忆由 MemoryStore / Repository 管理。

三者边界更清楚。

## 当前完成程度

本次完成：

```text
新增 HUMANCHAT_CHECKPOINT_PATH
新增 SQLite checkpointer 优先创建逻辑
保留内存 checkpointer 降级逻辑
Runtime 改为把 Settings 传给 create_checkpointer
requirements.txt 增加 langgraph-checkpoint-sqlite
README 和 .env.example 同步配置说明
```

本次没有做：

```text
测试覆盖
Postgres checkpointer
历史内存 checkpointer 数据迁移
```

