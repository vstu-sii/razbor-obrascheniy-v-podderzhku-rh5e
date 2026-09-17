from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_hello_world() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "AI Support Triage is running"}


def test_health() -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_triage_contract() -> None:
    response = client.post("/triage", json={"message": "Не могу войти"})
    assert response.status_code == 200
    assert response.json()["status"] == "escalated_to_operator"
