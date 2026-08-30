# 前后端分离阶段二：Application 收口与旧入口清理修改记录

## 一、本阶段的目标

阶段一建立了 FastAPI lifespan 和 `ChatApplication`，但为了兼容旧项目，仍保留了：

```text
main.py
  -> human_chat.cli
      -> open_chat_runtime()
          -> ChatRuntime
```

同时，`ChatApplication` 仍公开 `session_repository`、`checkpoint`、`memory`、
`tool_registry` 等底层对象。健康检查路由直接读取这些资源。

这种状态更像“给旧 CLI 外面加了一层 HTTP 壳”，还不是明确的前后端分离后端。

根据本轮要求，本阶段完成两件事：

1. 用唯一的 `HumanChatApplication` 收口资源生命周期和后端用例。
2. 删除只服务于旧 CLI、本地麦克风、STT 和服务器扬声器播放的代码。

本阶段完成后，Python 项目只保留 Web 后端需要的主调用路径。

---

## 二、重构后的主调用链

```text
python -m human_chat.api
        |
        v
FastAPI lifespan
        |
        v
open_human_chat_application(settings)
        |
        +--> SessionRepository
        +--> CheckpointerResource
        +--> MemoryResource
        +--> ToolRegistry / MCP bridge
        +--> compiled LangGraph
        |
        v
HumanChatApplication
        |
        +--> status()
        +--> create_session()
        +--> get_session()
        +--> list_sessions()
        +--> run_turn()
        +--> resume_turn()
        +--> get_graph_state()
```

不存在第二条 CLI 启动链，也不存在另一个 Runtime 再次组合资源。

---

## 三、为什么删除 ChatRuntime

### 3.1 原结构的问题

阶段一中：

```python
ChatApplication.create_runtime(session)
```

会创建一个 `ChatRuntime`。`ChatRuntime` 再持有：

```text
settings
session
compiled graph
session_repository
memory_service
tool_registry
checkpoint 元信息
```

其中很多只是把 Application 已持有的对象再次暴露一遍。

对于旧 CLI，这种“当前会话对象”有一定便利；对于 HTTP 服务，它会造成：

1. 路由可能绕过 Application，直接调用 Repository 或 ToolRegistry。
2. 每个请求都要先构造 Runtime，实际只为了得到 `thread_id` 和 graph config。
3. 资源所有权分散，很难判断关闭责任属于 Runtime 还是 Application。
4. `open_chat_runtime()` 与 FastAPI lifespan 形成两套组合入口。
5. 将来增加会话锁、流式运行和审批时，不知道逻辑应放在哪一层。

### 3.2 新结论

Web 后端只保留一个进程级 Application。一次会话不是一个资源容器，而只是：

```text
SessionRecord + thread_id
```

调用时由 Application 根据 `session_id` 构造 LangGraph config：

```python
{"configurable": {"thread_id": thread_id}}
```

因此删除：

```text
human_chat/runtime.py
ChatRuntime
open_chat_runtime()
open_chat_application()
```

替换为：

```text
human_chat/application.py
HumanChatApplication
open_human_chat_application()
```

---

## 四、HumanChatApplication 的设计

### 4.1 底层对象全部私有

构造函数接收组合好的资源，但保存为私有字段：

```python
self._settings
self._graph
self._sessions
self._checkpoint
self._memory
self._tool_registry
```

FastAPI 不应该出现下面的调用：

```python
application.session_repository.save(...)
application.memory.service.add(...)
application.tool_registry.invoke_tool(...)
```

原因是这些调用绕过业务边界，使接口层同时承担存储、Agent 和安全策略。

正确方向是：

```text
FastAPI -> Application method -> implementation detail
```

### 4.2 会话用例

Application 暴露：

```python
create_session()
get_session(session_id)
list_sessions(limit)
```

它内部才使用 `SessionRepository`。Repository 仍然存在，因为持久化需要它，但不再
成为接口层依赖。

