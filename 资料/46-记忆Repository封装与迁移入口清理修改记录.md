# 46 记忆 Repository 封装与迁移入口清理修改记录

## 本次目标

本次修改收紧长期记忆模块的依赖边界，使正常聊天流程中的 Runtime、CLI 和 Graph 只能通过 `MemoryService` 使用长期记忆，不再获得或传递 `MemoryRepository`。

项目当前按单用户 CLI 场景设计，因此复用由 `open_memory_resource()` 创建的固定用户 Service，不提前引入多用户 `MemoryServiceProvider`。

同时删除当前没有真实数据迁移需求的 JSON 到 Postgres 迁移模块，避免为假设中的未来升级长期保留额外入口和 Repository 暴露。

## 修改前的问题

旧的 `MemoryResource` 同时暴露：

```text
repository
service
store
backend
persistent
```

这导致项目存在两条长期记忆业务路径：

```text
CLI
  -> MemoryResource.service

Graph
  -> MemoryResource.repository
  -> build_graph()
  -> Graph 内部重新创建 LongTermMemoryService
```

虽然 Graph 节点最终也通过 Service 进行记忆操作，但 Repository 已经穿过 Runtime 和 Graph 构建边界。上层可以绕过 Service 的规范化、去重、删除和 Prompt 格式化规则。

此外，`LongTermMemoryService` 公开保存：

```python
self.repository
self.namespace
```

调用者能够直接取得 Repository 或依赖 Service 内部 namespace，进一步削弱封装。

## 为什么当前不引入多用户 Service Provider

根据当前项目范围，HumanChat 是单用户 CLI：

```text
memory_user_id 来自固定 Settings
一个 open_chat_runtime() 对应一个 Graph
没有登录系统
没有同一个 Graph 同时处理多个租户请求
```

因此当前只需要一个绑定 `default_memory_namespace(settings)` 的 `MemoryService`。

如果现在增加：

```python
services.for_user(user_id)
```

会为了尚不存在的多用户需求增加 Provider、生命周期和请求上下文复杂度。未来真正引入 Web 账号、多租户并发和单 Graph 复用时，再将固定 Service 升级为按用户创建的 Provider。

## 修改内容

### 1. `MemoryResource` 删除 Repository 字段

资源结构收口为：

```python
@dataclass(frozen=True)
class MemoryResource:
    service: MemoryService
    store: Any | None
    backend: str
    persistent: bool
```

`open_memory_resource()` 内部仍会根据后端创建：

```text
JsonMemoryRepository
LangGraphMemoryRepository
```

但 Repository 只用于构造 `LongTermMemoryService`，不会放入返回给上层的资源对象。

### 2. Service 内部依赖改为私有属性

`LongTermMemoryService` 改为：

```python
self._repository = repository
self._namespace = namespace
```

所有读取、添加、删除和 Prompt 格式化方法都通过私有属性访问持久化层。

Python 下划线不是安全隔离机制，但能够明确模块契约：这两个属性属于实现细节，调用者不应依赖。

### 3. Graph 强制注入 `MemoryService`

`build_graph()` 的长期记忆依赖从：

```python
memory_repository: MemoryRepository | None = None
```

改为关键字必传参数：

```python
*,
memory_service: MemoryService,
```

Graph 不再导入：

```text
MemoryRepository
JsonMemoryRepository
LangGraphMemoryRepository
LongTermMemoryService
LangGraph Runtime Store adapter 构造逻辑
```

`prepare_context` 直接调用：

```python
memory_service.format_for_prompt()
```

`review_memory` 直接调用：

```python
memory_service.add(...)
```

Graph 只描述 Agent 工作流和业务调用，不再负责选择持久化实现或组装 Service。

### 4. Runtime 传递统一 Service

`_build_runtime_graph()` 从传递：

```python
memory_repository=memory.repository
```

改为：

```python
memory_service=memory.service
```

同一个 `MemoryResource.service` 同时提供给：

