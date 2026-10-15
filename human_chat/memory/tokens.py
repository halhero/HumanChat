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
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    prompt = (instruction + "\n只输出一个符合下面JSON Schema的JSON对象，不输出自然语言、null或裸数组。"
              "无候选时仍输出包含空列表的对象。\nJSON Schema:\n" + schema_json)
    if text_tokens(prompt + body) + 128 > budget:
        raise MemoryBudgetError("Memory processing input exceeds its configured budget")
    result = llm.with_structured_output(schema, method="json_mode").invoke([
        SystemMessage(content=prompt), HumanMessage(content=body),
    ])
    return schema.model_validate(result)
