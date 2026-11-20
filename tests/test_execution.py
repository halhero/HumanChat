import pytest
from pydantic import ValidationError

from human_chat.execution import (
    ExecutionPlan,
    PlanStep,
    ReplanDecision,
    StepResultSubmission,
)


def step() -> PlanStep:
    return PlanStep(
        instruction="读取项目配置",
        success_criteria="获得当前生效的配置值",
        review_after=False,
    )


def test_plan_and_replan_contracts_require_real_remaining_work():
    assert ExecutionPlan(steps=[step()]).steps[0].instruction == "读取项目配置"
    assert ReplanDecision.model_validate({"outcome": "continue"}).root.outcome == "continue"

    with pytest.raises(ValidationError):
        ExecutionPlan(steps=[])
    with pytest.raises(ValidationError):
        ReplanDecision.model_validate({"outcome": "revise", "remaining_steps": []})


def test_step_result_requires_a_self_contained_non_empty_result():
    result = StepResultSubmission(status="completed", result="读取到 backend=sqlite。")

    assert result.status == "completed"
    with pytest.raises(ValidationError):
        StepResultSubmission(status="completed", result="   ")
