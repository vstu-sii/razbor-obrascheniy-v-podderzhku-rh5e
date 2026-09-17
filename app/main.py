from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field


class TriageRequest(BaseModel):
    message: str = Field(min_length=1)
    customer_name: str | None = None
    account_id: str | None = None
    order_id: str | None = None
    history: list[str] = Field(default_factory=list)


class TriageResponse(BaseModel):
    status: str
    category: str
    urgency: str
    summary: str
    collected_data: dict[str, Any]
    missing_data: list[str]
    knowledge_base_sources: list[dict[str, str]]
    next_step: str
    draft_reply: str
    route: str
    needs_human_review: bool


app = FastAPI(title="AI Support Triage", version="0.1.0")


@app.get("/")
def hello_world() -> dict[str, str]:
    return {"message": "AI Support Triage is running"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/triage", response_model=TriageResponse)
def triage(request: TriageRequest) -> TriageResponse:
    """Lab 1 contract stub; real retrieval and LLM adapter arrive later."""
    return TriageResponse(
        status="escalated_to_operator",
        category="unclassified",
        urgency="normal",
        summary=request.message[:240],
        collected_data={
            "customer_name": request.customer_name,
            "account_id": request.account_id,
            "order_id": request.order_id,
            "history_messages": len(request.history),
        },
        missing_data=["category confirmation", "relevant knowledge-base article"],
        knowledge_base_sources=[],
        next_step="Проверить обращение и выбрать маршрут вручную.",
        draft_reply="Здравствуйте! Мы проверим обращение и вернёмся с ответом.",
        route="first_line_operator",
        needs_human_review=True,
    )
