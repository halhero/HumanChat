"""Resolve a user turn into a standalone request or one clarification question."""

import json

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, model_validator


REQUEST_UNDERSTANDING_PROMPT = """你负责整理当前用户请求，不负责回答或执行请求。
结合有限的近期对话，补全当前输入中必要的指代、省略和上下文依赖，使其成为忠实、可独立理解的请求。
必须保留用户原意，不添加对话中没有的事实、约束或目标。已经自包含的输入只做最小必要整理。
只有当缺少的信息存在多个合理解释，并且不同解释会显著改变回答、长期记忆召回或工具选择时，才提出澄清问题。
不要因措辞、表达风格或不影响处理方式的轻微歧义追问。澄清问题只询问继续处理所需的最小信息，并使用当前对话的语言。

输出规则：
- 能可靠理解时，仅填写 resolved_request，并将 clarification_question 设为空字符串。
- 必须澄清时，仅填写 clarification_question，并将 resolved_request 设为空字符串。
- 不得回答问题，不得执行任务，不得输出原因、置信度、意图标签或其他字段。

示例：
- 当前输入“继续”，近期对话正在讨论 HumanChat 的数据库存储方案：resolved_request 应明确为继续该方案，而不是保留孤立的“继续”。
- 当前输入“按刚才那个方案写”，近期对话已有唯一方案：resolved_request 应补全方案对象。
- 当前输入“把它发给他”，上下文中有多个可能对象或接收者：clarification_question 只询问无法确定的关键信息。
"""


class RequestUnderstanding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved_request: str = ""
    clarification_question: str = ""

    @model_validator(mode="after")
    def validate_exclusive_result(self):
        self.resolved_request = self.resolved_request.strip()
        self.clarification_question = self.clarification_question.strip()
        if bool(self.resolved_request) == bool(self.clarification_question):
            raise ValueError(
                "Exactly one of resolved_request or clarification_question must be provided"
            )
        return self


def invoke_request_understanding(
    llm,
    question: str,
    recent_context: list[dict[str, str]],
) -> RequestUnderstanding:
    payload = {
        "recent_context": recent_context,
        "current_user_input": question,
    }
    schema = json.dumps(
        RequestUnderstanding.model_json_schema(),
        ensure_ascii=False,
    )
    prompt = (
        REQUEST_UNDERSTANDING_PROMPT
        + "\n只输出符合以下 JSON Schema 的 JSON 对象：\n"
        + schema
    )
    result = llm.with_structured_output(
        RequestUnderstanding,
        method="json_mode",
    ).invoke(
        [
            SystemMessage(content=prompt),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ]
    )
    return RequestUnderstanding.model_validate(result)
