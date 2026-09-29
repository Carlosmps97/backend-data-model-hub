"""Adjudicación #5 (final review): GET /facets defiende `derived` server-side.

El compiler ya 422a los campos calculados en where/groupBy/agregaciones (no
existen en Mongo); /facets era el único hueco — facetar `tableCount` (path =
el array `tableIds`) agrupaba por arrays y devolvía basura a cualquier cliente
no-browser. Mismo 422, misma defensa en profundidad."""
from __future__ import annotations

from unittest.mock import AsyncMock


def test_facets_de_campo_derived_422(client, monkeypatch):
    from app.features.reporting.query import executor as ex

    # Catálogo sin UDP dinámicos (no toca DB); el estático de `models` ya trae
    # tableCount con hydrate='derived'.
    monkeypatch.setattr(ex, "_udp_defs", AsyncMock(return_value=[]))
    resp = client.get("/api/reporting/facets", params={"field": "tableCount", "from": "models", "projectId": "p1"})
    assert resp.status_code == 422
    assert "calculated" in resp.json()["detail"]


def test_facets_de_campo_enum_sigue_funcionando(client, monkeypatch):
    # El guard no sobre-bloquea: un enum se resuelve de allowedValues (sin DB).
    from app.features.reporting.query import executor as ex
    from app.features.reporting.query import router as qrouter

    monkeypatch.setattr(ex, "_udp_defs", AsyncMock(return_value=[]))
    monkeypatch.setattr(qrouter, "get_db", AsyncMock(return_value=None))
    resp = client.get("/api/reporting/facets",
                      params={"field": "parentCardinality", "from": "relationships", "projectId": "p1"})
    assert resp.status_code == 200
    assert [v["value"] for v in resp.json()["data"]] == [
        "one", "many", "one-only", "zero-one", "one-many", "zero-many"]


def test_facets_exige_project_id(client, monkeypatch):
    # Doc 75: las facetas son de UN proyecto — sin `projectId` no hay consulta.
    from app.features.reporting.query import executor as ex

    monkeypatch.setattr(ex, "_udp_defs", AsyncMock(return_value=[]))
    resp = client.get("/api/reporting/facets", params={"field": "dataType", "from": "columns"})
    assert resp.status_code == 422
