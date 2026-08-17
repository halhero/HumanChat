# 47 Graph 调用链与运行时配置边界统一修改记录

## 本次目标

本次修改针对代码复查中已经确认会产生实际问题的冗余进行统一治理，范围包括：

```text
Graph 正常回答被重复生成
LangGraph Store 被传入 Graph 却没有任何节点使用
AgentContext 被传入 Graph 却没有任何运行时语义
角色 TTS 参数同时存在于 Settings 和角色 YAML
Settings 默认值同时写在模型与 load_settings() 中
手写 JSON 指令与 with_structured_output() 重复约束回答格式
```

本次没有按照“当前没有调用就删除”的方式清理代码。`MemoryResource`、Repository
按 ID 查询、角色身份字段和部分未来业务元数据仍然具有明确的架构价值，因此继续保留。

## 修改前的问题

### 1. 一次普通回答会调用两次同一个模型

旧 Graph 先执行：

```text
call_agent_model
  -> tool_llm.invoke()
```

这个模型既可以发起工具调用，也可以直接回答。如果它直接回答，Graph 并不会使用该
AIMessage，而是进入 `generate_reply`，再次执行：

```text
llm.with_structured_output(TtsResponse).invoke()
```

因此“不需要工具”的普通问题也会调用同一个模型两次。第一次回答被丢弃，带来额外的：

```text
模型费用
响应延迟
限流压力
两次回答内容不一致的可能性
```

此外，旧的工具判断提示只包含当前问题，不包含角色设定、长期记忆和会话历史。第一个
模型实际上无法在完整对话上下文中决定应当回答还是调用工具。

### 2. Store 存在两条看似有效的长期记忆入口

资料 46 保留了以下调用：

```text
MemoryResource.store
  -> Runtime
  -> build_graph(store=...)
  -> workflow.compile(store=store)
```

当时保留它的理由是 LangGraph 编译过程可能需要 Store。但继续检查 Graph 后确认：

```text
没有节点接收 Runtime
没有节点读取 runtime.store
长期记忆读取和保存全部通过 MemoryService
MemoryRepository 已经持有实际 Store
```

因此这条注入链没有实际效果，还会让维护者误以为长期记忆同时由 Graph Store 和
MemoryService 两套入口管理。本资料对资料 46 中“保留原始 Store 给 Graph”的结论作出
后续修正。

### 3. AgentContext 暗示了并不存在的按请求用户隔离

旧 Runtime 每次调用 Graph 都传递：

```python
AgentContext(user_id=settings.memory_user_id)
```

同时 Graph 声明：

```python
StateGraph(ChatState, context_schema=AgentContext)
```

但是没有任何节点读取 Context。当前用户 namespace 已经在创建
`LongTermMemoryService` 时固定，改变 AgentContext 也不会改变记忆访问范围。

保留这段代码会制造错误的多用户能力暗示。项目当前仍是单用户 CLI，因此应在真正出现
按请求用户、租户或权限上下文需求时，再以完整方式引入运行时 Context。

### 4. TTS 角色参数有两个配置来源

旧 `Settings` 定义了：

```text
tts_ref_audio_path
tts_prompt_text
tts_prompt_lang
tts_text_lang
tts_split_method
tts_speed_factor
```

`load_settings()` 也读取对应环境变量，但 `TtsClient` 完全不使用这些字段。真正生效的
数据来自 `characters/*.yaml` 中的 `character.tts`。

这比普通的未使用字段更危险，因为部署人员设置环境变量后不会得到任何错误，却也不会
改变实际 TTS 行为。

### 5. Settings 默认值重复

每个配置默认值同时存在于：

```text
Settings 字段声明
load_settings() 的 os.getenv(..., default)
```

这会使 `Settings()` 和 `load_settings()` 在未来修改遗漏时产生不同结果。测试、CLI 和
其他入口可能因此获得不同的模型、后端或路径配置。

### 6. 回答格式约束重复

旧系统提示手动要求模型返回：

```json
{"text": "你的回答"}
```

随后又使用 `with_structured_output(TtsResponse)`。两种约束解决的是同一个问题，而且
`TtsResponse` 只有一个 `text` 字段，TTS 最终也只需要普通回答文本。

## 修改内容

### 1. Graph 改为单一模型对话链

