"""F2 #1: lock/unlock — el service audita; rutas registradas (smoke)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.glossary import service


def test_set_lock_audita_lock(monkeypatch):
    entry = {"id": "t1", "term": "codigo", "abbrev": "COD", "locked": True,
             "lockedBy": "admin", "lockedAt": "2026-07-10T00:00:00+00:00"}
    monkeypatch.setattr(service.repository, "set_lock", AsyncMock(return_value=entry))
    audit = AsyncMock()
    monkeypatch.setattr(service, "audit", audit)
    out = asyncio.run(service.set_lock("t1", True, "admin"))
    assert out == entry
    audit.assert_awaited_once_with("admin", "glossary.lock", target="codigo",
                                   target_type="glossary_entry")


def test_set_lock_audita_unlock(monkeypatch):
    entry = {"id": "t1", "term": "codigo", "abbrev": "COD", "locked": False,
             "lockedBy": None, "lockedAt": None}
    monkeypatch.setattr(service.repository, "set_lock", AsyncMock(return_value=entry))
    audit = AsyncMock()
    monkeypatch.setattr(service, "audit", audit)
    asyncio.run(service.set_lock("t1", False, "admin"))
    audit.assert_awaited_once_with("admin", "glossary.unlock", target="codigo",
                                   target_type="glossary_entry")


def test_set_lock_inexistente_no_audita(monkeypatch):
    monkeypatch.setattr(service.repository, "set_lock", AsyncMock(return_value=None))
    audit = AsyncMock()
    monkeypatch.setattr(service, "audit", audit)
    assert asyncio.run(service.set_lock("nope", True, "admin")) is None
    audit.assert_not_awaited()


def test_lock_routes_registradas(client):
    paths = {
        (getattr(r, "path", None), m)
        for r in client.app.routes
        for m in getattr(r, "methods", set()) or set()
    }
    assert ("/api/glossary/{entry_id}/lock", "POST") in paths
    assert ("/api/glossary/{entry_id}/unlock", "POST") in paths
