# 49 MCP 多 Server 并发工具发现修改记录

## 一、本次修改目标

本次修改解决 HumanChat 在启用多个 MCP Server 时，启动阶段工具发现被串行等待的
问题。

修改前，每个 Server 都单独调用一次：

```python
self._bridge.run(
    self._discover_tools(server_name, connection),
    timeout=server.startup_timeout_seconds,
    operation=...,
)
```

`McpAsyncBridge.run()` 会通过 `future.result(timeout=...)` 同步等待当前异步任务。
因此 `load_tools()` 中的普通 `for` 循环会形成以下执行顺序：

```text
提交 Server A -> 等待 A 完成 -> 提交 Server B -> 等待 B 完成 -> 提交 Server C
```

虽然 `McpAsyncBridge` 的后台 event loop 具备并发能力，但下一台 Server 的协程只有在
上一台 Server 返回后才会被提交，所以没有真正利用 asyncio 的并发等待能力。

本次修改后的目标是：

```text
一次提交批量发现协程
        |
        v
后台 event loop 创建多个具名 Task
        |
        +--> Server A 连接和工具发现
        +--> Server B 连接和工具发现
        +--> Server C 连接和工具发现
        |
        v
按配置顺序处理结果并注册工具
```

## 二、为什么需要修改

### 1. 串行等待会线性增加启动时间

假设三台 Server 的工具发现分别耗时 5 秒、4 秒和 3 秒：

```text
串行发现：约 5 + 4 + 3 = 12 秒
并发发现：约 max(5, 4, 3) = 5 秒
```

如果第一台 Server 不可用并在 15 秒后超时，后面的健康 Server 也必须先等待这 15
秒才能开始连接。这会让一个故障依赖拖慢全部 MCP 能力。

### 2. `future.result()` 本身不是错误

HumanChat 当前的 Graph、CLI 和 Runtime 使用同步入口，所以仍然需要一个位置把异步
MCP API 转换成同步结果。`future.result()` 的职责是正确的：

```text
同步线程等待结果
        |
        v
后台线程中的 asyncio event loop 执行异步任务
```

真正的问题是过去每次只给它一个 Server 协程。正确做法不是删除同步桥接，而是让
同步桥接等待一个“内部已经并发”的批量协程。

### 3. 商业项目不能只追求并发速度

直接把所有 Server 无限制地同时启动也不够成熟，尤其是 `stdio` transport 可能会
创建本地子进程。因此重构还必须同时保证：

1. 每台 Server 有独立启动超时。
2. 并发数量有明确上限。
3. 单台 Server 失败可以被隔离。
4. 必需依赖失败时可以立即终止。
5. 未完成 Task 在异常和取消时会被清理。
6. 工具注册顺序不因网络完成顺序而变化。

## 三、总体设计

### 1. 同步层只提交一次

`McpToolProvider.load_tools()` 不再在普通 `for` 循环中逐台执行 `_bridge.run()`，而是
改为：

```python
discoveries = self._bridge.run(
    self._discover_enabled_servers(enabled_servers),
    timeout=self._discovery_batch_timeout(enabled_servers),
    operation="MCP Server 批量工具发现",
)
```

此时主线程仍然会在 `future.result()` 处等待，但后台 event loop 已经获得整个 Server
集合，可以同时推进多个发现任务。

### 2. event loop 内显式创建具名 Task

批量协程为每台 Server 创建一个 Task：

```python
asyncio.create_task(
    self._discover_server_tools(...),
    name=f"mcp-discovery:{server_name}",
)
```

显式创建 Task 而不是只保存协程对象，具有以下意义：

1. Task 名称可以直接定位到具体 Server。
2. fail-fast 时可以单独取消未完成 Task。
3. 关闭资源时可以检查 Task 是否完成。
4. 调试工具能够看到明确的异步任务身份。

调用 `create_task()` 会把协程调度到当前正在运行的 MCP event loop。Task 真正推进发生在
批量协程执行到 `await asyncio.gather(...)`、把控制权交还给 event loop 之后。

