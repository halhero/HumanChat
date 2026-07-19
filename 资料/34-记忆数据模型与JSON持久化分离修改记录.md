# 34 记忆数据模型与 JSON 持久化分离修改记录

## 本次目标

本次修改解决记忆模块最基础的职责混合问题：`memory_store.py` 既定义记忆数据结构，又直接读写 JSON 文件。

修改后，数据模型只描述“长期记忆是什么”，Repository 只处理“长期记忆如何保存和读取”。这是后续建立统一业务服务的前提。

## 修改前的问题

原来的依赖关系是：

```text
JsonMemoryRepository
  -> memory_store.load_memory()
  -> memory_store.save_memory()
```

这意味着名为 Repository 的持久化层并不真正拥有持久化逻辑，反而把文件操作委托给了上层含义的 `memory_store.py`。

由此产生三个问题：

1. 看到文件名无法判断代码职责。
2. 修改 JSON 格式时需要进入业务和模型混合文件。
3. 新增数据库或 LangGraph Store 后端时，容易继续复制混合逻辑。

## 修改了什么

### 1. 新增 `human_chat/memory_models.py`

该文件现在只包含：

```text
MemoryItem
LongTermMemory
create_default_memory
```

`MemoryItem` 表示一条长期记忆，`LongTermMemory` 表示记忆集合。旧版 `preferences`、`facts`、`notes` 的兼容迁移也属于数据模型解析，因此仍由 `LongTermMemory` 负责。

### 2. 默认记忆改为工厂函数

原实现使用一个全局可变对象 `DEFAULT_MEMORY`。如果调用方意外修改它，后续读取可能共享被修改后的内容。

现在改为：

```python
def create_default_memory() -> LongTermMemory:
    return LongTermMemory(...)
```

每次初始化都会得到新的模型实例，避免不同用户或不同测试之间共享可变状态。

### 3. JSON 读写进入 `JsonMemoryRepository`

`JsonMemoryRepository` 现在完整负责：

```text
检查文件是否存在
创建默认记忆文件
读取和解析 JSON
将模型序列化为 JSON
创建父目录
写入文件
```

Repository 不再调用 `memory_store.py` 中的文件函数，持久化职责已经收回到正确层级。

### 4. JSON 写入使用临时文件替换

保存时先写入同目录临时文件，再使用 `replace` 替换正式文件：

```python
temporary_path.write_text(...)
temporary_path.replace(self.path)
```

这样可以降低程序在写入一半时退出，导致正式 JSON 文件只剩半段内容的风险。

## 为什么这样设计

成熟项目通常将记忆模块拆成三种职责：

```text
Model：描述数据
Repository：负责持久化
Service：执行业务规则
```

本次先完成前两层的边界。数据模型不能知道自己保存在哪个文件中，也不应该知道底层未来是 JSON、SQLite 还是 LangGraph Store。

Repository 可以依赖数据模型，因为它需要把存储数据还原为模型；数据模型不依赖 Repository，因此核心结构不会被具体存储技术绑住。

## 本次暂时保留的过渡内容

`memory_store.py` 中的增删和提示词格式化函数暂时保留，使本次提交可以独立运行，也让下一步迁移业务规则时更容易审查。

下一步会新增 `LongTermMemoryService`，把这些业务行为收口到唯一入口，随后删除含义不清晰的旧 Store 文件。

## 对成熟商业项目的意义

这一步带来的价值不是增加新功能，而是降低未来功能扩展的成本：

1. 更换存储后端时不需要改数据模型。
2. 数据文件损坏风险因原子替换而降低。
3. 单元测试可以分别验证模型解析和 JSON 持久化。
4. 开发者通过文件名就能找到对应职责，减少维护时的理解成本。

## 本次完成程度

已经完成：

```text
记忆模型独立
默认记忆实例隔离
JSON 读写收归 Repository
JSON 临时文件原子替换
旧数据格式兼容保留
```

尚未完成：

```text
业务增删规则迁移到 Service
Graph 和 CLI 改用 Service 命名
删除旧 memory_store.py
更新分层测试
```
