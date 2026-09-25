"""Doc 94 D7 · dry-run del re-derivado del glosario: cuántos nombres físicos
cambiarían con los cambios del borrador (en ambos scopes, igual que el apply),
en cuántas tablas y una muestra. No escribe nada. Mismo cálculo que el apply
(`compute_rephysicalize`)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.glossary import service

TERMS = [{"id": "t1", "term": "cliente", "abbrev": "CLI", "scope": "column"},
         {"id": "t2", "term": "codigo", "abbrev": "COD", "scope": "column"}]
COLS = [
    {"_id": "c1", "tableId": "tb1", "logicalName": "codigo cliente", "physicalName": "CODCLI"},
    {"_id": "c2", "tableId": "tb1", "logicalName": "nombre cliente", "physicalName": "NOMBRECLI"},
    {"_id": "c3", "tableId": "tb2", "logicalName": "fecha alta", "physicalName": "FECHAALTA"},
    {"_id": "c4", "tableId": "tb2", "logicalName": "codigo cliente", "physicalName": "XX",
     "physicalNameOverridden": True},                       # override manual: nunca cuenta
]
TABLES = [{"_id": "tb1", "logicalName": "maestro clientes", "physicalName": "MAESTROCLIENTES"}]


def _mock(monkeypatch, tables=TABLES):
    async def list_entries(pid, scope):
        return [t for t in TERMS if t["scope"] == scope]

    async def entities(pid, scope):
        return COLS if scope == "column" else tables

    monkeypatch.setattr(service.repository, "list_entries", list_entries)
    monkeypatch.setattr(service.repository, "entities_for_rephysicalize", entities)
    monkeypatch.setattr(service.repository, "table_physical_names",
                        AsyncMock(return_value={"tb1": "MAESTROCLIENTES", "tb2": "ALTAS"}))
    monkeypatch.setattr(service.settings_service, "get_naming_for",
                        AsyncMock(return_value={"separator": "", "case": "upper"}))
    upd = AsyncMock()
    monkeypatch.setattr(service.repository, "update_physical_names", upd)
    return upd


def test_alta_que_no_renombra_nada_da_cero(monkeypatch):
    upd = _mock(monkeypatch)
    out = asyncio.run(service.impact_preview("p1", "column", [{"term": "saldo", "abbrev": "SLD"}], []))
    assert out == {"columns": 0, "columnTables": 0, "tables": 0, "sample": []}
    upd.assert_not_awaited()


def test_cambio_de_abreviatura_cuenta_columnas_tablas_y_muestra(monkeypatch):
    _mock(monkeypatch)
    out = asyncio.run(service.impact_preview("p1", "column", [{"id": "t1", "term": "cliente", "abbrev": "CLTE"}], []))
    assert (out["columns"], out["columnTables"], out["tables"]) == (2, 1, 0)
    assert {(s["entity"], s["table"], s["from"], s["to"]) for s in out["sample"]} == {
        ("column", "MAESTROCLIENTES", "CODCLI", "CODCLTE"),
        ("column", "MAESTROCLIENTES", "NOMBRECLI", "NOMBRECLTE")}


def test_borrado_vuelve_al_texto_sin_abreviar(monkeypatch):
    _mock(monkeypatch)
    out = asyncio.run(service.impact_preview("p1", "column", [], ["t2"]))
    assert out["columns"] == 1
    assert out["sample"] == [{"entity": "column", "table": "MAESTROCLIENTES", "from": "CODCLI", "to": "CODIGOCLI"}]


def test_incluye_lo_que_el_apply_repararia_en_el_otro_scope(monkeypatch):
    """El apply re-deriva AMBOS scopes: una tabla desfasada también cambiaría."""
    _mock(monkeypatch, tables=[{"_id": "tb1", "logicalName": "maestro clientes", "physicalName": "VIEJO"}])
    out = asyncio.run(service.impact_preview("p1", "column", [], []))
    assert (out["columns"], out["tables"]) == (0, 1)
    assert out["sample"] == [{"entity": "table", "table": "VIEJO", "from": "VIEJO", "to": "MAESTROCLIENTES"}]


def test_naming_del_borrador_manda_sobre_el_guardado(monkeypatch):
    _mock(monkeypatch)
    out = asyncio.run(service.impact_preview("p1", "column", [], [], {"separator": "_", "case": "upper"}))
    assert out["columns"] == 3            # CODCLI→COD_CLI, NOMBRECLI→NOMBRE_CLI, FECHAALTA→FECHA_ALTA