### 3. 使用 `asyncio.gather` 收集结果

非 fail-fast 模式使用：

```python
await asyncio.gather(
    *tasks,
    return_exceptions=True,
)
```

`return_exceptions=True` 会把单台 Server 的普通异常放在对应结果位置，而不是让第一个
异常丢掉其他成功结果。例如：

```text
[
    Server A 的工具列表,
    Server B 的超时异常,
    Server C 的工具列表,
]
```

`gather` 的结果顺序与传入 Task 顺序一致，不取决于完成顺序。因此即使 C 最先完成，
同步注册阶段仍然按照 A、B、C 的配置顺序处理。

## 四、并发上限设计

### 1. 新增配置字段

`McpConfig` 顶层新增：

```json
{
  "version": 1,
  "max_concurrent_server_discoveries": 4,
  "servers": {}
}
```

字段约束为：

```text
默认值：4
最小值：1
最大值：32
```

旧配置没有该字段时自动使用默认值 4，不需要升级配置版本。

### 2. 为什么使用 Semaphore

Provider 创建：

```python
semaphore = asyncio.Semaphore(concurrency)
```

每台 Server 只有进入：

```python
async with semaphore:
```

后才会真正建立连接。这样即使配置文件包含很多 Server，也不会在同一时刻创建过多
HTTP 连接或本地 `stdio` 子进程。

所有 Task 仍然会被创建，但超过上限的 Task 会异步等待 semaphore，不会占用线程，也
不会进行 MCP 网络连接。

## 五、超时语义

本次设计保留两层超时，但两层职责不同。

### 1. 单 Server 启动超时

每台 Server 使用：

```python
await asyncio.wait_for(
    self._discover_tools(...),
    timeout=server.startup_timeout_seconds,
)
```

这个超时只在 Server 获得 semaphore 并开始连接后计时。排队等待并发槽位不会消耗它的
启动预算。

超时后会产生带 Server 名称和秒数的 `McpOperationTimeout`，方便日志定位。

### 2. 批量桥接安全超时

同步层的 `McpAsyncBridge.run()` 仍然需要一个总超时。批量安全上界使用：

```text
所有已启用 Server 的 startup_timeout_seconds 之和 + 5 秒清理宽限
```

这是保守的最后防线，不代表正常情况下会等待这么久。任务完成后 `future.result()` 会
立即返回；正常并发耗时仍接近最慢并发批次的耗时。

使用总和可以覆盖 `max_concurrent_server_discoveries=1` 的合法配置，也为 Task 取消和
transport 清理保留时间。

## 六、失败处理语义

### 1. `fail_fast=False`

适合把 MCP Server 视为可选外部能力的场景：

```text
某台 Server 失败
    -> 异常作为 gather 结果返回
    -> 记录该 Server 完整异常日志
    -> 跳过该 Server
    -> 继续注册其他健康 Server
```

### 2. `fail_fast=True`

适合所有 MCP Server 都是启动必需依赖的场景：

```text
任一 Server 首次失败
    -> gather 向上抛出异常
    -> finally 取消仍未完成的 Task
    -> 等待取消清理完成
    -> HumanChat 启动失败
```

由于普通 `asyncio.gather` 在首个异常时不会替调用者取消其他子任务，代码在 `finally`
中显式执行 `task.cancel()` 并等待这些 Task 结束，避免后台继续连接或遗留子进程。

### 3. Adapter 依赖缺失

`langchain-mcp-adapters` 缺失属于整个进程的依赖问题，而不是某一台 Server 的临时
故障。因此无论 `fail_fast` 是否开启，都直接抛出 `McpDependencyError`。

## 七、发现与注册职责拆分

原 `_load_server_tools()` 同时负责网络发现和本地注册。并发化后拆分为：

