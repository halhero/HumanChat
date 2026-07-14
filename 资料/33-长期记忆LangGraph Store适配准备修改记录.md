# 33 长期记忆 LangGraph Store 适配准备修改记录

## 本次目标

本次目标是为长期记忆接入 LangGraph Store 做代码准备。

当前项目默认仍使用 JSON 文件，因为它简单、适合本地开发、对用户当前环境要求低。

但成熟 Agent 项目的长期记忆最终通常需要更标准的 Store 后端：

```text
namespace
key
value
search
delete
```

LangGraph Store 的长期记忆模型正是围绕这些概念设计的。

## 修改了什么

### 1. 新增 `LangGraphMemoryRepository`

`human_chat/memory_repository.py` 新增：

```python
class LangGraphMemoryRepository:
    def __init__(self, store):
        self.store = store
```

它封装一个外部传入的 LangGraph Store-like 对象。

当前实现使用统一 key：

```text
profile
```

保存完整长期记忆对象。

### 2. 实现 Repository 协议方法

`LangGraphMemoryRepository` 实现：

```python
load_memory(namespace)
save_memory(namespace, memory)
list_items(namespace)
put_item(namespace, item)
delete_item(namespace, item_id)
```

这样它和 `JsonMemoryRepository` 对上层呈现相同接口。

### 3. 长期记忆序列化函数公开

`memory_store.py` 新增公开函数：

```python
def memory_to_dict(memory: LongTermMemory) -> dict:
    ...
```

这样 Store adapter 可以复用统一的 Pydantic v1/v2 兼容序列化逻辑。

### 4. `storage.__init__` 导出新 Repository

新增导出：

```python
LangGraphMemoryRepository
```

方便后续工厂函数或测试直接使用。

## 为什么这样做

### 1. 不直接强切后端

当前用户的长期记忆在 JSON 文件中。

如果一次性切到 LangGraph Store，会带来：

```text
迁移成本
依赖不确定性
数据兼容风险
调试复杂度增加
```

所以本次先增加 adapter，让架构具备替换能力。

### 2. 保持上层接口不变

无论底层是 JSON 还是 LangGraph Store，上层都只依赖：

```python
MemoryRepository
```

这能避免未来后端替换时大面积修改 CLI、Graph、MemoryStore。

### 3. 明确长期记忆后端边界

`MemoryStore` 负责业务语义。

`MemoryRepository` 负责持久化语义。

`LangGraphMemoryRepository` 负责对接框架 Store。

这个分层更接近成熟项目。

## 怎么做

保存长期记忆：

```python
self.store.put(namespace, "profile", memory_to_dict(memory))
```

读取长期记忆：

```python
stored = self.store.get(namespace, "profile")
return LongTermMemory(**_stored_value(stored))
```

新增 item：

```python
memory = self.load_memory(namespace)
memory.items = [existing for existing in memory.items if existing.id != item.id]
memory.items.append(item)
self.save_memory(namespace, memory)
```

删除 item：

```python
memory.items = [item for item in memory.items if item.id != item_id]
self.save_memory(namespace, memory)
```

## 对成熟项目的意义

### 1. 后端切换路径清晰

后续可以新增配置：

```env
HUMANCHAT_MEMORY_BACKEND="json|langgraph"
```

然后由工厂函数选择 Repository 实现。

### 2. 保留当前可用性

默认 JSON 后端仍然可用，不影响用户现有数据。

### 3. 与 LangGraph 长期记忆模型对齐

namespace/key/value 的思路已经进入项目核心存储层。

后续接入真正 Store 时，不需要重新设计上层业务模型。

## 当前完成程度

本次完成：

```text
新增 LangGraphMemoryRepository
公开 memory_to_dict
storage 导出 LangGraphMemoryRepository
JsonMemoryStore 已经通过 Repository item API 写入
```

本次没有做：

```text
默认启用 LangGraph Store 后端
Store 后端配置开关
向量检索或语义搜索
测试覆盖
```