这体现了 Service/Application 和 Repository 的区别：

- Application 表达“系统可以完成什么”。
- Repository 表达“数据怎样保存和读取”。

### 4.3 Graph 用例

Application 暴露：

```python
run_turn(session_id, question)
resume_turn(session_id, value)
get_graph_state(session_id)
```

`resume_turn()` 仍使用 LangGraph：

```python
Command(resume=value)
```

Application 没有重新实现 Graph 路由，也没有在外部手动执行工具或保存候选记忆。

### 4.4 Session 元数据同步

Graph checkpoint 保存真实短期对话状态；Session JSON 保存会话目录元数据。

每轮完成后 Application 更新：

```text
message_count
updated_at
checkpoint_backend
recoverable
```

`recoverable` 的含义仍是“进程重启后是否可恢复”，所以只有持久化 checkpointer 且
确实存在对应 thread 时才为真。

---

## 五、ApplicationStatus 为什么存在

原健康检查直接访问：

```python
application.checkpoint.backend
application.memory.backend
application.tool_registry.registrations()
```

这等于接口层知道 Application 的内部资源结构。

本阶段新增不可变数据类：

```python
@dataclass(frozen=True)
class ApplicationStatus:
    checkpoint_backend: str
    checkpoint_persistent: bool
    memory_backend: str
    memory_persistent: bool
    mcp_enabled: bool
    registered_tool_count: int
```

健康检查只调用：

```python
status = application.status()
```

### dataclass(frozen=True) 语法说明

`@dataclass` 会根据字段自动生成初始化、比较和显示方法。

`frozen=True` 表示实例创建后不能修改：

```python
status.memory_backend = "other"  # 会报错
```

状态快照只用于读取，不应被 API 路由改写，因此不可变模型更合适。

---

## 六、资源生命周期

`open_human_chat_application()` 使用 `@contextmanager`：

```python
@contextmanager
def open_human_chat_application(settings):
    with open_checkpointer(...) as checkpoint:
        with open_memory_resource(...) as memory:
            with open_tool_registry(...) as tool_registry:
                graph = build_graph(...)
                yield HumanChatApplication(...)
```

### contextmanager 的意义

一个带 `yield` 的 context manager 分为两段：

```text
yield 前：创建资源
yield 时：应用运行
yield 后：按 with 的逆序关闭资源
```

因此关闭顺序是：

```text
HumanChatApplication 停止被使用
-> ToolRegistry / MCP bridge 关闭
-> Memory Store 关闭
-> Checkpointer 关闭
```

FastAPI lifespan 覆盖整个进程服务周期。资源不会按请求重复创建，也不会存在 CLI
入口在 lifespan 外另开一套连接。

---

## 七、为什么删除 CLI

删除文件：

```text
main.py
human_chat/cli/__init__.py
human_chat/cli/app.py
human_chat/cli/commands.py
human_chat/cli/debug.py
human_chat/cli/interrupts.py
```

同时删除只验证这些入口的测试：

```text
tests/test_cli_app.py
tests/test_cli_commands.py
```

### 不保留兼容入口的原因

保留 CLI 会产生两套产品交互：

```text
Web API 交互
CLI input()/print() 交互
```

工具审批、长期记忆审批、会话选择、错误展示都必须分别维护。任何行为变化都要修改
两套代码并防止语义不一致。

当前目标已经明确为前后端分离，所以 CLI 不再是需要兼容的公开产品入口。历史提交和
旧资料仍能解释它曾经如何工作，不需要让废弃代码继续参与安装和维护。

### 删除而不是注释

把旧代码注释掉或放到 `legacy/` 目录不会降低复杂度：

1. 静态搜索仍会找到它。
2. 新手仍然要判断它是否有效。
3. 依赖仍可能被误保留。
4. 安全修复可能遗漏旧入口。

Git 已经保存历史，因此真正不再需要的代码应删除。

---

## 八、为什么删除旧语音链

删除文件：

