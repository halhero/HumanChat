"""Translate private LangGraph events into a stable browser-facing protocol."""

from typing import Any

from human_chat.conversation.models import (
    ConversationEvent,
    PendingReview,
    ReviewDecisionError,
)
from human_chat.tool_provider import ToolRegistry
from human_chat.tool_review import create_tool_review_request


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
        "understand_request": ("thinking", "正在理解你的问题"),
        "run_react_agent": ("working", "正在获取回答所需的信息"),
        "create_plan": ("working", "正在规划执行步骤"),
        "execute_plan_step": ("working", "正在执行计划"),
        "summarize_plan_result": ("writing", "正在整理执行结果"),
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


def create_pending_review(
    payload: Any,
    registry: ToolRegistry,
) -> PendingReview:
    if not isinstance(payload, dict):
        raise ReviewDecisionError("无法识别确认请求。")

    if payload.get("type") == "plan_budget":
        return PendingReview(
            kind="plan_budget",
            public_payload={
                "kind": "plan_budget",
                "completed_steps": payload.get("completed_steps", 0),
                "remaining_steps": payload.get("remaining_steps", []),
                "hard_limit": payload.get("hard_limit", 0),
            },
        )

    action_requests = payload.get("action_requests")
    if isinstance(action_requests, list) and action_requests:
        if any(action.get("name") == "ask_user" for action in action_requests):
            if len(action_requests) != 1 or action_requests[0].get("name") != "ask_user":
                raise ReviewDecisionError("不支持同时请求用户输入和普通工具。")
            return PendingReview(
                kind="user_input",
                public_payload={
                    "kind": "user_input",
                    "question": action_requests[0].get("args", {}).get(
                        "question", ""
                    ),
                },
            )

        request = create_tool_review_request(action_requests, registry)
        return PendingReview(
            kind="tool",
            public_payload={
                "kind": "tool",
                "title": "确认外部操作",
                "description": "此操作可能影响外部数据，请确认后继续。",
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
            resume_action_count=len(action_requests),
        )

    raise ReviewDecisionError("暂不支持这种确认请求。")
