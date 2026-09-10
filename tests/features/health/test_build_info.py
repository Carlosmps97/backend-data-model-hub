"""`GET /api/health` expone la identidad del build (doc 82): `build.sha`/`time`
cuando el deploy los estampó, `null` en dev local."""
from __future__ import annotations

from app.core.config import settings
from app.features.health.router import build_info


def test_sin_estampa_no_hay_build(monkeypatch):
    monkeypatch.setattr(settings, "BUILD_SHA", "")
    monkeypatch.setattr(settings, "BUILD_TIME", "2026-09-10T00:00:00Z")
    assert build_info() is None


def test_con_estampa_el_health_dice_que_commit_corre(monkeypatch, client):
    monkeypatch.setattr(settings, "BUILD_SHA", "3af3619")
    monkeypatch.setattr(settings, "BUILD_TIME", "2026-09-10T02:09:00Z")
    body = client.get("/api/health").json()
    assert body["build"] == {"sha": "3af3619", "time": "2026-09-10T02:09:00Z"}
    monkeypatch.setattr(settings, "BUILD_TIME", "")
    assert client.get("/api/health").json()["build"] == {"sha": "3af3619", "time": None}
