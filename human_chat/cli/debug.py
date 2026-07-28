from human_chat.memory_review import parse_memory_review_request


def print_debug_summary(result: dict) -> None:
    print("[debug] Graph result summary:")
    print(f"[debug] keys={sorted(result.keys())}")
    print(f"[debug] messages={len(result.get('messages', []))}")
    print(f"[debug] tool_messages={len(result.get('tool_messages', []))}")
    print(f"[debug] tool_call_count={result.get('tool_call_count', 0)}")
    print(f"[debug] tool_events={len(result.get('tool_events', []))}")
    print(f"[debug] tool_limit_reached={result.get('tool_limit_reached', False)}")
    for event in result.get("tool_events", []):
        print(
            "[debug] "
            f"tool={event.get('tool')} "
            f"round={event.get('round')} "
            f"status={event.get('status')}"
        )
    review_request = parse_memory_review_request(result.get("memory_review_request"))
    print(f"[debug] memory_review_candidates={len(review_request.candidates)}")
    if result.get("tts_error"):
        print(f"[debug] tts_error={result['tts_error']}")
