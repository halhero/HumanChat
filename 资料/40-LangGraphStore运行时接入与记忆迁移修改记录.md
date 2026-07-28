# 40 LangGraph Store 运行时接入与记忆迁移修改记录

## 本次目标

本次修改把 `LangGraphMemoryRepository` 从一个未使用的适配器提升为 Graph 可以正式使用的运行资源，并引入 LangGraph Runtime Context 传递用户身份。

同时保留 JSON 本地兼容模式，增加显式、幂等的 JSON 到 Store 迁移入口。应用启动不会自动迁移或删除用户数据。

## 修改前的问题

虽然项目已经存在 `LangGraphMemoryRepository`，但运行时工厂始终创建 JSON Repository，Graph 编译时也没有传入 Store。

因此之前的状态是：

```text
代码中存在 Store adapter
实际 Graph 永远使用 JSON
user_id 从全局 Settings 闭包读取
没有 Store 数据迁移入口
```

这只能算“为框架化做准备”，不能算真正完成接入。

## 修改内容

### 1. 新增 `MemoryResource`

`human_chat/memory_resources.py` 统一描述长期记忆运行资源：

```text
repository
service
store
backend
persistent
```

Graph、CLI 和 Runtime 由同一个资源获得 Repository 和 Service，不再分别创建后端实例。

### 2. 新增长期记忆后端配置

新增：

```env
HUMANCHAT_MEMORY_BACKEND="json"
HUMANCHAT_MEMORY_POSTGRES_URI=""
```

当前支持：

```text
json：本地兼容模式，跨重启持久化
memory：LangGraph InMemoryStore，测试和开发模式
postgres：LangGraph PostgresStore，商业持久化模式
```

Postgres 依赖采用可选导入。只有选择 postgres 后端时才要求安装数据库包，不增加本地 JSON 用户的启动负担。

### 3. Store 资源使用上下文管理器

`open_memory_resource()` 与 Checkpointer 资源采用相同模式：

```python
with open_memory_resource(settings) as memory:
    ...
```

PostgresStore 的连接生命周期由上下文负责；JSON 和 InMemoryStore 也通过相同入口组装 Service。

### 4. 新增 `AgentContext`

Graph 的运行时上下文定义为：

```python
@dataclass(frozen=True)
class AgentContext:
    user_id: str
```

`ChatRuntime` 每次调用 Graph 时显式传入 context，而不是让节点从全局 Settings 推导当前用户。

这为同一个已编译 Graph 服务多个用户提供正确的数据隔离基础。

### 5. Graph 正式编译 Store

Graph 现在通过：

```python
workflow.compile(
    checkpointer=checkpointer,
    store=store,
)
```

同时声明 `context_schema=AgentContext`。

在 Store 模式下，节点通过 LangGraph 注入的 `runtime.store` 创建 Repository；在 JSON 兼容模式下，使用 Runtime 构建阶段传入的 JSON Repository。

### 6. namespace 按 Runtime 用户生成

记忆 namespace 现在在节点执行时构造：

```text
("users", runtime.context.user_id, "memory")
```

不再把 Graph 编译时的固定用户 ID 永久封进节点闭包。

### 7. CLI 复用当前 MemoryService

`/memory` 命令不再根据 Settings 单独创建 JSON Service，而是使用 `runtime.memory_service`。

这样 Graph 自动保存的记忆和 CLI 管理的记忆始终来自同一个后端、同一个用户 namespace。

### 8. 新增显式迁移入口

执行：

```powershell
python -m human_chat.memory_migration
```

前必须显式设置：

```env
HUMANCHAT_MEMORY_BACKEND="postgres"
HUMANCHAT_MEMORY_POSTGRES_URI="..."
```

迁移按 MemoryItem.id 执行，报告：

```text
copied
updated
skipped
```

重复执行时，相同内容会被跳过，因此迁移是幂等的。

## 为什么这样设计

Checkpointer 保存 thread 内的 Graph 状态，Store 保存跨 thread 的用户长期记忆。两者都属于运行资源，因此都应该由 Runtime 组装层管理，并在 Graph 编译时显式传入。

用户身份属于每次运行的上下文，而不是 Graph 的永久配置。使用 `AgentContext` 后，同一个 Graph 实例才具备服务多个用户的架构可能。

## 对成熟项目的意义

1. LangGraph Store 不再是死代码，而是可选择的正式后端。
2. user_id 进入框架原生 Runtime Context。
3. Graph 与 CLI 共享同一记忆资源。
4. JSON 本地模式不会因商业后端接入而失效。
5. Postgres 连接拥有明确生命周期。
6. 数据迁移显式、可重复、可审计，不在启动时偷偷发生。

## 测试覆盖

新增测试验证：

```text
JSON MemoryResource 的持久化属性
InMemoryStore 运行资源正式写入 LangGraph Store
Runtime 将 user_id 传入 Graph context
JSON 到 Store 迁移第一次复制、第二次跳过
```

## 下一步

下一步建立 ToolRegistry：让 LangChain Tool、治理元数据和 CLI 命令只注册一次，并为本地工具与未来 MCP Provider 提供统一聚合入口。
