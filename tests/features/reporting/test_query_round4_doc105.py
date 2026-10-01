"""Doc 105 (ronda 4, revisor R9) — motor de consulta del Reporting.

Base: los repros de `R9/test_r9_findings.py`, adaptados. Cada bloque dice qué
fallaba. Lo del FRONT (Validate → builder con `maxRows`, filtro booleano sin
`value`) lo corrige otro agente; aquí sólo el backend."""
from __future__ import annotations

import asyncio

import pytest
import sqlglot
from starlette.testclient import TestClient

from app.core.db import client as db_client
from app.features.reporting import views
from app.features.reporting.query.compiler import compile_spec
from app.features.reporting.query.schema import build_catalog
from app.features.reporting.query.spec import QuerySpec
from app.main import app

from .lakebase_sql_doc105 import LakebaseCheckingDb

ON = {"projectId": "p1", "flgactive": True}
NUMS = ["5", "10", "9", "100", "30", "7", "12", "1"]          # UDP «Num» (number): se guarda como TEXTO
FLAGS = ["false", "true", "false", "Sí", "no", "yes", "false", "true"]   # «Flag»: Excel "true"/"false", UI texto libre
UDPS = [{"_id": "u_num", **ON, "name": "Num", "level": "column", "dataType": "number"},
        {"_id": "u_bool", **ON, "name": "Flag", "level": "column", "dataType": "boolean"},
        {"_id": "u_date", **ON, "name": "Fecha", "level": "column", "dataType": "date"}]


@pytest.fixture
def qdb(monkeypatch) -> LakebaseCheckingDb:
    fake = LakebaseCheckingDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    raw = fake.raw
    raw["udp_definitions"].insert_many(UDPS)
    raw["parent_domains"].insert_many([{"_id": f"d{i}", **ON, "name": f"DOM{i}"} for i in range(4)])
    raw["canonical_tables"].insert_many([
        {"_id": "t1", **ON, "physicalName": "CLIENTE", "schema": "core"},
        {"_id": "t2", **ON, "physicalName": "CUENTA", "schema": "core"},
        {"_id": "t3", **ON, "physicalName": "RIESGO", "schema": "risk"}])
    raw["canonical_columns"].insert_many([
        {"_id": f"c{i}", **ON, "tableId": f"t{1 + i % 3}", "physicalName": f"C{i}", "logicalName": f"Col {i}",
         "dataType": "INT" if i % 2 else "STRING", "ordinal": i if i != 7 else None, "isPrimaryKey": i % 4 == 0,
         "parentDomainId": f"d{i % 4}",
         "udpValues": {"u_num": NUMS[i], "u_bool": FLAGS[i], "u_date": f"2026-01-0{1 + i}"}}
        for i in range(8)])
    return fake


@pytest.fixture
def raw() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _sql(raw, text: str) -> tuple[int, object]:
    res = raw.post("/api/reporting/query/sql", json={"text": text, "projectId": "p1"})
    return res.status_code, (res.json()["data"] if res.status_code == 200 else res.json().get("detail"))


def _names(data) -> list[str]:
    return sorted(r["physicalName"] for r in data["rows"])


def _validate(raw, text: str) -> str | None:
    res = raw.post("/api/reporting/query/validate", json={"text": text, "projectId": "p1"})
    assert res.status_code == 200, res.text[:200]
    errors = res.json()["data"]["errors"]
    return errors[0]["message"] if errors else None


# ── `;` inicial, un solo parseo y tope del texto ─────────────────────────────
@pytest.mark.parametrize("route", ["/api/reporting/query/sql", "/api/reporting/query/validate"])
@pytest.mark.parametrize("text", ["; SELECT physicalName FROM columns", ";;SELECT physicalName FROM columns"])
def test_r4_punto_y_coma_inicial_no_es_500(qdb, raw, route, text):
    """Repro R9 (`test_punto_y_coma_inicial_no_es_500`)."""
    res = raw.post(route, json={"text": text, "projectId": "p1"})
    assert res.status_code == 200, (res.status_code, res.text[:200])