```text
Graph 节点
ChatRuntime.memory_service
CLI /memory 命令
```

因此自动提取、审核保存、Prompt 注入和手动 CLI 管理使用同一套业务规则与同一后端实例。

### 5. 保留原始 Store 的合理组装用途

`MemoryResource.store` 没有删除，因为 Runtime 组装层仍需把 LangGraph Store 传给：

```python
workflow.compile(store=store)
```

Store 属于框架编译资源，只有 Runtime/Graph 组装边界需要看到它；普通 CLI 和 Graph 业务节点不直接管理 Store。

### 6. 删除记忆迁移模块

删除：

```text
human_chat/memory_migration.py
test_memory_resources.py 中的迁移测试
README 中的迁移命令说明
```

当前没有需要保留的真实旧记忆，项目也尚未形成已经部署的 JSON 到 Postgres 升级路径。提前维护一个通用迁移脚本，会带来以下成本：

1. 为迁移向 `MemoryResource` 暴露 Repository。
2. 维护尚未发生的源数据与目标数据库兼容假设。
3. 未来真实结构变化后仍可能需要重写脚本。
4. README 暗示当前存在已经支持的生产升级流程。

未来真正切换 Postgres 时，应根据当时确定的数据版本、备份策略、校验规则和回滚方案编写一次性迁移工具。

### 7. 测试不再依赖 Service 内部 namespace

`test_memory_service.py` 使用测试自身定义的 `TEST_NAMESPACE` 准备 Repository 数据，不再访问：

```python
service.namespace
```

测试通过公开 Service 方法验证行为，同时仍可直接测试假 Repository 的准备结果。

## 修改后的依赖方向

```text
CLI --------------------+
                        |
Graph ------------------+--> MemoryService
                        |          |
Runtime ----------------+          v
                            MemoryRepository
                                  |
                     +------------+-------------+
                     |                          |
             JsonMemoryRepository   LangGraphMemoryRepository
                                                |
                                    InMemoryStore / PostgresStore
```

Repository 实现由 `open_memory_resource()` 创建并注入 Service，正常业务上层不再获得 Repository 引用。

## 为什么这样设计

Service 是长期记忆业务规则的唯一入口，Repository 是 Service 使用的持久化端口。让 Graph 直接接收 Service 能同时满足：

```text
依赖倒置
业务规则复用
持久化实现隐藏
测试替换能力
单用户场景的实现简洁性
```

本次没有为了形式统一隐藏所有基础设施对象。Store 仍由组装层使用，Repository 协议及其实现仍由 Service 和 Repository 单元测试使用；收口目标是阻止普通业务代码绕过 Service，而不是让类型在整个项目中不可见。

## 对成熟项目的意义

1. CLI 和 Graph 不会出现两套长期记忆规则。
2. Repository 后端选择集中在资源工厂。
3. Graph 构建函数的依赖显式，不再静默创建 JSON fallback。
4. 更换 JSON、内存或 Postgres 后端不会改变业务节点。
5. Service 内部结构不再成为测试和调用者契约。
6. 删除没有真实升级对象的迁移入口，降低当前维护面。
7. 多用户能力可以在真实需求出现时通过 Service Provider 增量引入。

## 测试覆盖

本步骤保留并更新测试验证：

```text
JSON MemoryResource 具有持久化能力且不提供 Store
内存 MemoryResource 使用 LangGraph InMemoryStore
MemoryResource.service 能写入同一个底层 Store
MemoryService 继续完成规范化、去重、删除和 Prompt 格式化
测试不访问 Service 的 Repository 与 namespace 实现属性
```

迁移幂等测试随迁移功能一并删除，不再将不存在的功能作为项目承诺。

## 本步骤涉及文件

```text
human_chat/memory_resources.py
human_chat/memory_service.py
human_chat/graph.py
human_chat/runtime.py
human_chat/memory_migration.py（删除）
tests/test_memory_resources.py
tests/test_memory_service.py
README.md
资料/46-记忆Repository封装与迁移入口清理修改记录.md
```
