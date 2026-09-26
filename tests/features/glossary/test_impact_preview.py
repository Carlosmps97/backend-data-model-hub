"""Doc 94 D7 + doc 95 D3/D4 · dry-run del re-derivado del glosario: las listas
COMPLETAS de nombres que cambiarían al aplicar el borrador, separadas en lo que
renombra el borrador (`renamed`) y lo que ya estaba desfasado y el apply repara
de paso (`outOfSync`, p. ej. un U+00A0 venido del XML). Recorre ambos scopes,
igual que el apply. No escribe nada."""
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


def _mock(monkeypatch, tables=TABLES, cols=COLS):
    async def list_entries(pid, scope):
        return [t for t in TERMS if t["scope"] == scope]

    async def entities(pid, scope):
        return cols if scope == "column" else tables

    monkeypatch.setattr(service.repository, "list_entries", list_entries)
    monkeypatch.setattr(service.repository, "entities_for_rephysicalize", entities)
    monkeypatch.setattr(service.repository, "table_physical_names",
                        AsyncMock(return_value={"tb1": "MAESTROCLIENTES", "tb2": "ALTAS"}))
    monkeypatch.setattr(service.settings_service, "get_naming_for",
                        AsyncMock(return_value={"separator": "", "case": "upper"}))
    upd = AsyncMock()
    monkeypatch.setattr(service.repository, "update_physical_names", upd)
    return upd


def test_alta_que_no_renombra_nada_no_trae_filas(monkeypatch):
    upd = _mock(monkeypatch)
    out = asyncio.run(service.impact_preview("p1", "column", [{"term": "saldo", "abbrev": "SLD"}], []))
    assert out == {"renamed": [], "outOfSync": []}
    upd.assert_not_awaited()


def test_cambio_de_abreviatura_trae_la_lista_completa(monkeypatch):
    _mock(monkeypatch)
    out = asyncio.run(service.impact_preview("p1", "column", [{"id": "t1", "term": "cliente", "abbrev": "CLTE"}], []))
    assert out["outOfSync"] == []
    assert out["renamed"] == [
        {"entity": "column", "tableId": "tb1", "table": "MAESTROCLIENTES", "from": "CODCLI", "to": "CODCLTE"},
        {"entity": "column", "tableId": "tb1", "table": "MAESTROCLIENTES", "from": "NOMBRECLI", "to": "NOMBRECLTE"}]


def test_borrado_vuelve_al_texto_sin_abreviar(monkeypatch):
    _mock(monkeypatch)
    out = asyncio.run(service.impact_preview("p1", "column", [], ["t2"]))
    assert out["renamed"] == [{"entity": "column", "tableId": "tb1", "table": "MAESTROCLIENTES",
                               "from": "CODCLI", "to": "CODIGOCLI"}]


def test_tabla_desfasada_del_otro_scope_va_aparte(monkeypatch):
    """Doc 95 D3: la tabla con U+00A0 no la renombra el término; el apply la
    repara de paso y el popup lo dice aparte."""
    _mock(monkeypatch, tables=[{"_id": "tb1", "logicalName": "maestro clientes",
                                "physicalName": "MAESTROCLIENTES\u00a0"}])
    out = asyncio.run(service.impact_preview("p1", "column", [{"term": "saldo", "abbrev": "SLD"}], []))
    assert out["renamed"] == []
    assert out["outOfSync"] == [{"entity": "table", "tableId": "tb1", "table": "MAESTROCLIENTES\u00a0",
                                 "from": "MAESTROCLIENTES\u00a0", "to": "MAESTROCLIENTES"}]


def test_columna_desfasada_del_mismo_scope_no_se_confunde_con_el_cambio(monkeypatch):
    cols = [*COLS, {"_id": "c9", "tableId": "tb2", "logicalName": "fecha alta", "physicalName": "FECHAALTA\u00a0"}]
    _mock(monkeypatch, cols=cols)
    out = asyncio.run(service.impact_preview("p1", "column", [{"id": "t1", "term": "cliente", "abbrev": "CLTE"}], []))
    assert [r["from"] for r in out["renamed"]] == ["CODCLI", "NOMBRECLI"]
    assert out["outOfSync"] == [{"entity": "column", "tableId": "tb2", "table": "ALTAS",
                                 "from": "FECHAALTA\u00a0", "to": "FECHAALTA"}]


def test_naming_del_borrador_manda_sobre_el_guardado(monkeypatch):
    _mock(monkeypatch)
    out = asyncio.run(service.impact_preview("p1", "column", [], [], {"separator": "_", "case": "upper"}))
    assert len(out["renamed"]) == 3            # CODCLI→COD_CLI, NOMBRECLI→NOMBRE_CLI, FECHAALTA→FECHA_ALTA


def test_clasificar_equivale_a_lo_que_re_deriva_el_apply():
    """Invariante: renamed ∪ outOfSync = exactamente lo que el apply re-deriva."""
    ents = [*COLS, {"_id": "c9", "tableId": "tb2", "logicalName": "fecha alta", "physicalName": "X\u00a0"}]
    cur = {"cliente": "CLI", "codigo": "COD"}
    draft = {"cliente": "CLTE", "codigo": "COD"}
    ren, oos = service.classify_rephysicalize(ents, cur, draft, ("", "upper"), ("", "upper"))
    assert sorted(ren + oos) == sorted(service.compute_rephysicalize(ents, draft, "", "upper"))
    assert [i for i, _ in oos] == ["c9"]
