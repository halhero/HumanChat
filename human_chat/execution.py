"""Execution-mode contracts shared by request triage and the main Graph."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
)


ExecutionMode = Literal["react", "plan_execute"]


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

