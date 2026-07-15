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
    resp = client.get("/api/reporting/facets", params={"field": "tableCount", "from": "models"})
    assert resp.status_code == 422
    assert "calculado" in resp.json()["detail"]


def test_facets_de_campo_enum_sigue_funcionando(client, monkeypatch):
    # El guard no sobre-bloquea: un enum se resuelve de allowedValues (sin DB).
    from app.features.reporting.query import executor as ex
    from app.features.reporting.query import router as qrouter

    monkeypatch.setattr(ex, "_udp_defs", AsyncMock(return_value=[]))
    monkeypatch.setattr(qrouter, "get_db", AsyncMock(return_value=None))
    resp = client.get("/api/reporting/facets",
                      params={"field": "sourceCardinality", "from": "relationships"})
    assert resp.status_code == 200
    assert resp.json()["data"] == [{"value": "one", "label": "one"},
                                   {"value": "many", "label": "many"}]
