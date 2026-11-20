# 双执行模式第一步：Triage 与执行契约修改记录

日期：2026-11-20。

## 1. 本步解决什么问题

HumanChat 原来的 `understand_request` 只生成完整请求或澄清问题。后续无论任务是一次即可完成，还是必须维护多步执行结果，都会进入同一套手写 ReAct 循环。

本步只建立双执行模式的入口和数据契约，不提前改执行 Graph：

```text
原始输入 + 有界近期对话 + 必要时的一次记忆辅助
                         ↓
                  understand_request
                         ↓
       resolved_request + execution_mode
                         或
             clarification_question
```

其中 `execution_mode` 只有两个合法值：

```text
react
plan_execute
```

这一步的意义是先把“系统究竟依据什么进入哪条执行路径”定义清楚。后续 Agent 和 Graph 只消费这个决定，不在执行过程中重新猜测模式。

## 2. 代码事实核对

实施前实际环境为：

| 组件 | 已安装版本 |
| --- | --- |
| Python（原 PyCharm 环境） | 3.10.20 |
| LangChain | 未安装 |
| langchain-core | 1.4.0 |
| LangGraph | 1.2.0 |
| langchain-openai | 1.2.1 |

当前项目已经使用 `enum.StrEnum`，它要求 Python 3.11 及以上，所以原 PyCharm 的 Python 3.10 环境无法加载完整应用。验证改用机器现有的 Python 3.13 隔离环境，没有为了兼容错误环境去改业务代码。

本次要求使用的 `create_agent`、`HumanInTheLoopMiddleware` 和 `ToolCallLimitMiddleware` 位于 `langchain` 包，而仓库此前只声明了 `langchain-core`。因此不补充 `langchain` 就无法实现批准的架构。

实际解析还证明，只锁 `langchain` 不足以形成可复现环境：干净安装会把无上限的 `langchain-core` 和 `langchain-openai` 升到更高版本，代码验证基线随安装时间变化。最终把本次核对和验证的四个框架版本一起固定：

```text
langchain-core==1.4.0
langchain==1.3.1
langchain-openai==1.2.1
langgraph==1.2.0
```

这不是为了追求最新版本，而是保证后续官方 Agent、结构化响应和 HITL 适配基于同一组已验证 API。

## 3. 为什么由 Triage 选择执行模式

执行模式依赖的是用户请求的完整语义，而不是原始句子的长度或某个关键词。

例如：

```text
“继续”
```

原句本身无法判断应该直接回答还是执行多步任务。只有先结合近期对话和必要的跨会话记忆，把它补全为自包含请求，才能选择执行方式。

因此顺序必须是：

```text
先完成语义补全
再在同一个 resolved 结果中给出 execution_mode
```

不能让 ReAct Agent 或 Planner 再分类，否则同一回合会出现多个相互竞争的模式决定，恢复时也难以确认哪一个才是权威值。

## 4. Triage 的模式边界

`react` 是默认模式，适用于：

- 直接回答和普通对话；
- 一次可以整体完成的任务；
- 不需要显式保存有序步骤和中间结果的工具任务。

`plan_execute` 只用于：

- 多个步骤存在明确先后依赖；
- 后续步骤必须读取前序步骤结果；
- 中间结果很可能改变剩余执行计划。

Prompt 明确禁止仅根据以下因素选择 Plan-Execute：

- 请求文本很长；
- 任务看起来重要；
- 可能使用多个工具；
- 模型难以给任务分类。

执行模式是系统内部决定，不得因为模式选择困难而向用户澄清。真正需要澄清的仍然只是用户目标、对象、关键指代或必要参数不明确。

## 5. 两阶段 Triage 如何保持一致

第一次 Triage 的三个互斥结果现在是：

```json
{
  "outcome": "resolved",
  "resolved_request": "...",
  "execution_mode": "react"
}
```

```json
{
  "outcome": "search_memory",
  "memory_query": "..."
}
```

```json
{
  "outcome": "clarify",
  "clarification_question": "..."
}
```

如果第一次选择 `search_memory`，第二次 Triage 在看完候选后也必须输出一个互斥结果：

```text
resolved + resolved_request + execution_mode
或
clarify + clarification_question
```

第二次没有 `search_memory` 分支，所以程序结构仍保证最多召回一次。模式选择发生在语义真正补全之后，而不是在生成 `memory_query` 时提前猜测。

## 6. Plan 数据模型

新增 `human_chat/execution.py`，只保存执行层共享的核心数据契约。

### PlanStep

```python
class PlanStep(BaseModel):
    instruction: str
    success_criteria: str
    review_after: bool
```

- `instruction`：当前步骤实际要完成什么；
- `success_criteria`：什么可观察结果才算完成；
- `review_after`：本步结果是否可能改变后续计划。

没有加入步骤 ID、依赖图、并发信息或工具绑定，因为首版计划是严格有序的线性列表，当前步骤永远是剩余列表的第一项。

### StepResultSubmission

