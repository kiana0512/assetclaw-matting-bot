from __future__ import annotations

from fastapi.testclient import TestClient

from assetclaw_matting.api.main import app


def test_url_verification_challenge(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "feishu_event_mode", "webhook")
    monkeypatch.setattr(settings, "feishu_verification_token", "ok-token")
    client = TestClient(app)
    response = client.post("/feishu/events", json={"type": "url_verification", "token": "ok-token", "challenge": "abc"})
    assert response.status_code == 200
    assert response.json() == {"challenge": "abc"}


def test_url_verification_bad_token(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "feishu_event_mode", "webhook")
    monkeypatch.setattr(settings, "feishu_verification_token", "ok-token")
    client = TestClient(app)
    response = client.post("/feishu/events", json={"type": "url_verification", "token": "bad", "challenge": "abc"})
    assert response.status_code == 403


def test_ws_mode_rejects_webhook_events(monkeypatch) -> None:
    from assetclaw_matting.config import settings

    monkeypatch.setattr(settings, "feishu_event_mode", "ws")
    client = TestClient(app)
    response = client.post(
        "/feishu/events",
        json={
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1", "event_id": "evt_test"},
            "event": {},
        },
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False