首次模型调用现在同时收到：

```text
角色 system prompt
长期记忆
回复语言要求
工具使用规则
历史 messages
当前用户问题
```

模型返回工具调用时，流程仍然进入 LangGraph `ToolNode`；工具结果以 `ToolMessage`
追加到同一条 `tool_messages` 对话链，然后再次调用绑定工具的模型。

模型不再请求工具时，最后一个 AIMessage 直接进入 `finalize_reply`，其文本同时用于：

```text
assistant_text
会话 messages
TTS 合成
长期记忆候选提取
```

修改后的正常流程是：

```text
prepare_context
  -> call_agent_model
       | 有 tool_calls
       v
     ToolNode -> call_agent_model
       |
       | 无 tool_calls
       v
     finalize_reply
       -> synthesize_speech
       -> extract_memory
       -> review_memory
```

普通问题只调用一次模型；调用一个工具的典型问题调用两次模型，第二次回答已经包含工具
结果，不再额外生成第三份正式回答。

### 2. 工具轮数上限使用明确的收尾路径

达到 `MAX_TOOL_CALL_ROUNDS` 后，Graph 不执行超出上限的工具，而是写入
`tool_limit_reached` 和调试事件，然后调用未绑定工具的原始 LLM：

```text
mark_tool_limit_reached
  -> generate_limit_reply
  -> finalize_reply
```

这一次额外调用不是重复生成，而是为了保证模型在不能继续使用工具时仍能根据已有结果
给出最终回答，并在信息不足时明确说明缺口。

### 3. 保留 MemoryResource，移除原始 Store 暴露

`MemoryResource` 继续保留为资源组合边界：

```python
@dataclass(frozen=True)
class MemoryResource:
    service: MemoryService
    backend: str
    persistent: bool
```

保留它的原因是：

1. `open_memory_resource()` 需要统一表达已经打开的记忆子系统。
2. PostgresStore 的生命周期仍由该 Context Manager 管理。
3. `backend` 和 `persistent` 可用于未来的诊断、健康检查和能力展示。
4. 将来可以增加明确的 capability，而不需要改变 Runtime 的资源组装形式。

删除的只有 `store` 字段。JSON、InMemoryStore 和 PostgresStore 仍在资源工厂内部创建，
LangGraph Store 仍由 `LangGraphMemoryRepository` 使用，业务上层不能绕过 MemoryService。

### 4. Graph 不再编译无消费者的 Store

`build_graph()` 删除 `store` 参数，Runtime 不再传递 `memory.store`，编译改为：

```python
workflow.compile(checkpointer=checkpointer)
```

这里仍然传递 Checkpointer，因为 LangGraph 确实使用它保存短期状态；不再传递 Store，
因为长期记忆已经由 Service 和 Repository 负责。两个持久化机制的用途现在更加明确：

```text
Checkpointer -> Graph 短期会话状态
MemoryService -> Repository -> 长期记忆后端
```

### 5. 删除无效 AgentContext

本次删除：

```text
AgentContext 模型
StateGraph 的 context_schema
ChatRuntime.graph_context
ask()/resume() 的 context 参数
```

`memory_user_id` 配置仍然保留，因为它继续用于资源组装时生成 Memory namespace。删除的
是没有消费者的 Graph Context，而不是用户命名空间概念。

### 6. TTS 配置所有权统一

角色身份和音色参数统一由：

```text
characters/*.yaml -> Character.tts -> TtsClient
```

负责。`Settings` 只保留部署和服务级配置：

```text
tts_service_url
tts_auto_start
gpt_sovits_dir
gpt_sovits_python
gpt_sovits_api_script
speech_output_path
```

这样角色配置负责“使用什么声音”，环境配置负责“服务在哪里、是否由项目启动、输出写到
哪里”。两类配置不再竞争同一个参数。

### 7. Settings 默认值改为单一来源

所有默认值只保留在 `Settings` 字段中。`load_settings()` 现在遍历 `_ENV_OVERRIDES`：

```text
字段名 -> 环境变量名 -> 类型解析器
```

只有环境变量真实存在时才覆盖模型默认值。布尔值、整数、相对路径和可选路径仍然经过
显式解析，其中相对路径继续以项目根目录为基准。

这样可以保证：

