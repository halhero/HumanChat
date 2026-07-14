# 31 长期记忆 Repository 写入收口修改记录

## 本次目标

本次目标是让长期记忆的新增和删除操作真正走 Repository 的 item API，而不是继续在业务层直接 load/save 整个 memory。

之前虽然已经新增了 `MemoryRepository`：

```python
put_item(namespace, item)
delete_item(namespace, item_id)
list_items(namespace)
```

但 `JsonMemoryStore.add()` 和 `JsonMemoryStore.delete()` 仍然是：

```python
memory = self.load()
add_memory_item(memory, ...)
self.save(memory)
```

这种写法能用，但 Repository 的语义没有被充分使用。

## 修改了什么

### 1. `JsonMemoryStore.add()` 改用 `list_items + put_item`

修改后流程：

```text
1. 清理输入文本
2. 通过 repository.list_items(namespace) 检查重复
3. 创建 MemoryItem
4. 通过 repository.put_item(namespace, item) 写入
```

核心代码：

```python
self.repository.put_item(
    self.namespace,
    MemoryItem(text=normalized, source=source, confidence=confidence),
)
```

### 2. `JsonMemoryStore.delete()` 改用 `list_items + delete_item`

修改后流程：

```text
1. 通过 repository.list_items(namespace) 读取当前 items
2. 将用户输入的序号转换为 item_id
3. 通过 repository.delete_item(namespace, item.id) 删除
4. 返回被删除的文本
```

也就是说，用户仍然使用 `/memory delete 1`，但内部删除操作已经转为稳定的 `item_id`。

### 3. `MemoryStore` 仍然保持业务外观

CLI 和 Graph 不需要关心 Repository。

它们仍然只调用：

```python
memory_store.add(...)
memory_store.delete(...)
memory_store.format_for_prompt()
```

## 为什么这样做

### 1. Store 和 Repository 职责要分清

成熟项目通常需要分层：

```text
CLI / Graph：用户交互和流程编排
MemoryStore：业务入口
MemoryRepository：持久化语义
具体后端：JSON / SQLite / LangGraph Store
```

如果业务层直接 load/save 整体对象，就会让 Repository 变成摆设。

### 2. 删除应该基于稳定 ID

用户界面可以显示序号，但存储层不应该依赖序号。

序号是展示顺序，`MemoryItem.id` 才是稳定标识。

这对未来 UI、分页、搜索、排序都更可靠。

### 3. 为后端替换减少改动面

当未来从 JSON 切换到 LangGraph Store 或数据库时，上层只需要继续调用 Repository API。

不需要在业务层重写一堆 load/save 逻辑。

## 怎么做

新增记忆：

```python
normalized = text.strip()
if normalized in [item.text for item in self.repository.list_items(self.namespace)]:
    return False

self.repository.put_item(
    self.namespace,
    MemoryItem(text=normalized, source=source, confidence=confidence),
)
```

删除记忆：

```python
items = self.repository.list_items(self.namespace)
item = items[index - 1]
self.repository.delete_item(self.namespace, item.id)
```

## 对成熟项目的意义

### 1. 后端可替换性更强

业务层不绑定 JSON 文件结构。

### 2. 数据操作更细粒度

新增和删除都变成 item 级操作。

### 3. 更接近 LangGraph Store 思路

LangGraph Store 的核心也是 namespace/key/value。

本次把业务写入路径进一步靠近这个模型。

## 当前完成程度

本次完成：

```text
JsonMemoryStore.add 改用 repository.put_item
JsonMemoryStore.delete 改用 repository.delete_item
删除操作由展示序号映射到 MemoryItem.id
MemoryStore 对 CLI 的外观不变
```

本次没有做：

```text
并发写冲突处理
数据库事务
测试覆盖
```

