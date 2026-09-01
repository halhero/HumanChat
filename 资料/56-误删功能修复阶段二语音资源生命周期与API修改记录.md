# 误删功能修复阶段二：语音资源生命周期与 API 修改记录

## 1. 本阶段目标

阶段一恢复了角色音色、STT/TTS 配置和供应商适配器。本阶段把这些能力正式纳入
HumanChat 后端：

1. Application 在进程启动时创建语音资源，在关闭时统一释放；
2. 角色配置只加载一次，并同时提供给 Graph 和语音模块；
3. 外部 TTS 已经运行时直接使用，不重复启动，也不在退出时关闭；
4. 只有开启自动启动且服务不可用时，HumanChat 才创建本地 GPT-SoVITS 进程；
5. FastAPI 只调用 Application 用例，不直接持有语音客户端；
6. 提供语音能力查询、音频转写和文本合成三个 HTTP 接口；
7. 对上传大小、音频类型、文本长度和外部服务错误建立明确边界。

这一阶段仍然不修改 React。先稳定后端契约，下一阶段前端只需要消费 API，不需要了解
OpenAI SDK、GPT-SoVITS 参数或 Python 子进程。

## 2. VoiceResource 的职责

新增的 `VoiceResource` 与现有 `MemoryResource`、`CheckpointerResource` 采用相同思路：

```python
@dataclass(frozen=True)
class VoiceResource:
    stt: SpeechToTextService | None
    tts: TextToSpeechService | None
    character: Character
    tts_process_owned: bool
```

它表示“当前进程可用的一组语音资源”，而不是新的业务 Service。真正的业务操作仍由
STT/TTS Service 提供，Resource 负责组合它们和记录进程所有权。

字段可以为空，是因为语音是可选能力：

- 没有 API key 或 STT model 时，文本聊天仍能启动；
- 当前角色没有 `tts` 时，文本聊天仍能启动；
- TTS 服务暂时离线时，聊天 Graph 仍然可用。

Application 通过稳定的业务异常向接口层说明某项能力不可用，而不是让整个后端进程因
可选语音服务离线而失败。

## 3. Context Manager 与资源生命周期

`open_voice_resource()` 使用 `@contextmanager`：

```text
进入上下文
  -> 创建 STT 客户端
  -> 创建 TTS 客户端
  -> 按配置决定是否启动本地 TTS 进程
  -> yield VoiceResource

退出上下文
  -> 只停止 HumanChat 自己创建的 TTS 进程
  -> 关闭 TTS HTTP 连接池
  -> 关闭 STT SDK 连接池
```

`yield` 前建立资源，`finally` 中释放资源，可以保证正常退出、异常退出和 FastAPI shutdown
都执行相同的清理逻辑。HTTP Client 持有连接池；显式 `close()` 可以及时释放 keep-alive
连接和系统句柄。

## 4. 后台已有 TTS 服务如何处理

这是本次修复的核心语义。

### 4.1 `tts_auto_start=false`

Application 不执行启动探测，也不创建子进程。后续收到语音合成请求时，TTS Client 仍然
会请求：

```text
HUMANCHAT_TTS_SERVICE_URL/tts
```

因此用户已经在后台启动 GPT-SoVITS 时，功能可以直接使用。服务未运行时，只影响当前
语音请求，不延迟后端启动，也不影响文本聊天。

### 4.2 `tts_auto_start=true`

资源管理器先探测配置的服务地址：

- 服务已经存在：直接复用，`owned_process=None`；
- 服务不存在：校验目录、Python 和 API 脚本，然后创建子进程；
- 子进程在超时前可访问：记录为 HumanChat 所有；
- 子进程提前退出或启动超时：清理进程并让启动失败，避免留下后台残留进程。

后端关闭时只有 `owned_process` 非空才执行 terminate/kill。即使
`tts_auto_start=true`，预先存在的外部服务也不会被误关。

## 5. 为什么角色配置只加载一次

以前 `build_graph()` 内部读取角色文件。语音恢复后，如果 VoiceResource 再读取一次，就会
出现同一个进程中两份可能不一致的 Character 对象。

现在 `open_human_chat_application()` 在组合资源前加载一次：

```python
character = load_character(settings.character_path)
```

随后把它传给：

- `build_graph(..., character=character)`：生成文本行为；
- `open_voice_resource(settings, character)`：生成角色语音。

`build_graph` 仍保留可选加载逻辑，已有独立调用和现有测试不必立即改变；正式 Application
路径则保证单次加载和一致快照。

## 6. Application 为什么不暴露 VoiceResource

FastAPI 依赖注入仍然只提供 `HumanChatApplication`。新增的公开用例是：

```python
voice_capabilities()
transcribe_audio(...)
synthesize_speech(text)
```

路由不能访问：

- `OpenAI` Client；
- `httpx.Client`；
- GPT-SoVITS 子进程；
- Character 内部对象；
- TTS 供应商 payload。

这与项目此前确定的 Repository 封装原则相同：实现资源保留在 Application 内部，上层只
调用业务语义。将来更换 STT/TTS 供应商时，API 路由无需修改。

## 7. 新增 HTTP 接口

所有接口位于 `/api/v1/voice`。

### 7.1 查询能力

```http
GET /api/v1/voice/capabilities
```

返回：

```json
{
  "stt_enabled": true,
  "tts_enabled": true,
  "tts_available": true,
  "tts_auto_start": false,
  "max_audio_bytes": 26214400
}
```