```python
class StepResultSubmission(BaseModel):
    status: Literal["completed", "failed", "blocked"]
    result: str
```

`result` 禁止为空。它必须在子 Agent 内部消息不保存到父 Graph 的情况下，仍能让后续步骤和 Replan 理解本步取得了什么事实、产物或失败信息。

### StepExecutionRecord

它把实际执行过的 `step` 与对应 `result` 组合成一条不可改写的历史记录。未来 `len(step_history)` 就是唯一执行次数，不再增加重复计数字段。

### ExecutionPlan 与 ReplanDecision

初始计划至少包含一个步骤。Replan 只允许三个互斥结果：

```text
continue
revise + 非空的完整剩余步骤快照
finish
```

`revise` 不允许空列表；如果不再需要步骤，模型必须明确选择 `finish`。结构化边界已经能表达这个规则，不再额外增加 Verifier。

## 7. 集中额度配置

新增默认值：

| 配置 | 默认值 | 作用 |
| --- | ---: | --- |
| `react_tool_call_limit` | 3 | 一次普通 ReAct 请求允许的工具调用量 |
| `plan_initial_max_steps` | 6 | Planner 首次生成的最大步骤数 |
| `plan_initial_execution_limit` | 4 | 用户无需再次确认即可自动执行的步骤尝试数 |
| `plan_execution_limit_increment` | 3 | 用户选择继续时增加的固定额度 |
| `plan_hard_execution_limit` | 12 | 一个任务绝不再继续执行的硬上限 |
| `plan_step_tool_call_limit` | 4 | 单个 PlanStep 内的业务工具调用上限 |

这些概念不能合并：计划最多生成多少步、当前获准自动执行多少步、整个任务最多尝试多少步，以及单步内部可调用多少次工具，约束的是四个不同层级。

配置只在 `Settings` 边界检查两条真实不变量：初始计划数和初始执行额度都不能超过硬上限。没有在后续每个节点重复校验同一关系。

## 8. 状态生命周期

`ChatState` 本步只新增：

```python
execution_mode: Literal["react", "plan_execute"] = "react"
```

生命周期为：

| 阶段 | 行为 |
| --- | --- |
| 新回合初始化 | 重置为 `react`，避免继承上一回合模式 |
| `understand_request` | resolved 时写入 Triage 决定 |
| 澄清路径 | 保持默认值，不参与执行 |
| 后续步骤 | Graph 路由读取它选择 ReAct 或 Plan-Execute |

本步没有提前加入 `remaining_steps`、`step_history` 等 Graph 状态；它们会在真正存在生产者和消费者的第三步接入。

## 9. 修改文件

| 文件 | 修改内容 |
| --- | --- |
| `requirements.txt` | 增加并锁定实际验证的 LangChain/LangGraph 版本组 |
| `.env.example` | 记录集中执行额度配置 |
| `human_chat/config.py` | 增加额度字段、环境变量映射和最小不变量校验 |
| `human_chat/execution.py` | 新增 Plan、步骤结果和 Replan 的结构化契约 |
| `human_chat/request_understanding.py` | resolved 分支增加执行模式并完善两阶段 Prompt |
| `human_chat/schemas.py` | 增加当前回合权威执行模式 |
| `human_chat/graph.py` | 在现有初始化和理解节点中重置、写入执行模式 |
| `tests/test_execution.py` | 验证计划与 Replan 的关键结构约束 |
| `tests/test_request_understanding.py` | 验证两阶段 Triage 均能形成唯一执行模式 |
| `tests/test_config.py` | 验证额度边界 |

## 10. 直接验证

契约、Triage 和配置：

```powershell
python -m pytest tests/test_request_understanding.py tests/test_execution.py tests/test_config.py -q
```

结果：

```text
18 passed
```

Graph 中与本步直接相关的四条路径：

```powershell
python -m pytest \
  tests/test_graph.py::test_direct_reply_reuses_first_model_response \
  tests/test_graph.py::test_resolved_request_drives_recall_and_agent_without_replacing_original \
  tests/test_graph.py::test_memory_assisted_understanding_keeps_triage_data_local \
  tests/test_graph.py::test_clarification_is_committed_without_memory_agent_or_tools -q
```

结果：

```text
4 passed
```

测试使用 Fake Model 只能证明结构化结果、状态写入和分支接线，不能证明真实模型对模式边界的判断质量。真实 Prompt 质量仍需要后续用代表性的正反请求进行评估。

## 11. 本步没有修改什么

- 没有接入 Plan Graph 节点；
- 没有构建 `react_agent` 或 `plan_step_agent`；
- 没有改变工具审批、API、SSE 或前端；
- 没有修改 Memory 数据模型、召回策略或写入流程；
- 没有保留第三种执行模式；
- 没有实现 DAG、并行步骤、Verifier 或任务数据库。

下一步会用官方 `create_agent` 替换普通 ReAct 的手写模型—工具循环，并先保持用户可见的工具审批协议不变。
