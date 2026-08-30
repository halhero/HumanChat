"""Framework-neutral helpers for reading LangGraph interrupt payloads."""


def extract_interrupt_payloads(result: dict) -> list:
    """Support both LangGraph Interrupt objects and serialized dictionaries."""

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
