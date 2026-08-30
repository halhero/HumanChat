"""Translate private LangGraph events into a stable browser-facing protocol."""

from typing import Any, Literal

from human_chat.conversation.models import (
    ConversationEvent,
    PendingReview,
    ReviewDecisionError,
)
from human_chat.memory_review import parse_memory_review_request
from human_chat.tool_review import parse_tool_review_request


def extract_interrupt_payloads(update: dict) -> list[Any]:
    interrupts = update.get("__interrupt__") or []
    if not isinstance(interrupts, (list, tuple)):
        interrupts = [interrupts]

    payloads = []
    for item in interrupts:
        value = getattr(item, "value", None)
        if value is None and isinstance(item, dict):
            value = item.get("value", item)
        if value is not None:
            payloads.append(value)
    return payloads


def progress_event(
    node_name: str,
    announced_stages: set[str],
) -> ConversationEvent | None:
    stages = {
        "prepare_context": ("thinking", "正在理解你的问题"),
        "execute_project_tools": ("working", "正在获取回答所需的信息"),
        "generate_limit_reply": ("writing", "正在整理已有信息"),
        "finalize_reply": ("writing", "正在生成回复"),
    }
    stage = stages.get(node_name)
    if stage is None or stage[0] in announced_stages:
        return None
    announced_stages.add(stage[0])
    return ConversationEvent(
        type="turn.progress",
        data={"stage": stage[0], "message": stage[1]},
    )


def create_pending_review(payload: Any) -> PendingReview:
    if not isinstance(payload, dict):
        raise ReviewDecisionError("无法识别确认请求。")

    review_type = payload.get("type")
    if review_type == "tool_review":
        request = parse_tool_review_request(payload.get("request"))
        return PendingReview(
            kind="tool",
            public_payload={
                "kind": "tool",
                "title": "确认外部操作",
                "description": "此操作可能影响外部数据，请确认后继续。",
                "selectable": False,
                "items": [
                    {
                        "id": call.call_id or f"tool-{index}",
                        "title": call.name,
                        "description": (
                            "只读外部操作"
                            if call.read_only
                            else "可能修改外部状态"
                        ),
                        "details": call.arguments,
                    }
                    for index, call in enumerate(request.calls)
                ],
            },
        )

    if review_type == "memory_review":
        request = parse_memory_review_request(payload.get("request"))
        memory_items = {
            f"memory-{index}": candidate.text
            for index, candidate in enumerate(request.candidates)
        }
        return PendingReview(
            kind="memory",
            public_payload={
                "kind": "memory",
                "title": "保存为长期记忆",
                "description": "请选择今后对话中可以继续使用的信息。",
                "selectable": True,
                "items": [
                    {"id": item_id, "title": text}
                    for item_id, text in memory_items.items()
                ],
            },
            memory_items=memory_items,
        )

    raise ReviewDecisionError("暂不支持这种确认请求。")


def build_resume_value(
    review: PendingReview,
    *,
    decision: Literal["approve", "reject"],
    selected_item_ids: list[str],
) -> dict[str, Any]:
    if review.kind == "tool":
        if selected_item_ids:
            raise ReviewDecisionError("工具确认不接受选项列表。")
        return {"approved": decision == "approve"}

    if decision == "reject":
        if selected_item_ids:
            raise ReviewDecisionError("拒绝保存时不能同时选择记忆。")
        return {"accepted_texts": []}

    unknown_ids = set(selected_item_ids) - set(review.memory_items)
    if unknown_ids:
        raise ReviewDecisionError("选择中包含无效的长期记忆条目。")
    return {
        "accepted_texts": [
            review.memory_items[item_id]
            for item_id in dict.fromkeys(selected_item_ids)
        ]
    }
