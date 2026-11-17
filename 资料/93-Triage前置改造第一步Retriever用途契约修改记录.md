# Triage 前置改造第一步：Retriever 用途契约修改记录

日期：2026-11-17。

## 1. 本步目标

本步只建立 Triage 后续调用所需的 Retriever 契约，不接入请求理解流程，也不修改 Graph。

修改前 `MemoryRetriever.recall()` 虽然已有 `purpose` 参数，但实际上只区分两种情况：

```text
purpose == answer
  -> current + historical

任何其他字符串
  -> current + historical + revoked + expired
```

如果直接传入 `purpose="triage"`，它会被当成维护召回，错误地把已经撤销或过期的记忆交给请求理解。

同时，召回数量只能在创建 Retriever 时通过 `self.limit` 决定，调用方不能让 Triage 使用更小的候选集合。

## 2. 明确三种真实用途

现在 Retriever 明确支持：

```python
RecallPurpose = Literal["answer", "triage", "maintenance"]
```

三种用途对应的行为如下：

| purpose | 可检索状态 | global scope | identity |
| --- | --- | --- | --- |
| `answer` | current、historical | 保留现有自动注入 | 不使用 |
| `triage` | current、historical | 不自动注入 | 不使用 |
| `maintenance` | current、historical、revoked、expired | 不自动注入 | 按现有逻辑使用 |

这些用途都有真实消费者：

- `answer` 用于回答或执行任务时构建长期记忆上下文；
- `triage` 将用于补全跨会话指代；
- `maintenance` 用于记忆形成阶段查找可能需要更新、纠正或撤销的旧记录。

未知 purpose 不再静默落入维护召回，而是明确失败，避免一个拼写错误扩大可见记忆状态。

## 3. 增加单次召回限量

接口增加：

```python
limit: int | None = None
```

未提供时继续使用 Retriever 实例的默认 `self.limit`，因此现有回答和维护调用不需要改变。

调用方提供时，本次调用使用独立限量：

```python
effective_limit = limit if limit is not None else self.limit
```

后续 Triage 可以调用：

```python
retriever.recall(
    memory_query,
    purpose="triage",
    limit=5,
)
```

## 4. 限量如何影响混合召回

项目现有混合召回会先从词面索引和向量索引各取一个较大的窗口，再进行融合。窗口仍然使用原来的四倍规则，但改为基于本次有效限量：

```python
window = effective_limit * 4
```

例如 Triage 指定 `limit=5`：

```text
词面候选窗口：20
语义候选窗口：20
融合去重后的最终候选：最多 5
```

这不是返回 5 条词面候选再加 5 条语义候选，而是两路候选融合后总共最多 5 条。

回答用途的 global scope 配额也改为根据 `effective_limit` 计算，默认调用仍与修改前一致。

## 5. 为什么 Triage 不加入 global scope

回答召回需要考虑适用于所有任务的用户约束和表达偏好，因此现有 `answer` 用途会预留一部分 global scope 名额。

Triage 召回的目的不同：它只需要找到能够补全当前指代、目标、对象或关键约束的跨会话信息。无条件加入全局偏好会占用小型 Top-K，却通常不能说明“上次那个项目”具体指什么。

因此 Triage 仍使用相同的 BM25 和向量检索能力，但不额外调用 `search_global_scope()`。

## 6. 为什么维护召回保留失效状态和 identity

记忆维护需要判断新候选是否对应已经存在、被撤销或过期的旧记录，也需要根据断言 identity 定向找到同一主体、关系和范围的记录。

所以本步没有把维护召回收窄为当前可用记忆，也没有改变 identity 结果在普通融合候选之后补入的现有语义。

## 7. 未修改范围

本步没有修改：

- BM25 词面检索；
- embedding 和向量 KNN；
- RRF 融合排序；
- current 与 historical 的版本排序和名额分配；
- SQLite、PostgreSQL 和索引表；
- MemoryRepository、MemoryService 和 MemoryItem；
- 请求理解 Prompt、Graph 和 ChatState；
- 执行召回的查询构造方式。

同一父记录的多个版本可能占用多个 Top-K 名额，这是已经确认但本阶段暂不处理的召回质量问题。

## 8. 修改文件

| 文件 | 修改内容 |
| --- | --- |
| `human_chat/memory/retrieval.py` | 增加明确的用途类型、Triage 过滤语义和单次 limit |
| `tests/test_memory_retrieval.py` | 验证 Triage 状态、窗口、最终数量以及不调用 global/identity |

## 9. 聚焦验证

执行：

```powershell
python -m pytest tests/test_memory_retrieval.py -q `
  -p no:cacheprovider `
  --basetemp=.validation-triage-retriever-20261117
```

结果：

```text
3 passed in 1.95s
```

测试覆盖：

- 原有词面、语义与 identity 融合行为；
- 回答召回保留 global scope 并保持最终数量有界；
- Triage 使用 current/historical；
- Triage 的 `limit=5` 产生 20 条两路候选窗口；
- Triage 不调用 global scope 和 identity；
- 两路融合后的 Triage 最终候选不超过 5 条。

本步只证明 Retriever 的程序契约和调用边界，不代表 Triage Prompt 或真实自然语言召回质量已经完成。
