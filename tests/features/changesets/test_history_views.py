"""Doc 61: historial de auditoría para VISTAS — `views` entra a la whitelist
del doc 51 y el detalle resuelve nombres de tablas fuente y muestra el
Custom SQL. Patrón test_entity_history.py (repository mockeado)."""
from __future__ import annotations

import asyncio

from app.features.changesets import service

AT = "2026-08-10T10:00:00+00:00"


def _hdr(cs_id: str, applied: str | None, label: str = "v3") -> dict:
    return {"id": cs_id, "status": "approved", "appliedAt": applied, "owner": "u1",
            "reviewedBy": "u2", "versionLabel": label, "title": f"Versión {label}",
            "approvals": {"u2": {"status": "approved", "at": AT}}}


def test_views_en_whitelist_de_historial():
    assert "views" in service.HISTORY_COLLECTIONS


def test_historial_de_vista_resuelve_tablas_y_custom_sql(monkeypatch):
    before = {"id": "v1", "name": "v_cliente", "tableId": "t1",
              "sourceTableIds": ["t1"],
              "sources": [{"column": "id", "tableId": "t1"}],
              "customSql": None}
    payload = {"id": "v1", "name": "v_cliente", "tableId": "t1",
               "sourceTableIds": ["t1"],
               "sources": [{"column": "id", "tableId": "t1"},
                           {"column": "nombre", "tableId": "t1"}],
               "customSql": "SELECT id, nombre FROM core.m_cliente"}
    changes = [{"id": "cs2::views::v1", "csId": "cs2", "collection": "views",
                "entityId": "v1", "op": "upsert", "payload": payload, "at": AT,
                "before": before, "beforeAt": AT}]

    async def _changes(collection, entity_id):
        return changes

    async def _headers(ids):
        return {"cs2": _hdr("cs2", "2026-08-05T00:00:00+00:00")}

    async def _published(collection, flt=None, **kw):
        if collection == "canonical_tables":
            return [{"id": "t1", "physicalName": "M_CLIENTE"}]
        return [{"id": "v1", "name": "v_cliente",
                 "createdAt": "2026-07-20T00:00:00+00:00"}]

    async def _earliest(project_id=None):
        return {"id": "base", "appliedAt": "2026-07-24T00:00:00+00:00",
                "versionLabel": "v1", "title": "Base"}

    async def _users():
        return [{"id": "u1", "name": "Ana P", "email": "ana@x.pe"},
                {"id": "u2", "name": "Rev", "email": "rev@x.pe"}]

    # Doc 80 §8: firma REAL (catálogos por proyecto desde el doc 75).
    async def _udp(project_id):
        return []

    monkeypatch.setattr(service.repository, "entity_changes", _changes)
    monkeypatch.setattr(service.repository, "changesets_by_ids", _headers)
    monkeypatch.setattr(service.repository, "published", _published)
    monkeypatch.setattr(service.repository, "earliest_applied", _earliest)
    monkeypatch.setattr("app.features.auth.repository.list_users", _users)
    monkeypatch.setattr("app.features.udp.repository.list_udp", _udp)

    out = asyncio.run(service.entity_history("views", "v1"))
    edited = out["items"][0]
    assert edited["action"] == "edited"
    labels = {f["label"] for f in edited["fields"]}
    # El script custom SÍ es revisable (no es ruido como el `sql` congelado).
    assert "Custom SQL" in labels
    # Las filas de sources resuelven la tabla por NOMBRE (res["tables"]).
    assert any("M_CLIENTE" in lbl for lbl in labels)
