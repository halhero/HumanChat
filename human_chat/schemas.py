from typing import Annotated, Any

from langgraph.graph import add_messages
from pydantic import BaseModel, Field


class ChatState(BaseModel):
    question: str = Field(description="User question.")
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)
    memory_prompt: str = ""
    tool_request: dict[str, Any] = Field(default_factory=dict)
    tool_result: str = ""
    assistant_text: str = ""
    tts_text: str = ""
    tts_error: str = ""


class TtsResponse(BaseModel):
    text: str = Field(default="", description="Text to synthesize.")


class ToolDecision(BaseModel):
    need_tool: bool = False
    tool_name: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