@pytest.mark.parametrize("route", ["/api/reporting/query/sql", "/api/reporting/query/validate"])
def test_r4_el_sql_se_parsea_una_sola_vez(qdb, raw, monkeypatch, route):
    calls: list[str] = []
    real = sqlglot.parse

    def spy(sql, *a, **k):
        calls.append(sql)
        return real(sql, *a, **k)

    monkeypatch.setattr(sqlglot, "parse", spy)
    res = raw.post(route, json={"text": "SELECT physicalName FROM columns", "projectId": "p1"})
    assert res.status_code == 200 and len(calls) == 1, calls


def test_r4_texto_desmedido_es_422(qdb, raw):
    text = "SELECT physicalName FROM columns WHERE " + " OR ".join(["ordinal = 1"] * 9000)   # > 100 KB
    assert len(text) > 100_000
    for route in ("/api/reporting/query/sql", "/api/reporting/query/validate"):
        assert raw.post(route, json={"text": text, "projectId": "p1"}).status_code == 422


# ── UDP number / boolean / date ──────────────────────────────────────────────
NUM_TEXT = "UDP numbers are stored as text"


@pytest.mark.parametrize("text", [
    'SELECT physicalName FROM columns WHERE udp."Num" > 5',                 # repro R9: [7, 9] («10» < «5»)
    'SELECT physicalName FROM columns WHERE udp."Num" BETWEEN 5 AND 20',
    'SELECT SUM(udp."Num") AS s FROM columns',                              # repro R9: 0
    'SELECT MAX(udp."Num") AS m FROM columns',                              # repro R9: '9'
    'SELECT AVG(udp."Num") AS a FROM columns',
])
def test_r4_udp_numerico_no_compara_ni_agrega_como_texto(qdb, raw, text):
    status, detail = _sql(raw, text)
    assert status == 422 and NUM_TEXT in detail, (status, detail)
    assert NUM_TEXT in (_validate(raw, text) or "")


def test_r4_udp_numerico_desde_el_builder(qdb, raw):
    """Repro R9 (`test_udp_numerico_desde_el_builder`)."""
    spec = {"projectId": "p1", "from": "columns", "select": ["physicalName"],
            "where": {"op": "and", "conditions": [{"field": "udp.u_num", "op": "gte", "value": "10"}]}}
    res = raw.post("/api/reporting/query", json=spec)
    assert res.status_code == 422 and NUM_TEXT in res.json()["detail"]


def test_r4_udp_numerico_el_catalogo_no_ofrece_rangos(qdb, raw):
    fields = raw.get("/api/reporting/catalog", params={"projectId": "p1", "from": "columns"}).json()["data"]["fields"]
    num = next(f for f in fields if f["key"] == "udp.u_num")
    assert num["ops"] == ["eq", "ne", "in", "exists", "isnull"]


@pytest.mark.parametrize("text, rows", [
    ('SELECT physicalName FROM columns WHERE udp."Num" = 10', ["C1"]),
    ('SELECT physicalName FROM columns WHERE udp."Num" = 1e1', ["C1"]),          # 10.0 → «10»
    ('SELECT physicalName FROM columns WHERE udp."Num" IN (100, 12)', ["C3", "C6"]),
    ("SELECT physicalName FROM columns WHERE udp.\"Num\" = '30'", ["C4"]),
])
def test_r4_udp_numerico_igualdad_por_texto(qdb, raw, text, rows):
    status, data = _sql(raw, text)
    assert status == 200 and _names(data) == rows, (status, data)


@pytest.mark.parametrize("text, rows", [
    ('SELECT physicalName FROM columns WHERE udp."Flag" = TRUE', ["C1", "C3", "C5", "C7"]),   # repro R9: 0 filas
    ('SELECT physicalName FROM columns WHERE udp."Flag" = FALSE', ["C0", "C2", "C4", "C6"]),
    ("SELECT physicalName FROM columns WHERE udp.\"Flag\" = 'true'", ["C1", "C3", "C5", "C7"]),
    ('SELECT physicalName FROM columns WHERE udp."Flag" <> TRUE', ["C0", "C2", "C4", "C6"]),
])
def test_r4_udp_booleano_normaliza_la_grafia(qdb, raw, text, rows):
    status, data = _sql(raw, text)
    assert status == 200 and _names(data) == rows, (status, data)
    assert not qdb.problems, qdb.problems


