"""A conservative tokenizer proxy, not a claim of native Qwen token counts."""

import json
import math
from functools import lru_cache

import tiktoken
from langchain_core.messages import HumanMessage, SystemMessage


class MemoryBudgetError(ValueError):
    pass


@lru_cache(maxsize=1)
def encoding():
    return tiktoken.get_encoding("cl100k_base")


def text_tokens(text):
    return math.ceil(len(encoding().encode(text)) * 1.25)


def payload_tokens(payload):
    return text_tokens(json.dumps(payload, ensure_ascii=False, default=str))


def structured_call(llm, schema, instruction, payload, budget):
    body = json.dumps(payload, ensure_ascii=False)
    if text_tokens(instruction + body) + payload_tokens(schema.model_json_schema()) + 128 > budget:
        raise MemoryBudgetError("Memory processing input exceeds its configured budget")
    return llm.with_structured_output(schema, method="function_calling").invoke([
        SystemMessage(content=instruction), HumanMessage(content=body),
    ])
