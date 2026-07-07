# 22 记忆 Store 接口与 CLI 修复修改记录

## 本次目标

本次是“记忆模块 LangChain / LangGraph 框架化”的第一步。

目标不是立刻替换存储后端，而是先把记忆模块的 Store 接口边界整理清楚，并修复当前 CLI 中一个已经存在的旧代码残留问题。

当前项目已经有：

```text
JsonSessionStore
JsonMemoryStore
create_session_store
create_memory_store
```

但这些接口还没有被显式定义成协议，上层代码也仍然暴露了一些具体 JSON 类型。对于后续框架化来说，第一步应该先明确“上层依赖接口，而不是依赖具体实现”。

## 修改了什么

### 1. 新增 `human_chat/storage/base.py`

新增两个协议接口：

```python
class SessionStore(Protocol):
    def create(self) -> dict: ...
    def load(self, session_id: str) -> dict: ...
    def save(self, session: dict) -> None: ...
    def list_recent(self, limit: int = 10) -> list[dict]: ...


class MemoryStore(Protocol):
    def load(self) -> LongTermMemory: ...
    def save(self, memory: LongTermMemory) -> None: ...
    def add(self, category: str, text: str) -> bool: ...
    def delete(self, category: str, index: int) -> str | None: ...
    def format_for_prompt(self) -> str: ...
```

这两个协议描述了上层代码真正需要的能力。

`JsonSessionStore` 和 `JsonMemoryStore` 当前已经天然满足这些方法，因此本次不需要大规模改动实现类。

### 2. 工厂函数返回接口类型

`human_chat/storage/__init__.py` 中的工厂函数现在显式返回协议类型：

```python
def create_session_store(settings) -> SessionStore:
    return JsonSessionStore(settings)


def create_memory_store(settings) -> MemoryStore:
    return JsonMemoryStore(settings)
```

这表达了一个更成熟的依赖方向：

```text
CLI / Runtime / Graph
  -> create_memory_store / create_session_store
  -> MemoryStore / SessionStore 协议
  -> 当前 JSON 实现
```

上层代码不需要关心后端是不是 JSON。

### 3. CLI 类型依赖从具体类改为接口

`human_chat/cli.py` 中原来导入：

```python
JsonSessionStore
```

现在改为：

```python
SessionStore
```

并把 `_choose_session`、`_create_new_session` 的类型标注改为接口。

这一步虽然小，但很重要：CLI 作为用户交互层，不应该绑定具体 JSON 存储。

### 4. 修复 `/memory add/delete` 的旧代码残留

在 `_handle_memory_command` 中，原来存在两行旧代码：

```python
save_memory(settings.memory_path, memory)
```

但 `save_memory` 和 `memory` 在 `cli.py` 中都没有定义。

实际上，当前 `memory_store.add()` 和 `memory_store.delete()` 内部已经会完成保存：

```python
added = memory_store.add(category, text)
deleted = memory_store.delete(category, index)
```

所以这两行不仅多余，而且会导致 `/memory add` 或 `/memory delete` 成功执行后继续抛出 `NameError`。

本次已删除这两行。

## 为什么这样做

### 1. 框架化要先从边界开始

很多重构失败的原因，是一开始就急着换实现，比如直接从 JSON 切到数据库或 LangGraph Store。

更稳妥的方式是先明确边界：

```text
上层需要什么能力？
当前实现如何满足？
未来替换时哪些代码不应该变？
```

`MemoryStore` 和 `SessionStore` 协议就是这个边界。

### 2. 为后续 LangGraph Store / Repository 做准备

后续长期记忆会继续向 LangGraph Store 的 `namespace / key / value` 语义靠近。

如果现在上层代码仍然到处依赖 `JsonMemoryStore`，后续替换会牵动很多文件。

有了接口后，后续可以逐步演进：

```text
JsonMemoryStore
  -> JsonMemoryRepository
  -> LangGraphStoreMemoryRepository
```

而 CLI / Graph / Runtime 不需要跟着大改。

### 3. 先修 bug，避免在重构中混入历史问题

`/memory add/delete` 中的未定义变量是一个真实运行 bug。

如果不先处理，后续做数据模型升级或 repository 化时，很容易误以为是新代码造成的问题。

本次先把旧残留清掉，让后续每一步更干净。

## 对成熟项目的意义

### 1. 降低后端迁移成本

接口清楚后，JSON、SQLite、LangGraph Store 都可以作为后端实现。

### 2. 降低上层耦合

CLI、Graph、Runtime 不再需要知道当前存储是不是 JSON。

### 3. 提高可测试性

后续测试可以传入假的 `MemoryStore` 或 `SessionStore`，不用真实读写文件。

### 4. 避免明显运行错误

修复 `/memory add/delete` 的旧残留后，手动记忆维护命令更可靠。

## 当前完成程度

本次完成：

```text
新增 SessionStore 协议
新增 MemoryStore 协议
存储工厂函数返回协议类型
CLI 会话选择类型从具体类改为接口
删除 /memory add/delete 中未定义变量的旧保存代码
```

本次没有完成：

```text
长期记忆数据模型升级
MemoryRepository / namespace / key / value 语义
自动记忆提取 Graph 节点化
记忆确认流程结构化
```

这些会在后续步骤继续推进。