def test_r4_udp_fecha_compara_texto_iso(qdb, raw):
    status, data = _sql(raw, "SELECT physicalName FROM columns WHERE udp.\"Fecha\" >= '2026-01-06'")
    assert status == 200 and _names(data) == ["C5", "C6", "C7"]


# ── Campo de la TABLA (schema en columns) ────────────────────────────────────
@pytest.mark.parametrize("spec", [
    {"groupBy": ["schema"], "aggregations": [{"fn": "count", "as": "count"}]},     # repro R9: un grupo null
    {"aggregations": [{"fn": "countDistinct", "field": "schema", "as": "d"}]},
    {"aggregations": [{"fn": "min", "field": "schema", "as": "m"}]},
])
def test_r4_campo_de_la_tabla_no_se_agrupa_ni_agrega(qdb, raw, spec):
    res = raw.post("/api/reporting/query", json={"projectId": "p1", "from": "columns", **spec})
    assert res.status_code == 422 and "comes from the table" in res.json()["detail"], res.text[:200]


def test_r4_schema_de_columnas_no_es_agrupable_en_el_catalogo(qdb, raw):
    fields = raw.get("/api/reporting/catalog", params={"projectId": "p1", "from": "columns"}).json()["data"]["fields"]
    assert next(f for f in fields if f["key"] == "schema")["groupable"] is False


# ── Orden agrupado por un BOOLEANO (Lakebase empataba true/false) ────────────
def _lakebase_order_key(v):
    """Espejo de `aggregate._order_by` (rank, numérico, texto) — repro R9."""
    if v is None:
        return (0, 0, "")
    if isinstance(v, bool):
        return (3, 0, "")                         # ELSE 3: true y false EMPATAN
    if isinstance(v, (int, float)):
        return (1, v, "")
    return (2, 0, v)


def test_r4_orden_agrupado_por_booleano(qdb, raw):
    spec = {"projectId": "p1", "from": "columns", "groupBy": ["isPrimaryKey"],
            "aggregations": [{"fn": "count", "as": "n"}], "orderBy": [{"field": "isPrimaryKey", "dir": "desc"}]}
    res = raw.post("/api/reporting/query", json=spec)
    assert res.status_code == 200 and res.json()["data"]["rows"] == [{"isPrimaryKey": True, "n": 2},
                                                                    {"isPrimaryKey": False, "n": 6}]
    compiled = compile_spec(QuerySpec.model_validate(spec), build_catalog("columns", []))
    (sort_key, _), = compiled.sort
    # La clave del $sort es un NÚMERO (0/1, null para vacío): Lakebase la ordena.
    helper = compiled.project[sort_key]
    assert sort_key not in compiled.output and "$cond" in str(helper)
    assert _lakebase_order_key(1) != _lakebase_order_key(0)
    assert not qdb.problems, qdb.problems


# ── SUM sin valores numéricos = NULL (como SQL) ─────────────────────────────
def test_r4_sum_de_solo_nulos_es_null(qdb, raw):
    """Repro R9 (`test_sum_de_solo_nulos_es_null_como_sql`)."""
    status, data = _sql(raw, "SELECT SUM(ordinal) AS s FROM columns WHERE physicalName = 'C7'")
    assert status == 200 and data["rows"] == [{"s": None}]
    status, data = _sql(raw, "SELECT tableId, SUM(ordinal) AS s FROM columns GROUP BY tableId ORDER BY tableId")
    assert data["rows"] == [{"tableId": "t1", "s": 9}, {"tableId": "t2", "s": 5}, {"tableId": "t3", "s": 7}]
    assert not qdb.problems, qdb.problems


@pytest.mark.parametrize("text", ["SELECT SUM(physicalName) AS s FROM columns",
                                  "SELECT AVG(isPrimaryKey) AS a FROM columns"])
def test_r4_sum_avg_de_un_campo_no_numerico_es_422(qdb, raw, text):
    status, detail = _sql(raw, text)
    assert status == 422 and "need a numeric field" in detail, (status, detail)


