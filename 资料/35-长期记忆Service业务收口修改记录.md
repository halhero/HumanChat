# 35 长期记忆 Service 业务收口修改记录

## 本次目标

本次修改建立统一的长期记忆业务层 `LongTermMemoryService`。

第一步已经将数据模型和 JSON 文件读写分开，但“什么样的记忆允许添加”“怎样按用户看到的序号删除”“怎样生成提示词”等业务规则仍然写在 `JsonMemoryStore` 中。

这些规则不属于 JSON。无论底层使用 JSON、SQLite 还是 LangGraph Store，都应该保持相同行为，因此它们需要进入与存储技术无关的 Service。

## 修改前的问题

原来的调用结构是：

```text
Graph / CLI
  -> JsonMemoryStore
       -> 处理文本去空格
       -> 检查重复
       -> 按序号删除
       -> 格式化提示词
       -> JsonMemoryRepository
```

`JsonMemoryStore` 名字表达的是“JSON 存储实现”，实际承担的却是记忆业务服务。

如果未来切换到 LangGraph Store，很容易再写一个 `LangGraphMemoryStore`，并复制同样的增删和格式化代码。两份业务规则会逐渐产生差异。

## 修改了什么

### 1. 新增 `MemoryService` 协议

`human_chat/memory_service.py` 定义上层需要的能力：

```python
class MemoryService(Protocol):
    def load(self) -> LongTermMemory: ...
    def save(self, memory: LongTermMemory) -> None: ...
    def add(self, text, source="manual", confidence=None) -> bool: ...
    def delete(self, index: int) -> str | None: ...
    def format_for_prompt(self) -> str: ...
```

Graph 和 CLI 关心的是这些业务能力，不需要知道数据保存在哪种后端。

### 2. 新增 `LongTermMemoryService`

这个具体服务接收两个依赖：

```python
LongTermMemoryService(repository, namespace)
```

Repository 决定数据如何持久化，namespace 决定数据属于哪个用户。Service 自己只执行统一的业务规则。

### 3. 收口新增规则

`add()` 统一执行：

```text
去除首尾空白
拒绝空内容
按完整文本检查重复
创建 MemoryItem
通过 Repository 持久化
```

调用者不需要重复这些判断，也不能绕开这些规则直接拼装 JSON。

### 4. 收口删除规则

CLI 向用户展示的是从 1 开始的序号，而程序列表从 0 开始。这个转换属于应用业务规则：

```python
zero_based_index = index - 1
```

Service 找到对应 `MemoryItem.id` 后，再让 Repository 按稳定 id 删除。存储层不需要理解“界面序号”的概念。

### 5. 收口提示词格式化

`format_for_prompt()` 从 Repository 获取当前用户的记忆，并统一输出：

```text
长期记忆：
- 第一条
- 第二条
```

这保证不同存储后端注入给模型的内容格式一致。

### 6. `JsonMemoryStore` 变成过渡适配器

为了让本步骤可以单独检出运行，`JsonMemoryStore` 暂时保留类名，但它已经不再包含任何业务实现：

```python
class JsonMemoryStore(LongTermMemoryService):
    def __init__(self, settings):
        ...
        super().__init__(repository, namespace)
```

它现在只负责根据配置组装 JSON Repository。下一步会删除这个容易误导的旧名字，由工厂直接构造 Service。

## 为什么这样设计

最终依赖方向变为：

```text
Graph / CLI
  -> MemoryService
       -> MemoryRepository
            -> JSON 或 LangGraph Store
```

每层只关心下一层的抽象：

```text
Graph / CLI：什么时候使用记忆
Service：记忆业务规则是什么
Repository：记忆怎样持久化
Model：记忆数据长什么样
```

这让替换存储技术不再影响业务规则，也让业务规则可以脱离真实文件进行测试。

## 测试设计

新增 `tests/test_memory_service.py`，使用内存 Repository 测试 Service，而不创建真实 JSON 文件。

覆盖内容包括：

```text
文本规范化与去重
从 1 开始的删除序号
空记忆与非空记忆提示词格式化
```

这证明 Service 只依赖 Repository 协议，不依赖 JSON 实现。

## 对成熟商业项目的意义

1. 业务规则只有一份，避免不同后端行为不一致。
2. Graph 和 CLI 不被 JSON 技术绑住。
3. Service 可以使用假 Repository 快速测试。
4. 以后新增更新、合并、冲突处理或审核状态时，有明确的业务落点。
5. 存储后端切换不需要复制增删逻辑。

## 本次完成程度

已经完成：

```text
统一 MemoryService 协议
统一 LongTermMemoryService 实现
新增、删除、格式化规则收口
JsonMemoryStore 降为无业务逻辑的过渡适配器
Service 独立测试用例
```

下一步处理：

```text
删除 memory_store.py 旧辅助函数
删除 JsonMemoryStore 旧命名
工厂直接组装 LongTermMemoryService
Graph 和 CLI 使用 create_memory_service
按最终分层更新测试和 README
```
