import json

from human_chat.memory_review import (
    MemoryReviewDecision,
    MemoryReviewRequest,
    parse_memory_review_request,
)
from human_chat.runtime import ChatRuntime
from human_chat.tool_review import (
    ToolReviewDecision,
    ToolReviewRequest,
    parse_tool_review_request,
)


def handle_graph_interrupts(runtime: ChatRuntime, result: dict) -> dict | None:
    """处理当前执行及后续恢复过程中连续出现的 Graph interrupt。

    一次提问可能先审批 MCP 工具，工具执行并生成回答后又触发长期记忆审批，所以
    不能只处理第一次 interrupt。每次 resume 后继续检查结果，直到 Graph 完成或
    遇到当前 CLI 不认识的 interrupt。
    """

    current_result = result
    latest_resume_result = None

    while True:
        payloads = extract_interrupt_payloads(current_result)
        if not payloads:
            return latest_resume_result

        handled = False
        for payload in payloads:
            decision = _prompt_interrupt_decision(payload)
            if decision is None:
                continue
            # 决定通过 LangGraph Command(resume=...) 回到原暂停节点，而不是在 CLI
            # 中直接调用工具，从而保留 Graph 的 checkpoint 和路由一致性。
            current_result = runtime.resume(_model_to_dict(decision))
            latest_resume_result = current_result
            handled = True
            break

        if not handled:
            return latest_resume_result


def _prompt_interrupt_decision(payload):
    """按显式 type 字段把 interrupt 分派给对应的人机交互逻辑。"""

    if not isinstance(payload, dict):
        print("收到暂不支持的 Graph interrupt，已跳过。")
        return None

    interrupt_type = payload.get("type")
    if interrupt_type == "memory_review":
        review_request = parse_memory_review_request(payload.get("request"))
        return prompt_memory_review_decision(review_request)
    if interrupt_type == "tool_review":
        review_request = parse_tool_review_request(payload.get("request"))
        return prompt_tool_review_decision(review_request)

    print("收到暂不支持的 Graph interrupt，已跳过。")
    return None


def extract_interrupt_payloads(result: dict) -> list:
    """兼容 LangGraph Interrupt 对象和序列化后的字典表示。"""

    interrupts = result.get("__interrupt__") or []
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


def prompt_memory_review_decision(
    review_request: MemoryReviewRequest,
) -> MemoryReviewDecision:
    accepted_texts = []

    print("发现候选长期记忆：")
    for index, candidate in enumerate(review_request.candidates, start=1):
        print(f"{index}. {candidate.text}")
        choice = input("保存这条记忆？y/N：").strip().lower()
        if choice == "y":
            accepted_texts.append(candidate.text)

    return MemoryReviewDecision(accepted_texts=accepted_texts)


def prompt_tool_review_decision(
    review_request: ToolReviewRequest,
) -> ToolReviewDecision:
    """向 CLI 用户展示脱敏调用信息，并以默认拒绝方式收集决定。"""

    print("模型请求执行需要确认的工具：")
    for index, call in enumerate(review_request.calls, start=1):
        safety = "只读" if call.read_only else "可能修改外部状态"
        print(f"{index}. {call.name} [{call.source}, {safety}]")
        print(
            "   参数："
            + json.dumps(call.arguments, ensure_ascii=False, default=str)
        )

    # 只有明确输入 y 才批准；回车或未知文本都不会放行受保护操作。
    choice = input("批准执行以上工具？y/N：").strip().lower()
    return ToolReviewDecision(approved=choice == "y")


def _model_to_dict(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
