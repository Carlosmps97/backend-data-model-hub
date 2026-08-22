"""GET /api/me devuelve la identidad en sesión en ambos modos."""
from __future__ import annotations

from app.core.config import settings


def test_me_local_mode(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    monkeypatch.setattr(settings, "LOCAL_DEV_USER", "carlos@local")
    resp = client.get("/api/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["email"] == "carlos@local"
    assert body["data"]["source"] == "local"


def test_me_databricks_mode_reads_headers(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "databricks")
    # httpx codifica los valores de header como ASCII; los caracteres latin-1
    # no-ASCII (p. ej. "Gómez") se prueban a nivel unitario en test_provider.py
    # con .encode("latin-1") — Starlette decodifica los headers como latin-1.
    resp = client.get(
        "/api/me",
        headers={
            "X-Forwarded-Email": "ana@corp.com",
            "X-Forwarded-Preferred-Username": "ana",
            "X-Forwarded-User": "Ana Gomez",
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data == {
        "email": "ana@corp.com",
        "username": "ana",
        "display_name": "Ana Gomez",
        "source": "databricks",
    }
