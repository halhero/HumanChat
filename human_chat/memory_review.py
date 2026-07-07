from pydantic import BaseModel, Field


class MemoryCandidate(BaseModel):
    category: str = Field(description="One of: preference, fact, note.")
    text: str = Field(description="A concise long-term memory candidate.")


class MemoryExtractionResult(BaseModel):
    candidates: list[MemoryCandidate] = Field(default_factory=list)


class MemoryReviewRequest(BaseModel):
    candidates: list[MemoryCandidate] = Field(default_factory=list)
    require_confirmation: bool = True


def create_memory_review_request(candidates: list[dict | MemoryCandidate]) -> MemoryReviewRequest:
    parsed_candidates = []

    for candidate in candidates:
        parsed = candidate if isinstance(candidate, MemoryCandidate) else MemoryCandidate(**candidate)
        parsed.text = parsed.text.strip()
        parsed.category = parsed.category.strip().lower() or "note"
        if parsed.text:
            parsed_candidates.append(parsed)

    return MemoryReviewRequest(candidates=parsed_candidates)
