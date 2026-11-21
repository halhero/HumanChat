# 双执行模式第四步：Plan-Execute 与交互恢复修改记录

日期：2026-11-21。

## 1. 本步完成的结果

本步把 `execution_mode="plan_execute"` 真正接入现有父 `StateGraph`，并完成了与之相关的两类用户交互：

- 单步执行缺少必要用户信息时的 `ask_user`；
- 首次自动执行额度用完后的继续、补充后继续或停止。

最终父 Graph 的两条执行路径是：

```text
build_memory_context
        |
        +-- react --------> run_react_agent ----------------> finalize_reply
        |
        +-- plan_execute -> create_plan
                             |
                             v
                       before_plan_step
                         /     |      \
                    execute  budget   end
                       |        |       |
                       v        v       v
                 step route   resume  summarize_plan_result
                    /   \       |             |
                 next  replan --+             v
                   |      |                finalize_reply
                   +------+ 
```

没有新建 Plan 子图，没有第三种执行模式，也没有引入 DAG、并发调度或独立 Verifier。

## 2. Planner 只做规划

`create_plan` 只读取：

```text
resolved_request
memory_context
tool_capabilities
plan_initial_max_steps
```

`tool_capabilities` 是从当前 `ToolRegistry` 实时构建的紧凑摘要，每个工具只包含：

```text
name
description
requires_confirmation
```

Planner 不绑定工具，不接收完整 JSON Schema，也不生成具体工具参数。这使它只负责判断任务应该如何按顺序拆分，实际工具选择留给单步执行 Agent。

Planner 返回 `ExecutionPlan` 后，程序只做一个必要的边界检查：步骤数不能超过 `plan_initial_max_steps`。如果超过则直接失败，不截断、不自动重试、不在程序中猜测哪些步骤应删除。

## 3. 父 Graph 如何保存计划事实

父状态只保存六个已确认字段：

| 字段 | 作用 |
| --- | --- |
| `remaining_steps` | 当前仍然有效的完整剩余计划 |
| `step_history` | 只追加的真实步骤尝试记录 |
| `authorized_step_limit` | 当前用户已授权的累计尝试上限 |
| `plan_user_context` | 只保存“补充后继续”提交的本轮补充 |
| `step_used_ask_user` | 最近步骤是否真实使用过 `ask_user` |
| `plan_end_reason` | 计划即将总结时的权威结束原因 |

已执行步骤数始终由 `len(step_history)` 得到；当前步骤始终是 `remaining_steps[0]`；最近结果始终是 `step_history[-1].result`。没有为这三个已知信息再增加平行字段。

## 4. 单步执行的边界

`plan_step_agent` 是与 `react_agent` 分离的第二个 `create_agent` 实例。它的差异是：

- 工具集合为“项目工具 + `ask_user`”；
- 最终输出必须是 `StepResultSubmission`；
- 单步工具上限使用 `exit_behavior="error"`；
- 受保护项目工具只允许 `approve | reject`；
- `ask_user` 只允许 `respond | reject`。

每次单步调用只传入：

```text
resolved_request
current_step
completed_step_records
memory_context
plan_user_context
```

返回后，父 Graph 只把当前 `PlanStep` 和结构化结果组成 `StepExecutionRecord`。子 Agent 的 AIMessage、ToolMessage 和结构化提交工具消息都不进入父状态或最终聊天历史。

工具调用超限会被转成 `failed`，普通执行异常也只保存异常类型与紧凑原因。`GraphBubbleUp` 类的中断和控制流异常会继续向上传播，不会被伪装成业务失败。本步没有增加自动重试。

## 5. 步骤结果如何决定下一站

`route_after_plan_step` 严格按以下顺序处理：

1. `blocked` -> 标记阻塞并汇总；
2. `failed` -> Replan；
3. 实际使用过 `ask_user` -> Replan；
4. 当前步骤 `review_after=True` -> Replan；
5. 其他情况 -> 直接回到统一的步骤前路由。

剩余步骤是否为空、软额度是否用完、硬上限是否达到，都只在 `before_plan_step` 判断一次。硬上限的优先级最高，确保不会在已有记录数达到上限后再执行新步骤。

## 6. Replan 只能改剩余计划

Replan 读取完整 `step_history`，但无论返回什么都不能更改它：

- `continue`：保持 `remaining_steps` 不变；
- `revise`：用新的完整剩余快照整体替换 `remaining_steps`；
- `finish`：设置 `plan_end_reason="planner_finished"` 并进入汇总。

完成历史不会因为 Replan 而重新生成，因此最终总结始终能区分“真实执行过的尝试”和“仍然只是计划的工作”。

## 7. `ask_user` 为什么不是新对话回合

`ask_user` 的真实 Python 函数体始终抛出异常。它只能被官方 HITL middleware 拦截，前端提交 `respond` 后，middleware 生成一条 ToolMessage 交还同一次 Step Agent 运行。

因此用户回答后：

