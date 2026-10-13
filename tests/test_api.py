from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_hello_world_and_health() -> None:
    assert client.get("/").json() == {"message": "AI Support Triage is running"}
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_stub_requires_operator_and_does_not_fabricate_answer() -> None:
    response = client.post(
        "/triage",
        json={"message": "Не могу войти", "customer_name": "Тестовый клиент"},
    )
    assert response.status_code == 200
    card = response.json()
    assert card["status"] == "not_analyzed"
    assert card["mode"] == "escalate"
    assert card["urgency"] == "unassessed"
    assert card["needs_human_review"] is True
    assert card["draft_reply"] is None
    assert card["knowledge_base_sources"] == []
    assert card["collected_data"] == {}
    assert "Тестовый клиент" not in str(card)