```text
human_chat/audio_recorder.py
human_chat/input_provider.py
human_chat/stt.py
human_chat/tts.py
```

### 8.1 麦克风录音属于客户端职责

旧代码使用服务器进程的 `sounddevice` 读取麦克风。

前后端分离后，用户操作的是浏览器所在设备。后端服务器的麦克风不是用户的麦克风，
甚至多数服务器没有音频设备。

未来若增加语音输入，应由浏览器获取用户授权和音频，再上传或流式发送给后端。

### 8.2 服务器扬声器播放不是 Web TTS

旧 Graph 的每轮固定路径为：

```text
finalize_reply
-> 调用 GPT-SoVITS
-> 写 speech/tmp.wav
-> simpleaudio 在服务器播放
-> extract_memory
```

问题包括：

1. 声音在服务器播放，不一定在用户设备播放。
2. 播放完成前 Graph 被阻塞。
3. 没启动 TTS 服务时，每轮文本聊天都会产生额外失败。
4. HTTP 后端不应假设存在声卡。

因此删除 `synthesize_speech` Graph 节点，路径改为：

```text
finalize_reply -> extract_memory
```

未来 Web TTS 应设计为音频响应、音频流，或浏览器端语音合成，而不是恢复本地播放。

### 8.3 角色配置同步清理

`CharacterTtsConfig` 和 `characters/nanami.yaml` 中的 `tts` 块只被旧 TTS 客户端使用。
删除 TTS 后继续保留这些字段会成为无消费者配置，因此同步删除。

角色配置现在只负责：

```text
id
name
reply_language
system_prompt
```

这是文本 Agent 当前真正需要的内容。

---

## 九、配置与依赖清理

删除的环境配置：

```text
HUMANCHAT_STT_MODEL
HUMANCHAT_STT_BASE_URL
HUMANCHAT_MIC_RECORD_SECONDS
HUMANCHAT_MIC_SAMPLE_RATE
HUMANCHAT_AUDIO_TEMP_DIR
HUMANCHAT_SPEECH_OUTPUT_PATH
HUMANCHAT_TTS_SERVICE_URL
HUMANCHAT_TTS_AUTO_START
GPT_SOVITS_DIR
GPT_SOVITS_PYTHON
GPT_SOVITS_API_SCRIPT
```

删除的 Python 依赖：

```text
requests
simpleaudio
openai
sounddevice
```

这些包在当前后端代码中已没有 import。

说明：模型聊天仍使用 `langchain-openai`。删除这里单独的 `openai` 条目，不等于禁用
OpenAI-compatible 模型；`langchain-openai` 会管理它自己的底层依赖。

`.gitignore` 中旧麦克风输入目录规则同步删除。`speech/tmp.wav` 规则暂时保留，仅用于
忽略开发机上历史版本已经生成的本地文件；新代码不会再创建或读取它。

---

## 十、CLI 工具元数据清理

工具框架中原有：

```python
class CliCommandSpec:
    command: str
    usage: str
```

`RegisteredTool` 还保存 `cli`，`ToolRegistry` 还建立 `_by_command` 索引。

删除 CLI 后，这些字段没有消费者，继续保留会让开发者误以为工具同时支持 CLI 命令。

本阶段删除：

```text
CliCommandSpec
RegisteredTool.cli
ToolMetadata.command
ToolMetadata.usage
ToolRegistry._by_command
get_registration_by_command()
get_metadata_by_command()
CLI command 重复校验
```

保留：

```text
工具名索引
Provider 加载
来源 metadata
read_only / requires_confirmation 策略
工具调用
本地工具与 MCP 工具统一注册
```

这说明清理并不是删除工具能力，而是删除已经失效的“工具到 CLI 命令”适配层。

---

## 十一、FastAPI 依赖注入更新

原别名：

```python
ChatApplicationDependency
```

改为：

```python
HumanChatApplicationDependency = Annotated[
    HumanChatApplication,
    Depends(get_human_chat_application),
]
```

FastAPI lifespan 保存：

