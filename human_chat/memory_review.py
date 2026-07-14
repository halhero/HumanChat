from pydantic import BaseModel, Field


class MemoryCandidate(BaseModel):
    text: str = Field(description="A concise long-term memory candidate.")


class MemoryExtractionResult(BaseModel):
    candidates: list[MemoryCandidate] = Field(default_factory=list)


class MemoryReviewRequest(BaseModel):
    candidates: list[MemoryCandidate] = Field(default_factory=list)
    require_confirmation: bool = True


class MemoryReviewDecision(BaseModel):
    accepted_texts: list[str] = Field(default_factory=list)


def create_memory_review_request(candidates: list[dict | MemoryCandidate]) -> MemoryReviewRequest:
    parsed_candidates = []

    for candidate in candidates:
        parsed = candidate if isinstance(candidate, MemoryCandidate) else MemoryCandidate(**candidate)
        parsed.text = parsed.text.strip()
        if parsed.text:
            parsed_candidates.append(parsed)

    return MemoryReviewRequest(candidates=parsed_candidates)


def parse_memory_review_request(data: dict | MemoryReviewRequest | None) -> MemoryReviewRequest:
    if data is None:
        return MemoryReviewRequest()
    if isinstance(data, MemoryReviewRequest):
        return data
    return MemoryReviewRequest(**data)


def parse_memory_review_decision(data: dict | MemoryReviewDecision | None) -> MemoryReviewDecision:
    if data is None:
        return MemoryReviewDecision()
    if isinstance(data, MemoryReviewDecision):
        return data
    return MemoryReviewDecision(**data)
