# 39 长期记忆 Repository 单条语义收口修改记录

## 本次目标

本次修改解决长期记忆 Repository 同时存在“整包读写”和“单条读写”两套语义的问题，并消除 JSON 与 LangGraph 实现中重复的整包加载、修改、覆盖流程。

修改后，Repository 只围绕 `MemoryItem` 工作。`LongTermMemory` 仍然作为业务层向上返回的集合视图，但不再作为持久化层的写入单位。

## 修改前的问题

旧协议同时包含：

```text
load_memory
save_memory
list_items
put_item
delete_item
```

这产生了两个矛盾：

1. 上层既可以保存单条记忆，也可以绕开单条规则直接覆盖整个记忆集合。
2. LangGraph Store 明明支持 namespace + key + value，却仍然把所有记忆存进固定的 `profile` key。

此外，`JsonMemoryRepository` 构造时已经绑定 namespace，每个方法却还要再传一次 namespace，导致接口同时采用“单用户实例”和“多租户实例”两种设计。

## 修改内容

### 1. Repository 协议只保留 item 方法

新协议为：

```python
list_items(namespace)
get_item(namespace, item_id)
upsert_item(namespace, item)
delete_item(namespace, item_id)
```

删除公开的 `load_memory()` 和 `save_memory()`。

### 2. Repository 不再绑定 namespace

`JsonMemoryRepository` 构造函数现在只接收基础路径：

```python
JsonMemoryRepository(settings.memory_path)
```

namespace 在每次操作时传入。一个 Repository 实例可以服务多个用户，接口与 LangGraph BaseStore 的使用方式保持一致。

### 3. JSON 聚合读写降为内部实现细节

JSON 文件本身仍然是一个包含 `items` 的文档，因此适配器内部仍需读取和原子替换文件。

但 `_load_memory()` 和 `_save_memory()` 现在是私有方法，上层无法使用它们绕过 item API。

### 4. LangGraph Store 使用独立记忆 key

每条记忆现在通过：

```python
store.put(namespace, item.id, item.model_dump())
```

保存。读取单条记忆使用 `store.get()`，列出记忆使用 `store.search()`，删除使用 `store.delete()`。

固定的 `profile` key 被移除，因此添加一条记忆不会覆盖整个用户记忆集合。

### 5. Service 不再提供整包 save

`MemoryService.save()` 被删除。Service 的 `load()` 只是将 Repository 返回的 item 列表组装成：

```python
LongTermMemory(items=...)
```

新增操作统一调用 `upsert_item()`，删除操作统一调用 `delete_item()`。

## 为什么这样设计

持久化接口应当使用后端可以稳定表达的最小操作单位。

对于 LangGraph Store，这个单位是：

```text
namespace + item_id + value
```

对于 JSON，虽然底层仍需覆盖文件，但适配器可以把这种限制封装在内部。业务层不应该因为 JSON 的物理格式而暴露整包覆盖能力。

## 对成熟项目的意义

1. 消除两套写入入口，业务规则更难被绕过。
2. LangGraph Store 不再进行低效的整包读改写。
3. 每条记忆拥有稳定 key，为更新、审计和语义索引做准备。
4. Repository 统一为多 namespace 语义，更适合多用户服务。
5. JSON 与 Store 可以运行同一套协议合同测试。

## 测试覆盖

测试现在分别验证：

```text
JSON 默认记忆创建
JSON item 新增、读取、删除
用户 namespace 路径隔离
LangGraph InMemoryStore 每条记忆独立保存
MemoryService 只依赖 item Repository
```

## 下一步

下一步会把 Store 从“可单独构造的适配器”提升为 Graph 正式运行资源：

```text
增加 memory backend 配置
编译 Graph 时传入 store
通过 Runtime Context 传递 user_id
提供 JSON 到 Store 的显式迁移入口
```