```python
application.state.human_chat_application
```

路由拿到的是同一个进程级 Application，而不是每个请求重新构造。

`Annotated` 保留 Python 类型信息，`Depends` 告诉 FastAPI 如何获取实例。

---

## 十二、README 重写

旧 README 同时描述：

```text
python main.py
CLI 会话选择
/memory 命令
/files 命令
麦克风输入
STT 配置
GPT-SoVITS 自动启动
本地播放失败行为
```

这些说明和新代码不再一致。

本阶段重写 README，只保留：

1. Web 后端架构。
2. `python -m human_chat.api` 启动方式。
3. 模型、API、Checkpoint、Memory、MCP 配置。
4. 当前迁移状态和下一步。

文档必须反映当前可运行代码，而不是保存所有历史功能说明。历史设计仍在 `资料/`
和 Git 历史中可查。

---

## 十三、主要文件变化

### 新增

```text
human_chat/application.py
资料/51-前后端分离阶段二Application收口与旧入口清理修改记录.md
```

### 删除

```text
main.py
human_chat/runtime.py
human_chat/cli/*
human_chat/audio_recorder.py
human_chat/input_provider.py
human_chat/stt.py
human_chat/tts.py
tests/test_cli_app.py
tests/test_cli_commands.py
```

### 重点修改

```text
human_chat/api/app.py
human_chat/api/dependencies.py
human_chat/api/routes/health.py
human_chat/graph.py
human_chat/schemas.py
human_chat/character.py
human_chat/config.py
human_chat/tool_provider.py
characters/nanami.yaml
.env.example
.gitignore
requirements.txt
README.md
```

---

## 十四、验证结果

本阶段没有新增测试文件。

删除两个 CLI 测试，是因为被测产品入口已经删除；保留它们只会验证不存在的行为。

### 14.1 编译

```powershell
python -m compileall -q human_chat
```

结果：通过。

### 14.2 现有有效测试

```powershell
python -m pytest -q
```

结果：

```text
30 passed
```

覆盖的后端模块包括 Checkpointer、配置、Graph、记忆、Session Repository、
ToolProvider 和本地工具。

### 14.3 依赖检查

```powershell
python -m pip check
```

结果：没有损坏的依赖。

### 14.4 实际 FastAPI lifespan

使用临时 SQLite、临时 Session/Memory 路径和替身模型启动真实 TestClient：

```text
GET /api/v1/health -> 200
status -> ok
checkpoint_backend -> sqlite
```

这验证了：

1. FastAPI 能从新模块导入 Application。
2. lifespan 能打开完整资源。
3. 健康检查只通过 `ApplicationStatus` 工作。
4. lifespan 退出后资源正常关闭。

### 14.5 差异检查

```powershell
git diff --check
```

结果：无空白错误。

---

## 十五、没有在本阶段做的事情

本阶段刻意没有添加会话 HTTP 路由和前端。

原因是分步提交需要保证每一步只有一个清楚主题：

```text
本阶段：确定唯一 Application 边界并清理旧路径
下一阶段：在这个边界上增加会话和流式 HTTP 协议
再下一阶段：让精简前端消费协议
```

如果边界清理和全部 API/前端混在同一个提交，后续出现问题时很难定位是架构迁移、
协议设计还是界面实现造成的。

---

## 十六、阶段结论

本阶段之后，HumanChat 不再同时扮演 CLI 程序和 Web 服务。

它现在具有明确的后端形态：

```text
FastAPI 是接口适配器
HumanChatApplication 是用例边界
LangGraph 是 Agent 状态机
Repository / Store / Provider 是私有实现细节
```

同时删除了前后端分离后位置错误的本地音频功能，以及 CLI 留在工具框架中的残余字段。

下一阶段可以在不暴露 Repository 和 Graph 内部状态的前提下，为
`HumanChatApplication` 增加会话列表、历史消息、流式对话、取消与 interrupt 审批的
版本化 HTTP API。