- 不创建新 turn；
- 不重新执行 Triage 或 Planner；
- 继续当前 `PlanStep`；
- 完成后只追加一条 `StepExecutionRecord`；
- 由于实际使用过 `ask_user`，之后强制执行 Replan。

`ask_user` 回答不重复写入 `plan_user_context`；它应由当前 Step Agent 纳入结构化步骤结果。

## 8. 计划额度暂停与恢复

首次 `authorized_step_limit` 直接等于 `plan_initial_max_steps`。当历史尝试数达到该值且仍有剩余步骤时，父 Graph 通过自己的 `interrupt()` 产生 `plan_budget` 请求。

恢复分支：

| 用户决定 | 状态更新 | 下一站 |
| --- | --- | --- |
| `continue` | 直接授权到硬上限 | `before_plan_step` |
| `continue_with_input` | 追加非空补充，并授权到硬上限 | `replan_or_finish` |
| `stop` | 设置 `user_stopped` | `summarize_plan_result` |

一旦用户选择继续，本轮就直接授权到硬上限，不存在固定 `+3` 或重复弹窗的续批机制。

## 9. 最终汇总与聊天历史

Plan 路径的结束原因只有：

```text
completed
planner_finished
blocked
user_stopped
hard_limit
```

`summarize_plan_result` 使用无工具 LLM，读取真实历史、剩余步骤、计划补充和结束原因，生成唯一 `assistant_text`。它必须如实区分成功、失败、阻塞、用户停止和硬上限。

最后仍由原有 `finalize_reply` 统一提交：

```text
原始用户 SourceMessage
最终 AIMessage
```

中间计划、工具消息和步骤结果不会被伪装成多条用户可见聊天消息。

## 10. 前后端三类交互

`ReviewRequest` 现在是三个互斥分支的 TypeScript 判别联合：

```text
tool
user_input
plan_budget
```

前端没有新建三个弹窗系统，而是在原有 `ReviewDialog` 内根据 `kind` 渲染：

- `tool`：保留脱敏后的工具名、参数、风险提示和确认/拒绝；
- `user_input`：显示问题和多行输入，支持提交或停止，空文本不能提交；
- `plan_budget`：显示已尝试数、剩余步骤和硬上限，支持继续、补充后继续或停止，空补充不能提交。

`resumeTurn` 发送完整 `{decision, message?}`，恢复同一 turn 的 SSE，不创建新 turn。

## 11. 修改文件和职责

| 文件 | 职责变化 |
| --- | --- |
| `human_chat/graph.py` | 构造 `plan_step_agent`，接入全部 Plan 节点、路由、中断与汇总 |
| `human_chat/conversation/protocol.py` | 为 Plan 执行节点补充用户可见进度提示 |
| `web/src/types.ts` | 建立三类 review 判别联合和完整决定类型 |
| `web/src/api.ts` | 恢复请求改为发送完整决定对象 |
| `web/src/components/ReviewDialog.tsx` | 在同一弹窗内完成三种交互 |
| `web/src/App.tsx` | `submitReview` 传递完整 `ReviewDecision` |
| `web/src/styles.css` | 增加补充输入和多按钮布局 |
| `tests/test_graph.py` | 验证路由、状态、中断恢复和消息隔离 |

## 12. 直接验证

Graph 相关用例：

```powershell
.\.validation-python-313b\Scripts\python.exe -m pytest tests\test_graph.py -q \
  --basetemp=data\pytest-tmp-plan-full
```

结果：`24 passed`。覆盖的核心行为包括：

- ReAct 仍能直接回复、调用工具、限额和审批；
- Plan 能顺序执行多步，第二步看不到第一步 Agent 内部 ToolMessage；
- `review_after`、`failed`、`blocked` 和 Replan 分支按契约运行；
- 单步工具上限形成 `failed`；
- `ask_user` 恢复同一步骤，历史只追加一次；
- 计划额度继续后直接授权到硬上限；
- 达到硬上限后不执行新步骤；
- 三类 review 的恢复值不会串用。

前端使用仓库已安装的 TypeScript 7.0.2 和 Vite 8.2.2：

```powershell
tsc.exe -b --pretty false
node .\node_modules\vite\bin\vite.js build
```

结果：TypeScript 编译通过，Vite 成功转换 `1822 modules` 并生成 `web/dist`。

本机 `pnpm build` 调用的 TypeScript 7 JavaScript wrapper 在启动原生编译器时持续等待且无输出；直接执行同一安装包中的官方 `tsc.exe` 后立即通过。这是本地包 wrapper 执行问题，本步没有更换包管理器、修改依赖或为此加入业务兼容分支。

## 13. 本步没有新增的机制

- 没有计划 ID、版本号、优先级或依赖字段；
- 没有计划并行、DAG 或任务调度器；
- 没有自动重试、模型 fallback 或备用 Agent；
- 没有子 Graph 或新的持久化结构；
- 没有将中间执行轨迹暴露给最终聊天历史；
- 没有将 `ask_user` 和计划额度暂停合并成新的通用交互框架。

