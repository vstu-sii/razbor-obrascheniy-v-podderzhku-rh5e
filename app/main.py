"""Lab 1 service skeleton. No model, retrieval, or outbound messaging is wired in."""

from typing import Any, Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field


class TriageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    history: list[str] = Field(default_factory=list)
    customer_name: str | None = None
    account_id: str | None = None
    order_id: str | None = None


class TriageResponse(BaseModel):
    status: Literal["not_analyzed"]
    mode: Literal["escalate"]
    category: Literal["other"]
    category_reason: str
    urgency: Literal["unassessed"]
    urgency_reason: str
    summary: str
    collected_data: dict[str, Any]
    missing_data: list[str]
    knowledge_base_sources: list[dict[str, str]]
    next_step: str
    draft_reply: str | None
    route: Literal["first_line_operator"]
    needs_human_review: Literal[True]


app = FastAPI(title="AI Support Triage — Lab 1 stub", version="0.2.0")


@app.get("/")
def hello_world() -> dict[str, str]:
    return {"message": "AI Support Triage is running"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/triage", response_model=TriageResponse)
def triage(_request: TriageRequest) -> TriageResponse:
    """Return an explicit handoff; the stub must not pretend to analyze tickets."""
    return TriageResponse(
        status="not_analyzed",
        mode="escalate",
        category="other",
        category_reason="Категория не определялась: AI-пайплайн ещё не подключён.",
        urgency="unassessed",
        urgency_reason="Срочность не определялась: требуется оператор.",
        summary="",
        collected_data={},
        missing_data=[],
        knowledge_base_sources=[],
        next_step="Оператор вручную читает исходное обращение и выбирает безопасный маршрут.",
        draft_reply=None,
        route="first_line_operator",
        needs_human_review=True,
    )
