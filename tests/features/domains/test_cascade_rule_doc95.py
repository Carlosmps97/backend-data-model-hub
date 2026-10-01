"""Doc 95 · LA regla de la cascada de tipos de un parent domain (pura) y la
vista previa EXACTA por columna: lo que se ve en el popup es lo que re-tipa el
apply, el revert y el rollback."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.features.domains import cascade, repository, service

# Dominio «Codigo Clave»: físico INTEGER, lógico NUMBER.
TYPES = {"physical": "INTEGER", "logical": "NUMBER"}
COLS = [
    {"_id": "c1", "tableId": "t1", "physicalName": "CODCLI", "logicalName": "codigo cliente",
     "dataType": "INTEGER", "logicalDataType": "NUMBER"},                            # lo sigue
    {"_id": "c2", "tableId": "t1", "physicalName": "CODCTA", "logicalName": "codigo cuenta",
     "dataType": "BIGINT", "logicalDataType": "NUMBER", "typeOverridden": True},     # override físico
    {"_id": "c3", "tableId": "t2", "physicalName": "CODAPP", "logicalName": "codigo app",
     "dataType": "INT", "logicalDataType": "NUMBER"},                                # tipo ya distinto
    {"_id": "c4", "tableId": "t2", "physicalName": "CODOFI", "logicalName": "codigo oficina",
     "dataType": "INTEGER", "logicalDataType": "TEXT", "logicalTypeOverridden": True},
]
NAMES = {"t1": {"schema": "core", "physicalName": "CLIENTE", "logicalName": "cliente"},
         "t2": {"schema": None, "physicalName": "APLICACION", "logicalName": "aplicacion"}}


# ── la regla (pura) ──

def test_retype_filter_es_la_regla_de_ida():
    assert cascade.retype_filter("pd", "physical", "INTEGER") == {
        "parentDomainId": "pd", "typeOverridden": {"$ne": True}, "dataType": "INTEGER",
        "flgactive": {"$ne": False}}
    assert cascade.retype_filter("pd", "logical", None) == {
        "parentDomainId": "pd", "logicalTypeOverridden": {"$ne": True}, "logicalDataType": None,
        "flgactive": {"$ne": False}}


@pytest.mark.parametrize("col, facet, expected", [
    (COLS[0], "physical", "change"), (COLS[1], "physical", "override"),
    (COLS[2], "physical", "differs"), (COLS[3], "logical", "override"), (COLS[3], "physical", "change"),
])
def test_column_status_espejo_del_filtro(col, facet, expected):
    assert cascade.column_status(col, facet, TYPES[facet]) == expected


def test_column_status_sin_tipo_actual():
    assert cascade.column_status({}, "logical", None) == "change"
    assert cascade.column_status({"logicalDataType": "TEXT"}, "logical", None) == "differs"


def test_classify_sin_cambio_mide_la_fisica_contra_el_tipo_actual():
    rows = cascade.classify_columns(COLS, TYPES, {})
    assert [(r["columnId"], r["status"], r["physical"], r["logical"]) for r in rows] == [
        ("c1", "change", "change", None), ("c2", "override", "override", None),
        ("c3", "differs", "differs", None), ("c4", "change", "change", None)]


def test_classify_con_ambas_facetas_basta_una_que_cambie():
    rows = {r["columnId"]: r for r in cascade.classify_columns(COLS, TYPES, {"physical": "UUID", "logical": "TEXT"})}
    assert (rows["c2"]["physical"], rows["c2"]["logical"], rows["c2"]["status"]) == ("override", "change", "change")
    assert (rows["c4"]["physical"], rows["c4"]["logical"], rows["c4"]["status"]) == ("change", "override", "change")
    assert rows["c3"]["status"] == "change"      # su lógico sí sigue al dominio


def test_summarize_totales_globales_filtro_orden_y_pagina():
    rows = cascade.classify_columns(COLS, TYPES, {"physical": "UUID"})
    out = cascade.summarize_columns(rows, NAMES, q="cod", status="change", offset=0, limit=1)
    assert out["totals"] == {"columns": 4, "tables": 2, "change": 2, "changeTables": 2,
                             "override": 1, "differs": 1}
    assert out["matched"] == 2
    assert [r["column"] for r in out["rows"]] == ["CODOFI"]                  # APLICACION < CLIENTE
    assert (out["rows"][0]["table"], out["rows"][0]["schema"]) == ("APLICACION", None)
    page2 = cascade.summarize_columns(rows, NAMES, status="change", offset=1, limit=1)
    assert [r["column"] for r in page2["rows"]] == ["CODCLI"]


def test_summarize_busca_por_tabla_o_atributo_sin_mayusculas():
    rows = cascade.classify_columns(COLS, TYPES, {})
    assert [r["columnId"] for r in cascade.summarize_columns(rows, NAMES, q="APLICA")["rows"]] == ["c3", "c4"]
    assert [r["columnId"] for r in cascade.summarize_columns(rows, NAMES, q="codigo cuenta")["rows"]] == ["c2"]


# ── repositorio: re-tipo con la regla ──

class _Cols:
    def __init__(self):
        self.calls = []

    async def update_many(self, flt, upd):
        self.calls.append((flt, upd["$set"]))
        return SimpleNamespace(modified_count=7)


def test_retype_usa_la_regla_y_devuelve_lo_modificado(monkeypatch):
    cols = _Cols()
    monkeypatch.setattr(repository, "get_db", AsyncMock(return_value={"canonical_columns": cols}))
    assert asyncio.run(repository.retype("pd", "physical", "INTEGER", "UUID")) == 7
    flt, sets = cols.calls[0]
    assert flt == cascade.retype_filter("pd", "physical", "INTEGER")
    assert sets["dataType"] == "UUID" and "updatedAt" in sets
    assert asyncio.run(repository.retype("pd", "physical", "UUID", "UUID")) == 0   # mismo tipo
    assert asyncio.run(repository.retype("pd", "logical", "NUMBER", None)) == 0    # nunca a vacío
    assert len(cols.calls) == 1


# ── servicio: vista previa exacta ──

def _svc(monkeypatch, *, types=("INTEGER", "NUMBER"), cols=COLS):
    monkeypatch.setattr(service.repository, "get_domain_types", AsyncMock(return_value=types))
    monkeypatch.setattr(service.repository, "domain_columns", AsyncMock(return_value=cols))
    monkeypatch.setattr(service.repository, "tables_by_ids", AsyncMock(return_value=NAMES))
    monkeypatch.setattr(service.repository, "models_affected", AsyncMock(return_value=3))


def test_column_impact_homologa_el_tipo_nuevo_y_cuenta_modelos(monkeypatch):
    _svc(monkeypatch)
    out = asyncio.run(service.column_impact("pd", physical_to="bigint"))
    assert out["targets"] == {"physical": "BIGINT"} and out["domain"] == TYPES
    assert out["totals"]["change"] == 2 and out["totals"]["models"] == 3
    # los modelos se cuentan sobre las tablas que CAMBIAN
    assert set(service.repository.models_affected.await_args.args[0]) == {"t1", "t2"}


def test_column_impact_tipo_igual_al_actual_no_es_cambio(monkeypatch):
    _svc(monkeypatch)
    out = asyncio.run(service.column_impact("pd", physical_to="integer", logical_to=""))
    assert out["targets"] == {}          # integer = INTEGER; '' en lógico = sin tipo → no cascadea


def test_column_impact_solo_totales_no_resuelve_nombres(monkeypatch):
    _svc(monkeypatch)
    out = asyncio.run(service.column_impact("pd", limit=0))
    assert out["rows"] == [] and out["totals"]["columns"] == 4
    service.repository.tables_by_ids.assert_not_awaited()


def test_column_impact_panel_cuenta_modelos_de_todas_las_tablas_del_dominio(monkeypatch):
    """Final review #6: el panel del dominio (sin tipos nuevos) dice «across K data
    models» sobre TODAS las tablas que usan el dominio, como el panel de antes —
    también la que solo tiene columnas con override."""
    solo_override = {"_id": "c5", "tableId": "t3", "physicalName": "CODX", "logicalName": "codigo x",
                     "dataType": "BIGINT", "logicalDataType": "NUMBER", "typeOverridden": True}
    _svc(monkeypatch, cols=[*COLS, solo_override])
    out = asyncio.run(service.column_impact("pd", limit=0))
    assert out["totals"]["models"] == 3 and out["totals"]["tables"] == 3
    assert set(service.repository.models_affected.await_args.args[0]) == {"t1", "t2", "t3"}


def test_count_retype_cuenta_lo_que_re_tipa_la_cascada(monkeypatch):
    _svc(monkeypatch)
    assert asyncio.run(service.count_retype("pd", "BIGINT", None)) == 2
    assert asyncio.run(service.count_retype("pd", "INTEGER", None)) == 0


def test_ruta_impact_columns_delegada(project_client, monkeypatch):
    spy = AsyncMock(return_value={"domain": TYPES, "targets": {}, "totals": {}, "matched": 0, "rows": []})
    monkeypatch.setattr(service, "column_impact", spy)
    resp = project_client.get("/api/projects/p1/domains/pd/impact/columns"
                              "?physicalTo=UUID&q=cli&status=change&offset=5&limit=50")
    assert resp.status_code == 200
    spy.assert_awaited_once_with("pd", "UUID", None, q="cli", status="change", offset=5, limit=50)
    assert project_client.get("/api/projects/p1/domains/pd/impact/columns?status=bogus").status_code == 422


# ── Doc 95 D10: restore del modelo alineado al dominio vigente (puro) ──

TYPES_BY_ID = {"pd": ("UUID", "TEXT")}


def test_restore_de_columna_que_hoy_sigue_al_dominio_conserva_el_tipo_vigente():
    current = {"parentDomainId": "pd", "dataType": "UUID", "logicalDataType": "TEXT"}
    payload = {"parentDomainId": "pd", "dataType": "INTEGER", "logicalDataType": "NUMBER", "physicalName": "CODCLI"}
    out = cascade.align_restored_column(payload, current, TYPES_BY_ID)
    assert (out["dataType"], out["logicalDataType"], out["physicalName"]) == ("UUID", "TEXT", "CODCLI")
    assert payload["dataType"] == "INTEGER"          # no muta el original


def test_restore_con_override_o_divorciada_queda_tal_cual():
    divorced = {"parentDomainId": "pd", "dataType": "BIGINT", "logicalDataType": "TEXT", "typeOverridden": True}
    payload = {"parentDomainId": "pd", "dataType": "INTEGER", "logicalDataType": "NUMBER"}
    out = cascade.align_restored_column(payload, divorced, TYPES_BY_ID)
    assert (out["dataType"], out["logicalDataType"]) == ("INTEGER", "TEXT")   # física divorciada; lógica sigue
    with_override = {**payload, "typeOverridden": True}
    current = {"parentDomainId": "pd", "dataType": "UUID"}
    assert cascade.align_restored_column(with_override, current, TYPES_BY_ID)["dataType"] == "INTEGER"


def test_restore_que_cambia_de_dominio_o_sin_dominio_no_se_toca():
    payload = {"parentDomainId": "otro", "dataType": "INTEGER"}
    assert cascade.align_restored_column(payload, {"parentDomainId": "pd", "dataType": "UUID"}, TYPES_BY_ID) == payload
    assert cascade.align_restored_column({"dataType": "X"}, None, TYPES_BY_ID) == {"dataType": "X"}
    assert cascade.align_restored_column({"parentDomainId": "pd"}, {"parentDomainId": "pd"}, {}) == {"parentDomainId": "pd"}
