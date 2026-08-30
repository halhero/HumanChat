"""Server-Sent Events serialization for HumanChat's one-way event streams."""

from collections.abc import AsyncIterator
import json

from fastapi.responses import StreamingResponse

from human_chat.application import (
    ConversationEvent,
    ConversationService,
    ConversationStream,
)


SSE_MEDIA_TYPE = "text/event-stream"
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
}


def encode_sse(event: ConversationEvent) -> str:
    """Serialize one named event according to the SSE wire format."""

    data = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event.type}\ndata: {data}\n\n"


def conversation_stream_response(
    service: ConversationService,
    stream: ConversationStream,
) -> StreamingResponse:
    """Create an SSE response and expose the turn id needed by control calls."""

    headers = {**SSE_HEADERS, "X-Turn-ID": stream.turn_id}
    return StreamingResponse(
        _event_source(service, stream),
        media_type=SSE_MEDIA_TYPE,
        headers=headers,
    )


async def _event_source(
    service: ConversationService,
    stream: ConversationStream,
) -> AsyncIterator[str]:
    fully_consumed = False
    try:
        async for event in service.iter_events(stream):
            yield encode_sse(event)
        fully_consumed = True
    finally:
        if not fully_consumed:
            await service.disconnect(stream.turn_id)
