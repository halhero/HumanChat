from typing import Annotated, Any

from langgraph.graph import add_messages
from pydantic import BaseModel, Field


class ChatState(BaseModel):
    question: str = Field(description="User question.")
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)
    tool_messages: list[Any] = Field(default_factory=list)
    tool_call_count: int = 0
    tool_events: list[dict[str, Any]] = Field(default_factory=list)
    tool_limit_reached: bool = False
    # 工具审批快照进入 Graph state/checkpoint；真正执行仍读取 tool_messages 中的
    # 原始 tool call，避免展示用的脱敏参数污染执行参数。
    tool_review_request: dict[str, Any] | None = None
    tool_review_approved: bool | None = None
    memory_review_request: dict[str, Any] | None = None
    memory_saved_count: int = 0
    memory_prompt: str = ""
    assistant_text: str = ""