```python
Settings() == load_settings()  # 没有环境变量覆盖时
```

以后修改默认模型或默认路径只需修改一个位置。

### 8. 删除重复的结构化回答包装

Graph 删除手写 JSON 指令、`TtsResponse` 和回答阶段的 `with_structured_output()`。
`finalize_reply` 从 LangChain AIMessage 中提取文本，并对空回答显式报错。

记忆提取仍然保留 `with_structured_output(MemoryExtractionResult)`，因为该步骤确实需要
`candidates` 数组这一结构化业务结果，不属于本次删除范围。

## 为什么这样设计

### 1. 扩展点必须拥有真实职责

`MemoryResource` 是资源和生命周期边界，因此保留。`AgentContext` 和 Graph Store 注入
当前没有消费者，而且暗示并不存在的行为，因此删除。两者不能仅凭“都暂时没有业务调用”
得出相同结论。

### 2. Service 是长期记忆的唯一业务入口

Store 是持久化基础设施，Repository 是适配层，Service 负责去重、格式化和业务规则。
Graph 只调用 Service，可以避免未来不同节点各自使用 Store 并形成多套记忆规则。

### 3. 模型回答应只有一个权威版本

工具模型已经拥有完整角色和对话上下文后，它的无工具回复就是正式回答。继续调用同一个
模型重写一次既没有增加职责隔离，也不能保证回答更好。

### 4. 配置必须有清晰所有权

成熟项目中的配置不仅要“可以设置”，还必须能回答：

```text
谁拥有这个参数
哪个来源优先
修改后由谁消费
未设置时默认值在哪里
```

本次通过角色配置、服务配置和默认值来源的拆分，使这些问题都有唯一答案。

## 有意保留、未作为冗余删除的内容

本次明确保留：

```text
MemoryResource 类型
MemoryResource.backend / persistent
SessionRepository.load()
MemoryRepository.get_item()
Character.id / name
MemoryItem.updated_at / confidence
MemoryReviewRequest.require_confirmation
CliCommand.usage
ToolRegistry.get_metadata_by_command()
SessionRecord 的恢复元数据
Pydantic 1/2 序列化兼容辅助函数
```

这些内容有合理的领域语义或未来功能方向。部分字段还没有完整接入行为，后续应在对应功能
设计时完善，而不是在本轮以静态未引用为理由删除。

## 对成熟项目的意义

1. 普通聊天减少一次没有价值的模型调用。
2. 工具选择能够看到角色、记忆和完整会话历史。
3. 工具结果与最终回答处于同一条可追踪消息链。
4. 长期记忆只有 Service 一条业务入口。
5. MemoryResource 保留生命周期和后端元数据价值，但不泄漏底层 Store。
6. Graph Context 不再虚假表示尚未实现的多用户隔离。
7. TTS 参数修改后能够确定唯一的生效来源。
8. Settings 默认值不会因两个位置修改不同步而漂移。
9. 工具调用上限仍然能得到明确的最终回复。
10. 新增测试固定关键调用次数和消息链行为，防止重复调用再次出现。

## 测试覆盖

新增 `tests/test_graph.py`，验证：

```text
不调用工具时只执行一次模型调用
首个模型调用包含 SystemMessage、长期记忆和用户问题
工具结果以 ToolMessage 保留在同一条模型对话中
一次工具调用后直接使用下一条 AIMessage 作为最终回答
tool_call_count 和 tool_events 继续正确更新
```

新增 `tests/test_config.py`，验证：

```text
没有环境变量时 load_settings() 与 Settings() 使用相同默认值
布尔、整数、项目相对路径和空可选路径能够正确解析
```

`tests/test_memory_resources.py` 改为通过公开 `MemoryService` 验证后端行为，不再访问
MemoryResource 的原始 Store。

完整验证结果：

```text
python -m pytest -q
37 passed

python -m compileall -q human_chat tests
通过

git diff --check
通过
```

## 本步骤涉及文件

```text
human_chat/config.py
human_chat/graph.py
human_chat/memory_resources.py
human_chat/runtime.py
human_chat/schemas.py
tests/test_config.py
tests/test_graph.py
tests/test_memory_resources.py
README.md
资料/47-Graph调用链与运行时配置边界统一修改记录.md
```
