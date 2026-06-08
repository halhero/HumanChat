from typing import Annotated, Any

from langgraph.graph import add_messages
from pydantic import BaseModel, Field


class ChatState(BaseModel):
    question: str = Field(description="User question.")
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)
    tts_text: str = ""
    tts_error: str = ""


class TtsResponse(BaseModel):
    text: str = Field(default="", description="Text to synthesize.")