`tts_enabled` 表示角色和服务地址已经配置；`tts_available` 表示当前服务地址可以连接。
二者分开后，前端能区分“没有这个功能”和“功能已配置但服务暂时离线”。

核心 `/health` 只返回 `stt_enabled`、`tts_enabled`，不会每次为了可选 TTS 服务执行网络
探测。这样可选语音故障不会把核心聊天服务误判为不健康。

### 7.2 上传音频转写

```http
POST /api/v1/voice/transcriptions
Content-Type: multipart/form-data
audio=<binary>
```

返回：

```json
{"text": "识别出的文字"}
```

FastAPI 的 `UploadFile` 负责解析 multipart。接口异步读取有限长度的数据，再通过
`run_in_threadpool()` 调用同步 STT SDK，避免外部网络请求阻塞 ASGI 事件循环。

### 7.3 文本合成音频

```http
POST /api/v1/voice/speech
Content-Type: application/json

{"text": "待朗读内容"}
```

成功响应直接是音频字节，并沿用 TTS 服务返回的 media type。响应设置：

```text
Cache-Control: no-store
```

聊天内容可能包含私人信息，不应被共享代理或浏览器长期缓存为可复用静态资源。

## 8. 上传安全和资源限制

新增配置：

```env
HUMANCHAT_STT_MAX_AUDIO_BYTES="26214400"
```

默认限制为 25 MiB，Pydantic 同时限制配置范围。接口只读取 `limit + 1` 字节：

- 等于或小于限制：继续转写；
- 大于限制：立即返回 HTTP 413；
- 空内容：返回 HTTP 400。

音频 MIME 类型采用明确白名单，覆盖 WAV、MP3、MP4/M4A、FLAC、OGG 和浏览器常见的
WebM。浏览器可能发送 `audio/webm;codecs=opus`，代码会先提取分号前的基础类型再校验。

上传文件名只作为供应商 API 元数据。代码去除目录部分并截断到 255 字符，不会把用户
文件名拼接为服务器磁盘路径。

## 9. HTTP 错误语义

接口使用项目统一的 `ApiError` 信封：

```text
400 empty_audio              音频为空
413 audio_too_large          超过上传限制
415 unsupported_audio_type   MIME 类型不受支持
503 transcription_failed     STT 未配置或供应商调用失败
503 speech_synthesis_failed  TTS 未配置、离线或合成失败
```

外部异常会写入服务器日志并保留异常链；客户端只获得稳定错误码和可读消息，不会得到 SDK
堆栈、密钥或内部对象。

## 10. 为什么语音不重新进入 LangGraph

Graph 的职责仍然是：

- 对话推理；
- 工具调用；
- 长期记忆提取和审核；
- 会话 checkpoint。

STT 发生在用户消息进入 Graph 之前，TTS 发生在文本回答产生之后。把二者做成独立 API
有以下好处：

1. 用户可以检查转写文字后再发送；
2. 同一条回答可以按需朗读，不必重新执行 Graph；
3. TTS 故障不改变已经成功生成的文本回答；
4. 浏览器可以停止播放，而不需要取消 Agent 推理；
5. 文本客户端完全不承担语音成本。

## 11. 依赖调整

增加 `python-multipart` 作为直接依赖。FastAPI 解析 `multipart/form-data` 文件上传需要该
包；只安装 FastAPI 本身并不会自动获得这项能力。

## 12. 本阶段修改文件

新增：

- `human_chat/voice/resources.py`：语音资源组合、进程探测、自动启动和清理；
- `human_chat/api/routes/voice.py`：能力、转写和合成接口。

修改：

- `human_chat/application.py`：封装语音用例和资源；
- `human_chat/graph.py`：允许复用已加载的 Character；
- `human_chat/api/app.py`：注册 voice router；
- `human_chat/api/models.py`：语音公开请求响应模型；
- `human_chat/api/routes/health.py`：增加非阻塞语音配置状态；
- `human_chat/config.py`、`.env.example`：增加音频上传上限；
- `human_chat/voice/stt.py`：显式关闭 SDK Client；
- `human_chat/voice/__init__.py`：导出 Resource；
- `.gitignore`：忽略项目内隔离的 pytest 临时目录；
- `requirements.txt`：声明 multipart 依赖。

## 13. 验证方式

执行：

```powershell
git diff --check
python -m compileall -q human_chat
python -c "from human_chat.api.app import create_api; ... app.openapi() ..."
```

OpenAPI 中已确认存在：

```text
/api/v1/voice/capabilities
/api/v1/voice/transcriptions
/api/v1/voice/speech
```

第一次执行现有 pytest 时，本机全局目录
`AppData/Local/Temp/pytest-of-10721` 返回 Windows `PermissionError`，错误发生在
`tmp_path` fixture 创建阶段，不是断言失败。随后改用项目内独立临时目录执行：

```powershell
python -m pytest -q --basetemp=data/pytest-tmp-stage56 -p no:cacheprovider
```

最终结果为 `30 passed`。这既绕开了损坏的全局临时目录权限，也确认当前全部既有测试没有
因语音资源接入产生回归。

## 14. 下一阶段

阶段三将实现浏览器语音交互：

1. 查询后端语音能力；
2. 使用 `MediaRecorder` 录制浏览器麦克风；
3. 支持选择已有音频文件；
4. 上传并把转写结果放入输入框，由用户确认后发送；
5. 为助手消息提供按需朗读；
6. 管理 Audio URL、播放切换和组件卸载清理。
