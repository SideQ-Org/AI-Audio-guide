from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "ai-audio-guide"


def test_index_serves_html():
    r = client.get("/")
    assert r.status_code == 200
    assert "AI Audio Guide" in r.text
