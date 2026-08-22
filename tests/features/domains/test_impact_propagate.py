"""Cascade de Parent Domain: impacto agrupado por tabla (F2 #2) + propagate."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.domains import service
from app.features.domains.service import cascade_filter, summarize_impact


GROUPS = [
    {"_id": "t2", "columns": 3, "overridden": 1},
    {"_id": "t1", "columns": 2, "overridden": 0},
]
NAMES = {
    "t1": {"schema": "core", "physicalName": "CLIENTE", "logicalName": "cliente"},
    "t2": {"schema": "core", "physicalName": "CUENTA", "logicalName": "cuenta soles"},
}


# ── summarize_impact (puro): agrupación, totales, q, paginación ────────────


def test_summarize_impact_agrupa_y_totaliza():
    out = summarize_impact(GROUPS, NAMES, models_affected=2)
    assert out["columnsUsing"] == 5
    assert out["willUpdate"] == 4
    assert out["overridden"] == 1
    assert out["totalTables"] == 2
    assert out["modelsAffected"] == 2
    # Ordenado por physicalName (case-insensitive).
    assert [t["physicalName"] for t in out["tables"]] == ["CLIENTE", "CUENTA"]
    assert out["tables"][1] == {"tableId": "t2", "schema": "core",
                                "physicalName": "CUENTA", "logicalName": "cuenta soles",
                                "columns": 3, "overridden": 1}


def test_summarize_impact_q_filtra_tablas_pero_totales_globales():
    out = summarize_impact(GROUPS, NAMES, models_affected=2, q="cuenta")
    assert out["totalTables"] == 1                              # solo CUENTA matchea
    assert [t["tableId"] for t in out["tables"]] == ["t2"]
    assert out["columnsUsing"] == 5 and out["willUpdate"] == 4  # globales, sin filtro


def test_summarize_impact_q_matchea_logical_name():
    out = summarize_impact(GROUPS, NAMES, models_affected=0, q="soles")
    assert [t["tableId"] for t in out["tables"]] == ["t2"]


def test_summarize_impact_pagina_offset_limit():
    out = summarize_impact(GROUPS, NAMES, models_affected=0, offset=1, limit=1)
    assert out["totalTables"] == 2
    assert [t["tableId"] for t in out["tables"]] == ["t2"]  # 2ª tabla del orden


def test_summarize_impact_tabla_sin_nombre_no_rompe():
    out = summarize_impact([{"_id": "tX", "columns": 1, "overridden": 0}], {}, 0)
    assert out["tables"][0]["physicalName"] == ""
    assert out["tables"][0]["schema"] is None


def test_summarize_impact_vacio():
    out = summarize_impact([], {}, 0)
    assert out == {"columnsUsing": 0, "willUpdate": 0, "overridden": 0,
                   "totalTables": 0, "modelsAffected": 0, "tables": []}


# ── impact (async, repos mockeados): orquestación ──────────────────────────


def test_impact_orquesta_repos(monkeypatch):
    monkeypatch.setattr(service.repository, "impact_groups",
                        AsyncMock(return_value=GROUPS))
    monkeypatch.setattr(service.repository, "tables_by_ids",
                        AsyncMock(return_value=NAMES))
    monkeypatch.setattr(service.repository, "models_affected", AsyncMock(return_value=2))
    out = asyncio.run(service.impact("pd-monto", q="cuenta"))
    assert out["modelsAffected"] == 2 and out["totalTables"] == 1
    service.repository.tables_by_ids.assert_awaited_once_with(["t2", "t1"])
    service.repository.models_affected.assert_awaited_once_with(["t2", "t1"])


# ── cascade_filter: propagate sólo toca columnas SIN override ──────────────


def test_propagate_targets_only_non_overridden():
    assert cascade_filter("pd-monto") == {
        "parentDomainId": "pd-monto",
        "typeOverridden": {"$ne": True},
        "flgactive": {"$ne": False},
    }


# ── Smoke de rutas: impact (GET con q/offset/limit) + propagate ────────────


def test_impact_and_propagate_routes_registered(client):
    paths = {
        (getattr(r, "path", None), m)
        for r in client.app.routes
        for m in getattr(r, "methods", set()) or set()
    }
    assert ("/api/domains/{domain_id}/impact", "GET") in paths
    assert ("/api/domains/{domain_id}/propagate", "POST") in paths


def test_impact_declara_query_params(client):
    route = next(
        r for r in client.app.routes
        if getattr(r, "path", None) == "/api/domains/{domain_id}/impact"
        and "GET" in getattr(r, "methods", set())
    )
    param_names = {p.name for p in route.dependant.query_params}
    assert {"q", "offset", "limit"} <= param_names
