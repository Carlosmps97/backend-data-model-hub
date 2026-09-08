"""Comparación entre versiones publicadas + etiqueta de restauración (doc 65).

- `compose_versions_range` / `compare_buckets`: estado NETO entre dos versiones
  a partir del ledger (before de la primera versión del rango + payload de la
  última), con exclusión honesta de lo irreconstruible.
- `compare_versions` / `compare_details`: orquestación con repository mockeado
  (normalización de orden, sentinels, shape del detalle doc 31).
- Restauración: `restoredFrom` aflora en el historial y el ledger conserva
  TODOS los eventos previos tras publicar una restauración (pregunta del doc:
  no hay incongruencia posible — historial derivado en lectura, append-only).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.changesets import service


def _wire_catalogs(monkeypatch):
    async def _empty():
        return []
    monkeypatch.setattr("app.features.udp.repository.list_udp", _empty)
    monkeypatch.setattr("app.features.domains.repository.list_domains", _empty)


# ── compose_versions_range (pura) ──────────────────────────────────────────


def test_compose_before_de_primera_y_after_de_ultima():
    v2 = {"canonical_tables": {"t1": {"op": "upsert", "beforeAt": "x",
                                      "before": {"id": "t1", "physicalName": "A"},
                                      "payload": {"physicalName": "B"}}}}
    v3 = {"canonical_tables": {"t1": {"op": "upsert", "beforeAt": "y",
                                      "before": {"id": "t1", "physicalName": "B"},
                                      "payload": {"physicalName": "C"}}}}
    composed, missing = service.compose_versions_range([v2, v3])
    entry = composed[("canonical_tables", "t1")]
    assert entry["before"] == {"id": "t1", "physicalName": "A"}   # de la PRIMERA
    assert entry["after"] == {"physicalName": "C"}                # de la ÚLTIMA
    assert missing == []


def test_compose_sin_imagen_previa_va_a_faltantes():
    v2 = {"canonical_tables": {"t1": {"op": "upsert",
                                      "payload": {"physicalName": "B"}}}}
    composed, missing = service.compose_versions_range([v2])
    assert composed == {}
    assert missing == [{"collection": "canonical_tables", "id": "t1", "name": "B"}]


def test_buckets_clasificacion_neta():
    composed = {
        ("canonical_tables", "nueva"): {"op": "upsert", "before": None,
                                        "after": {"physicalName": "NUEVA"}},
        ("canonical_tables", "borrada"): {"op": "delete",
                                          "before": {"physicalName": "VIEJA"},
                                          "after": None},
        ("canonical_tables", "editada"): {"op": "upsert",
                                          "before": {"physicalName": "X", "schema": "a"},
                                          "after": {"physicalName": "X", "schema": "b"}},
        # editada y DEVUELTA a su estado (solo difiere ruido interno): neto cero
        ("canonical_tables", "igual"): {"op": "upsert",
                                        "before": {"physicalName": "Z", "updatedAt": "1"},
                                        "after": {"physicalName": "Z", "updatedAt": "2"}},
        # creada y borrada dentro del rango: neto cero
        ("canonical_tables", "efimera"): {"op": "delete", "before": None, "after": None},
    }
    res = {"udp": {}, "domains": {}, "tables": {}, "columns": {},
           "projects": {}, "folders": {}}
    buckets = service.compare_buckets(composed, res)
    b = buckets["canonical_tables"]
    assert [e["id"] for e in b["added"]] == ["nueva"]
    assert [e["id"] for e in b["deleted"]] == ["borrada"]
    assert [e["id"] for e in b["edited"]] == ["editada"]


# ── compare_versions / compare_details (mocks) ─────────────────────────────


HDRS = {
    "v1": {"id": "v1", "projectId": "p1", "status": "approved", "appliedAt": "2026-01-01",
           "versionLabel": "v1", "title": "uno"},
    "v3": {"id": "v3", "projectId": "p1", "status": "approved", "appliedAt": "2026-03-01",
           "versionLabel": "v3", "title": "tres"},
    "d1": {"id": "d1", "status": "draft"},
}
CHANGES = {
    "v2": {"canonical_tables": {"t1": {"op": "upsert", "beforeAt": "x",
                                       "before": {"id": "t1", "physicalName": "VIEJO"},
                                       "payload": {"physicalName": "INTERMEDIO"}}}},
    "v3": {"canonical_tables": {"t1": {"op": "upsert", "beforeAt": "y",
                                       "before": {"id": "t1", "physicalName": "INTERMEDIO"},
                                       "payload": {"physicalName": "NUEVO"}}}},
}
AFTER_V1 = [
    {"id": "v3", "appliedAt": "2026-03-01", "versionLabel": "v3"},
    {"id": "v2", "appliedAt": "2026-02-01", "versionLabel": "v2"},
    {"id": "v9", "appliedAt": "2026-09-01", "versionLabel": "v9"},
]


def _wire(monkeypatch):
    _wire_catalogs(monkeypatch)
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(side_effect=lambda cid: HDRS.get(cid)))
    monkeypatch.setattr(
        service.repository, "applied_after",
        AsyncMock(side_effect=lambda pid, at: [v for v in AFTER_V1 if v["appliedAt"] > at]))
    monkeypatch.setattr(service.repository, "changes_map",
                        AsyncMock(side_effect=lambda cid, cols=None: CHANGES.get(cid, {})))
    monkeypatch.setattr(service.repository, "published", AsyncMock(return_value=[]))


def test_compare_compone_el_rango_y_normaliza_orden(monkeypatch):
    _wire(monkeypatch)
    out = asyncio.run(service.compare_versions("v3", "v1"))  # orden invertido a propósito
    assert out["from"]["versionLabel"] == "v1" and out["to"]["versionLabel"] == "v3"
    assert out["versionsSpanned"] == 2          # v2 y v3; v9 (posterior) queda fuera
    edited = out["collections"]["canonical_tables"]["edited"]
    assert [e["id"] for e in edited] == ["t1"]
    assert edited[0]["name"] == "NUEVO"
    assert out["counts"] == {"added": 0, "edited": 1, "deleted": 0, "total": 1}
    assert out["unreconstructed"] == []


def test_compare_sentinels(monkeypatch):
    _wire(monkeypatch)
    assert asyncio.run(service.compare_versions("v1", "v1")) == "same"
    assert asyncio.run(service.compare_versions("v1", "d1")) == "not-applied"
    assert asyncio.run(service.compare_versions("v1", "nope")) is None


def test_compare_details_antes_de_primera_despues_de_ultima(monkeypatch):
    _wire(monkeypatch)
    out = asyncio.run(service.compare_details(
        "v1", "v3", [("canonical_tables", "t1"), ("canonical_tables", "ajena")]))
    assert len(out["items"]) == 1               # la ajena al rango se omite
    d = out["items"][0]
    assert d["action"] == "modified"
    row = next(f for f in d["fields"] if f["key"] == "physicalName")
    assert row["before"] == "VIEJO" and row["after"] == "NUEVO"


# ── restauración: procedencia + historial íntegro ──────────────────────────


def test_historial_tras_restauracion_conserva_eventos():
    """Publicar una restauración NO borra el historial: el ledger es append-only
    y el evento nuevo aparece encima, rotulado con su `restoredFrom`."""
    changes = [
        {"csId": "cs1", "collection": "canonical_tables", "entityId": "t1",
         "op": "upsert", "payload": {"physicalName": "A"}, "at": "1",
         "before": None, "beforeAt": "1"},
        {"csId": "cs2", "collection": "canonical_tables", "entityId": "t1",
         "op": "upsert", "payload": {"physicalName": "B"}, "at": "2",
         "before": {"physicalName": "A"}, "beforeAt": "2"},
        # cs3 = restauración publicada de v1: vuelve a "A" con before estampado
        {"csId": "cs3", "collection": "canonical_tables", "entityId": "t1",
         "op": "upsert", "payload": {"physicalName": "A"}, "at": "3",
         "before": {"physicalName": "B"}, "beforeAt": "3"},
    ]
    headers = {
        "cs1": {"id": "cs1", "status": "approved", "appliedAt": "2026-01-01",
                "owner": "u1", "versionLabel": "v1", "title": "uno", "approvals": {}},
        "cs2": {"id": "cs2", "status": "approved", "appliedAt": "2026-02-01",
                "owner": "u1", "versionLabel": "v2", "title": "dos", "approvals": {}},
        "cs3": {"id": "cs3", "status": "approved", "appliedAt": "2026-03-01",
                "owner": "u2", "versionLabel": "v3", "title": "Restore to v1",
                "approvals": {},
                "restoredFrom": {"csId": "cs1", "versionLabel": "v1",
                                 "appliedAt": "2026-01-01"}},
    }
    ev = service.history_events(changes, headers)
    # Los TRES eventos siguen ahí (nada se recorta al restaurar), reciente primero.
    assert [e["version"] for e in ev] == ["v3", "v2", "v1"]
    assert ev[0]["restoredFrom"]["versionLabel"] == "v1"
    assert ev[1]["restoredFrom"] is None
