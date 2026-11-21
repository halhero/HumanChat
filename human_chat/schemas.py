from typing import Annotated, Any

from langgraph.graph import add_messages
from pydantic import BaseModel, Field

from human_chat.execution import (
    ExecutionMode,
    PlanEndReason,
    PlanStep,
    StepExecutionRecord,
)


class ChatState(BaseModel):
    question: str = Field(description="User question.")
    input_source: dict[str, Any] | None = None
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)
    resolved_request: str = ""
    clarification_question: str = ""
    execution_mode: ExecutionMode = "react"
    memory_context: dict[str, Any] = Field(default_factory=dict)
    assistant_text: str = ""
    remaining_steps: list[PlanStep] = Field(default_factory=list)
    step_history: list[StepExecutionRecord] = Field(default_factory=list)
    authorized_step_limit: int = 0
    plan_user_context: list[str] = Field(default_factory=list)
    step_used_ask_user: bool = False
    plan_end_reason: PlanEndReason | None = None
