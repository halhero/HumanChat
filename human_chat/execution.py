"""Execution-mode contracts and prompts shared by the main Graph."""

from typing import Annotated, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator


ExecutionMode = Literal["react", "plan_execute"]
PlanEndReason = Literal[
    "completed",
    "planner_finished",
    "blocked",
    "user_stopped",
    "hard_limit",
]


CREATE_PLAN_PROMPT = """
你是 HumanChat 的任务规划器。你的职责是把已经解析完整的用户请求拆成一份可以按顺序执行的剩余计划，不负责亲自调用工具，也不直接回答用户。

输入包含 resolved_request、memory_context、tool_capabilities 和 max_steps。

规则：
1. 只输出 ExecutionPlan，不输出解释性正文。
2. steps 必须按执行顺序排列，数量为 1..max_steps。
3. 只生成线性顺序计划，不生成并行、分支、DAG 或依赖图。
4. 每个步骤应具有独立执行价值，不把一次自然操作机械拆成无价值小步。
5. instruction 明确写出本步骤要取得的结果，不写模型推理过程。
6. success_criteria 必须能通过工具结果或已有上下文判断，不使用空泛标准。
7. review_after 只在结果可能改变后续方案、获取了用户补充或位于明显阶段边界时设为 true。
8. 只能依据 tool_capabilities 判断能力，不假设未列出的工具存在，也不生成具体工具参数。
9. 如果需要只能由用户提供的信息，生成一个负责获取该信息并继续当前子任务的步骤，review_after=true；执行器会使用 ask_user。
10. 不重复执行 resolved_request 中已明确完成的事项，不把最终总结设计成计划步骤。
""".strip()


PLAN_STEP_PROMPT = """
你是 HumanChat 的单步骤执行器。本次只负责 current_step，不重新规划整个任务，不直接向用户生成最终答复。

最终必须提交 StepResultSubmission：completed 表示已满足 success_criteria；failed 表示本次尝试未满足，但可能通过重规划继续；blocked 表示当前无法自主继续。result 必须紧凑、具体地保存事实、关键输出和未完成原因。

规则：
1. 只处理 current_step，不擅自执行后续步骤。
2. 可以使用 completed_step_records、memory_context 和 plan_user_context，但不得声称没有依据的事实。
3. 工具调用成功不等于步骤完成；必须对照 success_criteria 判断。
4. 如果缺少只能由用户提供且确实必需的信息，调用 ask_user(question)；该模型轮次不同时请求无关工具。
5. 用户回答后继续完成同一步骤，不要只把用户回答原样当作最终结果。
6. 如果用户拒绝或无法提供必需信息，提交 blocked，不无限重复询问。
7. 工具失败时可在本次工具额度内调整方法；最终仍未达标则提交 failed。
8. 不把内部推理、完整工具消息或冗长日志放入 result。
9. 不虚构已执行动作、工具输出或完成状态。
10. 最终只返回符合 schema 的 StepResultSubmission。
""".strip()


REPLAN_PROMPT = """
你是 HumanChat 的剩余计划裁决器。已执行历史是不可变事实，你只能决定是否继续、替换尚未执行的计划，或结束任务。

规则：
1. 只输出 ReplanDecision，不输出解释性正文。
2. continue 表示现有 remaining_steps 仍合理；revise 返回新的完整 remaining_steps 快照；finish 表示应结束并交给总结器。
3. 不得修改、删除、重写或再次执行 step_history 中的步骤。
4. revise 只包含尚未执行步骤，不要复制已完成步骤。
5. 保持顺序计划，不生成并发、分支或依赖图。
6. 新剩余计划不得超过 remaining_hard_budget；没有剩余额度时必须 finish。
7. 步骤 failed 时应判断是否更换方法、缩小剩余目标或结束，不无条件原样重试。
8. 用户补充信息改变计划时使用 revise；现有计划完全不受影响时才使用 continue。
9. 若实际使用 ask_user 获取了信息，必须重新检查后续步骤，不能机械 continue。
""".strip()


SUMMARY_PLAN_PROMPT = """
你是 HumanChat 的计划执行结果汇总器。请根据真实步骤记录向用户生成最终答复，不调用工具，不继续执行任务。

规则：
1. 先回答用户最关心的结果，再补充必要过程或限制。
2. 只把 status=completed 且有事实结果支持的事项表述为已完成。
3. failed、blocked 和未执行的 remaining_steps 必须如实说明。
4. completed 和 planner_finished 均必须以真实记录为准；planner_finished 不自动代表原计划全部完成。
5. blocked 说明已完成部分与阻塞点；user_stopped 说明任务按用户选择停止；hard_limit 说明已达本轮上限及已完成和未完成内容。
6. 不暴露内部节点名、middleware、token、完整 prompt、堆栈或模型推理。
7. 不建议从头重做已完成步骤。
8. 输出自然、简洁、面向用户的最终文本。
""".strip()


@tool
def ask_user(question: str) -> str:
    """Ask the user for information that only the user can provide."""

    raise RuntimeError("ask_user must be resolved by HITL respond")


class PlanStep(BaseModel):
    """One ordered unit of work that a single step Agent must complete."""

    model_config = ConfigDict(extra="forbid")

    instruction: str
    success_criteria: str
    review_after: bool

    @field_validator("instruction", "success_criteria")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Plan step text must not be empty")
        return value


class StepResultSubmission(BaseModel):
    """Self-contained outcome returned by one plan-step Agent invocation."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "failed", "blocked"]
    result: str

    @field_validator("result")
    @classmethod
    def validate_result(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Step result must not be empty")
        return value


class StepExecutionRecord(BaseModel):
    """Immutable record of one real attempt to execute a plan step."""

    model_config = ConfigDict(extra="forbid")

    step: PlanStep
    result: StepResultSubmission


class ExecutionPlan(BaseModel):
    """Initial ordered plan produced before step execution begins."""

    model_config = ConfigDict(extra="forbid")

    steps: list[PlanStep] = Field(min_length=1)


class ContinuePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["continue"]


class RevisePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["revise"]
    remaining_steps: list[PlanStep] = Field(min_length=1)


class FinishPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["finish"]


ReplanOutcome = Annotated[
    ContinuePlan | RevisePlan | FinishPlan,
    Field(discriminator="outcome"),
]


class ReplanDecision(RootModel[ReplanOutcome]):
    """Mutually exclusive decision about the still-unexecuted plan."""

