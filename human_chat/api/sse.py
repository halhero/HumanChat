"""Server-Sent Events encoding for conversation streams."""

import json
from collections.abc import AsyncIterator

from fastapi import Request
from fastapi.responses import StreamingResponse

from human_chat.conversation import ConversationService, ConversationStream


def conversation_stream_response(
    request: Request,
    service: ConversationService,
    stream: ConversationStream,
) -> StreamingResponse:
    return StreamingResponse(
        _event_source(request, service, stream),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Turn-ID": stream.turn_id,
        },
    )


async def _event_source(
    request: Request,
    service: ConversationService,
    stream: ConversationStream,
) -> AsyncIterator[str]:
    completed_normally = False
    try:
        async for event in service.iter_events(stream):
            if await request.is_disconnected():
                return
            yield _encode_event(event.type, event.data)
        completed_normally = True
    finally:
        if not completed_normally:
            await service.disconnect(stream.turn_id)


def _encode_event(event_type: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_type}\ndata: {payload}\n\n"
