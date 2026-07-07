# 24 长期记忆 Repository 与 Store 语义修改记录

## 本次目标

本次是“记忆模块 LangChain / LangGraph 框架化”的第三步：为长期记忆引入 Repository 层和 namespace 语义。

前一步已经把长期记忆从字符串列表升级为结构化 `MemoryItem`。但当前长期记忆仍然本质上绑定在一个文件上：

```text
data/memory/user_profile.json
```

这适合单用户原型，但不适合成熟 Agent 项目。

成熟 Agent 项目的长期记忆通常需要支持：

```text
用户级记忆
项目级记忆
角色级记忆
不同环境的记忆隔离
未来迁移到 LangGraph Store / SQLite / Postgres
```

LangGraph Store 的核心思想是：

```text
namespace / key / value
```

所以本次先不强行替换 JSON 后端，而是在 JSON 后端上方增加 Repository 语义，让代码结构逐步靠近 LangGraph Store。

## 修改了什么

### 1. 新增 `human_chat/memory_repository.py`

新增：

```python
MemoryNamespace = tuple[str, ...]
```

并定义默认 namespace：

```python
def default_memory_namespace(settings: Settings) -> MemoryNamespace:
    return ("users", settings.memory_user_id, "memory")
```

这表示当前默认记忆属于：

```text
users / default / memory
```

### 2. 新增 `MemoryRepository` 协议

新增协议：

```python
class MemoryRepository(Protocol):
    def load_memory(namespace) -> LongTermMemory: ...
    def save_memory(namespace, memory) -> None: ...
    def list_items(namespace) -> list[MemoryItem]: ...
    def put_item(namespace, item) -> None: ...
    def delete_item(namespace, item_id) -> bool: ...
```

它表达了比 `MemoryStore` 更底层、更接近持久化后端的能力。

区别是：

```text
MemoryStore：面向 CLI / Graph 的业务外观层
MemoryRepository：面向存储后端的 namespace/key/value 语义层
```

### 3. 新增 `JsonMemoryRepository`

当前实现仍然使用 JSON 文件：

```python
JsonMemoryRepository(settings.memory_path, namespace)
```

它支持：

```text
load_memory
save_memory
list_items
put_item
delete_item
```

当前 JSON 后端只支持一个 namespace。如果传入不同 namespace，会直接报错。

这样做是有意的：避免让代码看起来已经支持多用户，但实际上所有用户仍写进同一个文件。

### 4. `JsonMemoryStore` 委托 Repository

修改前：

```python
JsonMemoryStore -> load_memory(settings.memory_path)
JsonMemoryStore -> save_memory(settings.memory_path, memory)
```

修改后：

```python
JsonMemoryStore -> JsonMemoryRepository -> load_memory(namespace)
JsonMemoryStore -> JsonMemoryRepository -> save_memory(namespace, memory)
```

也就是说，上层 `JsonMemoryStore` 继续保持现有行为，但内部已经开始使用 Repository 语义。

### 5. 配置增加 `memory_user_id`

`Settings` 新增：

```python
memory_user_id: str = "default"
```

可通过环境变量设置：

```env
HUMANCHAT_MEMORY_USER_ID="default"
```

当前它主要用于构建 namespace：

```text
("users", memory_user_id, "memory")
```

未来多用户时，可以用不同 `memory_user_id` 形成隔离。

## 为什么这样做

### 1. 不应该让长期记忆永远绑定单文件

单文件 JSON 的问题是：

```text
不适合多用户
不适合多项目
不适合细粒度查询
不适合增量同步
不适合未来 UI 分页和搜索
```

但直接删除 JSON 又风险太高。

所以本次采用中间层：

```text
当前仍用 JSON
代码语义先升级为 Repository
未来替换后端时，上层不大改
```

### 2. namespace 是长期记忆成熟化的核心

长期记忆需要回答：

```text
这是谁的记忆？
这是哪个项目的记忆？
这是哪个角色可见的记忆？
这是全局记忆还是会话记忆？
```

namespace 可以表达这些边界。

例如未来可以扩展为：

```text
("users", "alice", "memory")
("projects", "HumanChat", "memory")
("characters", "nanami", "memory")
```

### 3. 为 LangGraph Store 做准备

LangGraph Store 的长期记忆设计也是围绕 namespace/key/value 展开的。

本次 Repository 的方法名和数据流都在向这个方向靠拢。

后续如果接入真正的 LangGraph Store，可以新增：

```python
LangGraphMemoryRepository
```

然后让工厂函数选择不同实现。

### 4. 保持当前功能稳定

本次没有改变 `/memory` 命令体验，没有改变 `graph.py` 读取长期记忆的方式，也没有改变默认 JSON 文件路径。

这是为了确保框架化是渐进式的，不让用户已有记忆突然不可用。

## 对成熟项目的意义

### 1. 支持多用户演进

虽然当前 JSON 后端仍是单 namespace，但上层语义已经开始支持用户隔离。

### 2. 支持后端替换

未来可以从 JSON 换到 SQLite、LangGraph Store 或 Postgres。

### 3. 支持更细粒度的记忆操作

Repository 已经具备按 `MemoryItem.id` 写入和删除的能力。

### 4. 让 Store 和 Repository 职责分离

```text
Store：业务层接口，适合 CLI / Graph 使用
Repository：持久化层接口，适合后端替换和 namespace 管理
```

这种分层更接近成熟项目结构。

## 当前完成程度

本次完成：

```text
新增 MemoryNamespace
新增 default_memory_namespace
新增 MemoryRepository 协议
新增 JsonMemoryRepository
JsonMemoryStore 内部改为委托 Repository
Settings 增加 memory_user_id
storage __init__ 导出 Repository 类型
```

本次没有完成：

```text
真正接入 LangGraph Store
多 namespace JSON 文件拆分
SQLite / Postgres 记忆后端
自动记忆提取 Graph 节点化
记忆确认流程结构化
```

这些会在后续步骤继续推进。