# ── /query/validate valida lo mismo que /query/sql ───────────────────────────
@pytest.mark.parametrize("text", [
    "SELECT physicalName FROM columns WHERE tableId > 't2'",
    "SELECT physicalName FROM columns ORDER BY logicalName",
    "SELECT physicalName FROM columns WHERE schema <> 'core'",
    "SELECT tableCount FROM models WHERE tableCount > 1",
    "SELECT viewName FROM view_columns GROUP BY viewName",
])
def test_r4_validate_no_da_valido_lo_que_run_rechaza(qdb, raw, text):
    """Repro R9 (`test_validate_no_da_valido_lo_que_run_rechaza`)."""
    run = raw.post("/api/reporting/query/sql", json={"text": text, "projectId": "p1"})
    assert run.status_code in (400, 422)
    assert _validate(raw, text) == run.json()["detail"]


# ── view_columns: desempate único ────────────────────────────────────────────
def test_r4_view_columns_pagina_sin_repetir_ni_saltar(qdb, raw):
    qdb.raw["views"].insert_many([
        {"_id": f"v{i}", **ON, "name": "MISMO_NOMBRE", "schema": "s_vu",
         "sources": [{"tableId": "t1", "column": f"K{j}", "outputAlias": f"A{j}"} for j in (3, 1, 2)]}
        for i in range(2)])
    got, cursor = [], None
    for _ in range(10):
        res = raw.post("/api/reporting/query" + (f"?cursor={cursor}" if cursor else ""),
                       json={"projectId": "p1", "from": "view_columns", "select": ["viewName", "outputName"],
                             "limit": 1})
        got += [r["_id"] for r in res.json()["data"]["rows"]]
        cursor = res.json()["data"]["nextCursor"]
        if not cursor:
            break
    assert got == ["v0#A1", "v0#A2", "v0#A3", "v1#A1", "v1#A2", "v1#A3"], got


# ── Scorecard: columnas de tablas inactivas ─────────────────────────────────
def test_r4_scorecard_no_cuenta_columnas_de_tablas_inactivas(monkeypatch):
    """Base: `R9/scorecard_check.py`."""
    fake = LakebaseCheckingDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    fake.raw["canonical_tables"].insert_many(
        [{"_id": f"t{i}", **ON, "physicalName": f"T{i}"} for i in range(1, 7)]
        + [{"_id": "t9", "projectId": "p1", "flgactive": False, "physicalName": "OFF"}])
    fake.raw["canonical_columns"].insert_many([
        {"_id": "c1", **ON, "tableId": "t1", "isPrimaryKey": True, "isForeignKey": True},
        {"_id": "c2", **ON, "tableId": "t2", "isPrimaryKey": True, "flgactive": False},   # PK inactiva
        {"_id": "c3", **ON, "tableId": "t3", "description": "ok"},
        {"_id": "c9", **ON, "tableId": "t9", "isPrimaryKey": True, "isForeignKey": True}])  # tabla inactiva
    card = asyncio.run(views.scorecard("p1"))
    assert (card["tables"], card["tablesWithoutPk"], card["columns"], card["pkColumns"], card["fkColumns"]) \
        == (6, 5, 2, 1, 1), card
    assert card["columnsWithoutDescription"] == 1 and not fake.problems, fake.problems


# ── SQL legítimo que daba 400 ───────────────────────────────────────────────
@pytest.mark.parametrize("text, rows", [
    ("SELECT physicalName FROM columns WHERE ordinal BETWEEN 2 AND 4", ["C2", "C3", "C4"]),
    ("SELECT physicalName FROM columns WHERE ordinal NOT BETWEEN 1 AND 5", ["C0", "C6", "C7"]),
    ("SELECT physicalName FROM COLUMNS WHERE PHYSICALNAME = 'C1'", ["C1"]),
    ("SELECT physicalName FROM Columns AS X WHERE x.ordinal = 2", ["C2"]),
    ("SELECT physicalName FROM columns WHERE isPrimaryKey IS NULL", []),
    ("SELECT physicalName FROM columns WHERE isPrimaryKey IS NOT NULL",
     ["C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7"]),
])
def test_r4_sql_legitimo_filas(qdb, raw, text, rows):
    status, data = _sql(raw, text)
    assert status == 200 and _names(data) == rows, (status, data)


