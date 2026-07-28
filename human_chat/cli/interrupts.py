from human_chat.memory_review import (
    MemoryReviewDecision,
    MemoryReviewRequest,
    parse_memory_review_request,
)
from human_chat.runtime import ChatRuntime


def handle_graph_interrupts(runtime: ChatRuntime, result: dict) -> dict | None:
    resume_result = None

    for payload in extract_interrupt_payloads(result):
        if not isinstance(payload, dict) or payload.get("type") != "memory_review":
            print("收到暂不支持的 Graph interrupt，已跳过。")
            continue

        review_request = parse_memory_review_request(payload.get("request"))
        decision = prompt_memory_review_decision(review_request)
        resume_result = runtime.resume(_model_to_dict(decision))

    return resume_result


def extract_interrupt_payloads(result: dict) -> list:
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


def _model_to_dict(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