```text
_discover_server_tools()
    -> 环境变量和 cwd 解析
    -> semaphore 并发控制
    -> MCP 连接
    -> 单 Server 超时

_register_server_tools()
    -> 工具名前缀还原
    -> include/exclude 过滤
    -> 配置引用校验
    -> 同步调用入口注入
    -> ToolPolicy 解析
    -> RegisteredTool 构建
```

这样异步阶段只做 I/O，注册阶段只做确定性的本地转换，职责更加清晰。

## 八、兼容性与行为边界

本次修改保持以下行为不变：

1. MCP 默认仍然关闭。
2. 本地工具不依赖 MCP 配置和 Adapter。
3. 每台 Server 仍使用自己的 include/exclude 和安全策略。
4. MCP 工具名仍添加 Server 前缀。
5. 工具调用仍使用各自的 `tool_timeout_seconds`。
6. 高风险工具仍进入 LangGraph 人工审批节点。
7. 不新增测试文件。

发生变化的是启动阶段：多台启用的 Server 从串行发现改为受控并发发现。

## 九、修改文件

### `human_chat/mcp_provider.py`

1. 批量提交全部启用 Server 的发现协程。
2. 为每台 Server 创建具名 asyncio Task。
3. 使用 gather 并发收集结果。
4. 使用 Semaphore 控制最大并发量。
5. 使用 wait_for 保留逐 Server 启动超时。
6. 补充 fail-fast 取消和非 fail-fast 故障隔离。
7. 拆分发现与注册职责。

### `human_chat/mcp_config.py`

新增并校验 `max_concurrent_server_discoveries`。

### `config/mcp_servers.example.json`

展示并发上限配置。

### `README.md`

说明并发发现、排队超时语义和两种失败策略。

## 十、验证记录

### 1. Python 编译检查

执行：

```powershell
python -m compileall -q human_chat
```

结果：通过，无语法错误。

### 2. 项目现有测试

执行：

```powershell
python -m pytest -q
```

结果：

```text
37 passed
```

本次没有新增或修改测试文件。

### 3. 多 Server 并发耗时验证

使用三个模拟 Server，每台异步等待 0.20 秒，并发上限设置为 3。

结果：

```text
parallel: elapsed=0.206s max_active=3
```

如果仍为串行执行，理论耗时应接近 0.60 秒。实际约 0.20 秒，且同时活跃任务数为
3，证明 Server 发现任务确实在后台 event loop 中并发推进。

### 4. 并发上限验证

使用四个模拟 Server，每台异步等待 0.15 秒，并发上限设置为 2。

结果：

```text
limit: elapsed=0.319s max_active=2
```

任务分为两批完成，最大同时活跃数没有超过 2，证明 Semaphore 上限生效。

### 5. 超时与健康 Server 隔离验证

配置一台 0.05 秒超时的慢 Server，以及一台 0.02 秒完成的健康 Server，使用
`fail_fast=False`。

结果：

```text
isolation: elapsed=0.062s loaded=1 cancelled=['slow']
```

慢 Server 被超时取消，健康 Server 正常计入已加载数量，证明单 Server 超时不会阻断
其他发现任务。

### 6. fail-fast 取消验证

配置一台在 0.03 秒后失败的 Server，以及一台需要 1 秒完成的慢 Server，使用
`fail_fast=True`。

结果：

```text
fail_fast: elapsed=0.034s error=RuntimeError cancelled=['slow']
```

首个失败立即向上传播，未完成的慢 Server 被取消，没有继续等待完整 1 秒。

### 7. 真实 MCP Adapter 联调

使用两个不同 Server 名称并发连接官方 LangChain 文档 MCP HTTP 地址。

结果：

```text
real_mcp: elapsed=3.041s servers=2 tools=6
```

两个 Server 均加载成功，共注册 6 个工具；所有工具名分别具有 `docs_a_` 或
`docs_b_` 前缀，没有名称冲突。

### 8. 配置兼容性与格式检查

示例配置成功解析为：

```text
max_concurrent_server_discoveries = 4
server_count = 2
```

`git diff --check` 通过，未发现空白符错误。