@pytest.mark.parametrize("text, columns, rows", [
    ("SELECT tableId, COUNT(*) FROM columns GROUP BY 1 ORDER BY 2 DESC, 1",
     ["tableId", "count"], [["t1", 3], ["t2", 3], ["t3", 2]]),
    ("SELECT tableId, COUNT(*) AS n FROM columns GROUP BY tableId ORDER BY COUNT(*), tableId DESC",
     ["tableId", "n"], [["t3", 2], ["t2", 3], ["t1", 3]]),
    ("SELECT COUNT(*), COUNT(DISTINCT dataType) FROM columns", ["count", "count_2"], [[8, 2]]),
    ("SELECT tableId, COUNT(*) AS Total FROM columns GROUP BY tableId ORDER BY total, tableId",
     ["tableId", "Total"], [["t3", 2], ["t1", 3], ["t2", 3]]),
    ("SELECT dataType, tableId, COUNT(*) AS n FROM columns GROUP BY tableId, dataType ORDER BY n DESC, dataType, tableId",
     ["dataType", "tableId", "n"],
     [["INT", "t2", 2], ["STRING", "t1", 2], ["INT", "t1", 1], ["INT", "t3", 1], ["STRING", "t2", 1],
      ["STRING", "t3", 1]]),
])
def test_r4_sql_legitimo_agrupado(qdb, raw, text, columns, rows):
    status, data = _sql(raw, text)
    assert status == 200, (status, data)
    keys = [c["key"] for c in data["columns"]]
    assert keys == columns and [[r[k] for k in keys] for r in data["rows"]] == rows, data


# Ronda 5: los agregados antes de las dimensiones y la dimensión agrupada fuera
# del SELECT ya NO son 400 — se soportan (`test_query_round5_doc105.py`).
@pytest.mark.parametrize("text, message", [
    ("SELECT tableId, COUNT(*) AS n FROM columns GROUP BY tableId ORDER BY MAX(ordinal)",
     "ORDER BY an aggregation that is in the SELECT: MAX(ordinal)"),
    ("SELECT physicalName FROM columns ORDER BY 3", "ORDER BY 3: there is no SELECT item 3."),
])
def test_r4_lo_que_no_se_soporta_se_explica(qdb, raw, text, message):
    status, detail = _sql(raw, text)
    assert status == 400 and detail == message, (status, detail)
    assert _validate(raw, text) == message


# ── Hidratación por página ───────────────────────────────────────────────────
def test_r4_hidratacion_lee_solo_lo_de_la_pagina(qdb, raw, monkeypatch):
    from . import lakebase_sql_doc105 as lk
    reads: list[tuple[str, dict]] = []
    real = lk._Collection.find

    def spy(self, flt=None, projection=None):
        if self.name in ("parent_domains", "canonical_tables"):
            reads.append((self.name, flt))
        return real(self, flt, projection)

    monkeypatch.setattr(lk._Collection, "find", spy)
    res = raw.post("/api/reporting/query", json={"projectId": "p1", "from": "columns", "limit": 2,
                                                  "select": ["physicalName", "parentDomainId", "schema"]})
    rows = res.json()["data"]["rows"]
    assert [(r["physicalName"], r["parentDomainId"], r["schema"]) for r in rows] == [("C0", "DOM0", "core"),
                                                                                   ("C1", "DOM1", "core")]
    by = {name: flt for name, flt in reads}
    assert by["parent_domains"]["_id"] == {"$in": ["d0", "d1"]}, by
    assert by["canonical_tables"]["_id"] == {"$in": ["t1", "t2"]}, by


def test_r4_view_columns_el_desempate_no_pisa_la_direccion_pedida(qdb, raw):
    """Ordenar por `outputName` (= `sources.outputAlias`, que también es
    desempate) DESC sigue siendo DESC."""
    qdb.raw["views"].insert_one({"_id": "v0", **ON, "name": "V", "schema": "s_vu",
                                 "sources": [{"tableId": "t1", "column": f"K{j}", "outputAlias": f"A{j}"}
                                             for j in (2, 3, 1)]})
    res = raw.post("/api/reporting/query", json={"projectId": "p1", "from": "view_columns", "select": ["outputName"],
                                                  "orderBy": [{"field": "outputName", "dir": "desc"}]})
    assert [r["outputName"] for r in res.json()["data"]["rows"]] == ["A3", "A2", "A1"]
