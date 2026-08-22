"""Búsqueda global por COLUMNA (Database Explorer): join columna→tabla con
esquema resuelto, descarte de huérfanas y contrato HTTP."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.catalog import service


def test_search_columns_resuelve_tabla_y_esquema(monkeypatch):
    monkeypatch.setattr(service.repository, "search_columns", AsyncMock(return_value=[
        {"id": "c1", "tableId": "t1", "physicalName": "COD_CLIENTE",
         "logicalName": "Código de Cliente", "dataType": "STRING"},
        {"id": "c2", "tableId": "t2", "physicalName": "COD_CLIENTE_RIESGO",
         "logicalName": None, "dataType": "INT"},
    ]))
    by_ids = AsyncMock(return_value=[
        {"id": "t1", "physicalName": "M_CLIENTE", "schema": "ddv_cliente"},
        {"id": "t2", "physicalName": "H_RIESGO", "schema": "ddv_riesgo"},
    ])
    monkeypatch.setattr(service.repository, "list_tables_by_ids", by_ids)

    out = asyncio.run(service.search_columns("cod_cli", 50))
    assert [c["id"] for c in out] == ["c1", "c2"]          # orden del repo (physicalName)
    assert out[0]["table"] == "M_CLIENTE" and out[0]["schema"] == "ddv_cliente"
    assert out[1]["table"] == "H_RIESGO" and out[1]["schema"] == "ddv_riesgo"
    # los ids de tabla van dedup + ordenados (una sola query $in)
    by_ids.assert_awaited_once_with(["t1", "t2"])


def test_search_columns_descarta_huerfanas_de_tabla_inactiva(monkeypatch):
    """Una columna cuya tabla fue borrada/desactivada no debe salir como hit
    (list_tables_by_ids ya filtra flgactive)."""
    monkeypatch.setattr(service.repository, "search_columns", AsyncMock(return_value=[
        {"id": "c1", "tableId": "t1", "physicalName": "SALDO", "logicalName": None, "dataType": "DECIMAL"},
        {"id": "c9", "tableId": "t-borrada", "physicalName": "SALDO_VIEJO", "logicalName": None, "dataType": "DECIMAL"},
    ]))
    monkeypatch.setattr(service.repository, "list_tables_by_ids", AsyncMock(return_value=[
        {"id": "t1", "physicalName": "M_CUENTA", "schema": "ddv_cuenta"},
    ]))
    out = asyncio.run(service.search_columns("saldo", 50))
    assert [c["id"] for c in out] == ["c1"]


def test_endpoint_columns_contrato_http(monkeypatch):
    """GET /api/catalog/columns: `q` es obligatorio (422 sin él) y la respuesta
    va en el envelope {success, data}."""
    from starlette.testclient import TestClient

    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/api/catalog/columns").status_code == 422  # sin q

    monkeypatch.setattr(service, "search_columns", AsyncMock(return_value=[
        {"id": "c1", "tableId": "t1", "physicalName": "COD_CLIENTE",
         "logicalName": None, "dataType": "STRING", "table": "M_CLIENTE", "schema": "ddv"},
    ]))
    r = client.get("/api/catalog/columns?q=cod&limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"][0]["table"] == "M_CLIENTE" and body["data"][0]["schema"] == "ddv"
