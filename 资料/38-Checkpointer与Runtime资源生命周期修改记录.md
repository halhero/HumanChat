# 38 Checkpointer 与 Runtime 资源生命周期修改记录

## 本次目标

本次修改解决两个运行时风险：SQLite Checkpointer 连接没有明确关闭入口，以及一次性调用和持久化会话之间没有清晰的线程与资源边界。

修改后，Checkpointer 由上下文管理器统一打开和关闭，Runtime 不再偷偷创建数据库连接或 Graph。持久化后端降级也从“自动发生”改为“必须显式允许”。

## 修改前的问题

原 `ChatRuntime` 在构造函数中同时执行：

```text
创建 Session Store
创建 SQLite 连接
创建 Checkpointer
编译 Graph
决定 thread_id
执行会话
```

调用者无法知道谁应该关闭 SQLite 连接，也无法在测试中只替换 Graph。旧 `run_once` 还使用固定 `run_once` thread_id，导致不同的一次性请求可能共享持久化 Checkpoint。

## 修改内容

### 1. 新增 `CheckpointerResource`

资源对象公开：

```text
saver
backend
persistent
has_thread(thread_id)
```

`backend` 告诉上层当前是 SQLite 还是内存实现，`persistent` 明确表示进程退出后是否仍能恢复。

### 2. 新增 `open_checkpointer()`

Checkpointer 必须通过：

```python
with open_checkpointer(settings) as checkpoint:
    ...
```

SQLite 使用官方 `SqliteSaver.from_conn_string()` 上下文管理器。退出作用域时数据库连接会被关闭，不再依赖进程结束时的隐式回收。

### 3. 内存降级改为显式策略

新增配置：

```env
HUMANCHAT_CHECKPOINT_BACKEND="sqlite"
HUMANCHAT_CHECKPOINT_ALLOW_MEMORY_FALLBACK="false"
```

默认情况下 SQLite 依赖缺失会直接报错，因为静默使用内存后端会让用户误以为会话可以跨重启恢复。

开发者只有明确打开 fallback 时，系统才会记录警告并使用内存 Checkpointer。

### 4. 新增 `open_chat_runtime()`

Runtime 的完整资源边界变为：

```text
打开 Checkpointer
检查会话对应的 thread_id
同步可恢复状态
使用 saver 编译 Graph
创建 ChatRuntime
执行聊天
退出后关闭 Checkpointer
```

CLI 不再直接构造 `ChatRuntime`，而是在上下文中使用它。

### 5. `ChatRuntime` 改为依赖注入

`ChatRuntime` 现在接收已经编译好的 `app`，不再自行创建 Checkpointer 和 Graph。

这让 Runtime 测试可以传入 `FakeGraph`，不需要 API Key、模型服务或 SQLite 数据库。

### 6. 修复一次性调用隔离

`run_once()` 使用新的 `SessionRecord` 和内存 Checkpointer。每次调用都会获得新的随机 thread_id，不会把上次调用的 Graph 状态带入下一次。

一次性调用本来就不承诺跨进程恢复，因此内存后端比写入共享 SQLite 更符合语义。

### 7. 同步 Session 可恢复状态

打开持久化会话时，系统通过 `checkpointer.get_tuple()` 检查对应 thread_id 是否拥有状态。

如果 Session 元数据声称存在历史，但数据库中没有 Checkpoint：

```text
记录警告
将 recoverable 标记为 false
CLI 明确提示将从空上下文继续
```

不再静默伪装成成功恢复。

### 8. Runtime 保存运行状态

每轮结束后更新：

```text
message_count
updated_at
checkpoint_backend
recoverable
```

SessionRepository 只保存元数据，Graph 消息仍由 Checkpointer 负责，两个持久化职责保持分离。

## 为什么这样设计

资源创建者必须也是资源释放责任的拥有者。把 SQLite 连接隐藏在 Runtime 构造函数里，会让 CLI、测试和未来 Web 服务都无法可靠管理生命周期。

上下文管理器使资源范围在代码结构上清晰可见，也为未来替换 PostgresSaver 或异步 Saver 保留统一入口。

## 对成熟项目的意义

1. 防止重复创建 Runtime 时积累数据库连接。
2. 防止一次性调用共享固定 thread_id。
3. 不再静默丢失可恢复能力。
4. Runtime 可以脱离真实模型和数据库测试。
5. 后端类型和持久化能力进入会话元数据，问题更容易诊断。
6. 为生产环境切换 Postgres Checkpointer 保留明确的资源工厂位置。

## 测试覆盖

新增测试验证：

```text
内存 Checkpointer 明确标记为非持久化
SQLite 上下文退出后连接已关闭
未知后端会被拒绝
Runtime 能保存强类型会话元数据
临时 Runtime 不要求 SessionRepository
```

## 下一步

下一步处理长期记忆 Repository：删除同时存在的聚合读写和 item 读写两套语义，让 JSON 与 LangGraph Store 都围绕单条 MemoryItem 工作。
