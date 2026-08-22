"""Smoke: la app se construye y health responde sin base de datos."""
from __future__ import annotations


def test_app_title():
    from app.main import app

    assert app.title == "Data Modeler Platform Backend"


def test_health_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    # Sin lifespan no hay conexión: db_connected False y estado "degraded"
    # (el health ahora hace un ping vivo en vez de congelar el booleano).
    assert body["status"] == "degraded"
    assert body["db_connected"] is False
