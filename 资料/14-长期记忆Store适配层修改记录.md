# 14 长期记忆 Store 适配层修改记录

## 本次目标

本次改造对应 `资料/11-框架化改造盘点.md` 中的第三项：长期记忆存储框架化。

这一阶段不直接删除 JSON 存储，也不强行接入尚未验证的外部 Store 后端，而是先把上层代码和具体 JSON 实现解耦。

## 修改前的问题

部分上层代码直接依赖具体实现：

```python
JsonMemoryStore(settings)
JsonSessionStore(settings)
```

这意味着如果以后要切换为 LangGraph Store 或 SQLite 后端，需要修改很多业务代码。

## 本次怎么改

在 `human_chat/storage/__init__.py` 中增加工厂函数：

```python
create_memory_store(settings)
create_session_store(settings)
```

上层代码不再直接创建 `JsonMemoryStore`，而是使用：

```python
memory_store = create_memory_store(settings)
session_store = create_session_store(settings)
```

目前工厂函数仍然返回 JSON 实现：

```text
JsonMemoryStore
JsonSessionStore
```

但这已经形成了后端替换点。

## 为什么这样做

长期记忆最终应该迁移到更接近 LangGraph Store 的模型：

```text
namespace
key
value
```

但现在项目仍在使用 JSON 文件，直接切换会带来较大风险。

所以采用分阶段迁移：

```text
第一阶段：建立 Store 工厂和适配层
第二阶段：让 Store 支持 namespace/key 语义
第三阶段：接入 LangGraph Store 或 SQLite
```

这样可以保证现有功能不被破坏。

## 修改后的依赖方向

修改前：

```text
CLI / Graph / Runtime -> JsonMemoryStore / JsonSessionStore
```

修改后：

```text
CLI / Graph / Runtime -> create_memory_store / create_session_store -> 当前后端实现
```

## 后续计划

下一步可以在不修改上层调用代码的前提下，新增：

```text
LangGraphStoreMemoryStore
SQLiteSessionStore
```

然后只修改工厂函数的返回对象。

这会让存储层逐渐接近成熟 Agent 项目的架构。

