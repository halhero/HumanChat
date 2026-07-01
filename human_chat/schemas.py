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
    memory_prompt: str = ""
    assistant_text: str = ""
    tts_error: str = ""


class TtsResponse(BaseModel):
    text: str = Field(default="", description="Text to synthesize.")
