"""Doc 105 (ronda 5, revisor R12) — motor de consulta del Reporting.

Base: `R12/test_r12_findings.py` y `R12/test_r12_equivalence.py`, adaptados.
Además, dos formas de SQL agrupado que la ronda 4 rechazaba con 400 y ahora se
soportan: agregados antes de las dimensiones en el SELECT (las columnas salen en
el orden del SELECT) y una dimensión agrupada que no está en el SELECT (se
agrupa igual y la columna no sale)."""
from __future__ import annotations

import asyncio
import csv
import io
import time

import pytest
from starlette.testclient import TestClient

from app.core.db import client as db_client
from app.features.reporting.query import router as query_router
from app.features.reporting.query.compiler import compile_spec
from app.features.reporting.query.schema import build_catalog
from app.features.reporting.query.spec import QuerySpec
from app.main import app

from .lakebase_sql_doc105 import LakebaseCheckingDb

ON = {"projectId": "p1", "flgactive": True}
UDPS = [{"_id": "u_num", **ON, "name": "Num", "level": "column", "dataType": "number"},
        {"_id": "u_bool", **ON, "name": "Flag", "level": "column", "dataType": "boolean"}]


@pytest.fixture
def qdb(monkeypatch) -> LakebaseCheckingDb:
    fake = LakebaseCheckingDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    raw = fake.raw
    raw["udp_definitions"].insert_many(UDPS)
    raw["canonical_tables"].insert_many([{"_id": f"t{i}", **ON, "physicalName": f"T{i}"} for i in range(1, 5)])
    raw["canonical_columns"].insert_many([
        {"_id": f"c{i}", **ON, "tableId": f"t{1 + i % 4}", "physicalName": f"C{i}", "ordinal": i,
         "dataType": ["INT", "STRING"][i % 2],
         "isPrimaryKey": i % 4 in (0, 2) and i < 4,                 # t1 y t3 tienen PK; t2 y t4 no
         "udpValues": {"u_num": ["5", "9007199254740992", "9007199254740993", "10.0"][i % 4],
                       "u_bool": ["Sí", "no", "yes", "true"][i % 4]}}
        for i in range(8)])
    return fake


@pytest.fixture
def raw() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _sql(raw, text: str):
    res = raw.post("/api/reporting/query/sql", json={"text": text, "projectId": "p1"})
    return res.status_code, (res.json()["data"] if res.status_code == 200 else res.json().get("detail"))


def _chain(n: int, op: str = "OR") -> str:
    return "SELECT physicalName FROM columns WHERE " + f" {op} ".join(["ordinal = 1"] * n)


# ── F1: cadena plana de OR/AND ───────────────────────────────────────────────
def test_r5_validate_con_300_condiciones_ni_500_ni_congela(qdb, raw):
    """Repro R12 (`test_f1_validate_con_300_condiciones_ni_500_ni_congela`)."""
    t0 = time.monotonic()
    res = raw.post("/api/reporting/query/validate", json={"text": _chain(300), "projectId": "p1"})
    assert res.status_code == 200 and time.monotonic() - t0 < 3, (res.status_code, res.text[:120])
    where = res.json()["data"]["spec"]["where"]
    assert where["op"] == "or" and len(where["conditions"]) == 300          # UN grupo de N, no un árbol


@pytest.mark.parametrize("n, op", [(1000, "AND"), (5000, "OR")])
def test_r5_sql_con_miles_de_condiciones_no_es_500(qdb, raw, n, op):
    """Repro R12 (`test_f1_sql_con_1000_condiciones_no_es_500`)."""
    status, data = _sql(raw, _chain(n, op))
    assert status == 200 and [r["physicalName"] for r in data["rows"]] == ["C1"], (status, str(data)[:120])
    assert not qdb.problems, qdb.problems[:2]


def test_r5_el_event_loop_no_se_congela(qdb):
    """Repro R12 (`test_f1_el_event_loop_no_se_congela`): un ticker cada 50 ms."""
    async def main() -> float:
        ticks: list[float] = []

        async def ticker():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.05)
        task = asyncio.create_task(ticker())
        await asyncio.sleep(0.2)
        await query_router.validate_sql(query_router.SqlBody(text=_chain(300), projectId="p1"))
        await asyncio.sleep(0.2)
        task.cancel()
        return max(b - a for a, b in zip(ticks, ticks[1:]))
    assert asyncio.run(main()) < 1.0


def test_r5_recursion_al_armar_el_spec_no_es_500(qdb, raw, monkeypatch):
    """Guarda del router: si armar el spec agotara la pila (hoy sqlglot corta
    antes, al parsear), es el 400 legible, no un 500."""
    def deep(*_a, **_k):
        raise RecursionError("maximum recursion depth exceeded")
    monkeypatch.setattr(query_router.parser, "to_spec", deep)
    status, detail = _sql(raw, "SELECT physicalName FROM columns")
    assert status == 400 and "nested too deeply" in detail, (status, detail)


@pytest.mark.parametrize("text", [
    "SELECT physicalName FROM columns WHERE " + "NOT " * 80 + "ordinal = 1",
    "SELECT physicalName FROM columns WHERE " + "(" * 400 + "ordinal = 1" + ")" * 400,
])
def test_r5_anidamiento_desmedido_es_400_legible(qdb, raw, text):
    status, detail = _sql(raw, text)
    assert status == 400 and "nested too deeply" in detail, (status, detail)
    val = raw.post("/api/reporting/query/validate", json={"text": text, "projectId": "p1"})
    assert val.status_code == 200 and "nested too deeply" in val.json()["data"]["errors"][0]["message"]


def test_r5_builder_con_filtro_desmedido_es_422_legible(qdb, raw):
    node = {"field": "physicalName", "op": "eq", "value": "C1"}
    for _ in range(80):
        node = {"op": "not", "conditions": [node]}
    res = raw.post("/api/reporting/query", json={"projectId": "p1", "from": "columns", "where": node})
    assert res.status_code == 422 and "nested too deeply" in str(res.json()["detail"]), res.text[:200]


@pytest.mark.parametrize("conditions", [5, 2.5, True])
def test_r5_builder_con_conditions_que_no_es_lista_es_422(qdb, raw, conditions):
    """El validador de profundidad corre ANTES que pydantic: un `conditions`
    que no es lista no debe romperlo (TypeError → 500)."""
    res = raw.post("/api/reporting/query", json={"projectId": "p1", "from": "columns",
                                                  "where": {"op": "and", "conditions": conditions}})
    assert res.status_code == 422, (res.status_code, res.text[:200])


# ── F2: ORDER BY de un MIN/MAX de booleano ───────────────────────────────────
def test_r5_orden_por_max_de_booleano_es_ordenable_en_lakebase(qdb, raw):
    """Repro R12 (`test_f2_…` y `test_lakebase_puede_ordenar_cada_clave_del_sort_agrupado`)."""
    text = ("SELECT tableId, MAX(isPrimaryKey) AS hasPk FROM columns GROUP BY tableId "
            "ORDER BY hasPk DESC, tableId LIMIT 2")
    status, data = _sql(raw, text)
    assert status == 200 and data["rows"] == [{"tableId": "t1", "hasPk": True}, {"tableId": "t3", "hasPk": True}]
    assert not qdb.problems, qdb.problems[:2]                      # el SQL de Lakebase se genera sin problemas
    spec = raw.post("/api/reporting/query/validate", json={"text": text, "projectId": "p1"}).json()["data"]["spec"]
    compiled = compile_spec(QuerySpec.model_validate(spec), build_catalog("columns", []))
    first_key = compiled.sort[0][0]
    assert first_key not in compiled.output and "$cond" in str(compiled.project[first_key])   # clave 0/1


# ── F3: UDP number con enteros grandes ───────────────────────────────────────
@pytest.mark.parametrize("text, values", [
    ('SELECT physicalName, udp."Num" FROM columns WHERE udp."Num" = 9007199254740993',
     ["9007199254740993", "9007199254740993"]),
    ('SELECT physicalName, udp."Num" FROM columns WHERE udp."Num" = 10.0', ["10.0", "10.0"]),     # tal como se guardó
    ('SELECT physicalName, udp."Num" FROM columns WHERE udp."Num" IN (5, 9007199254740992)',
     ["5", "5", "9007199254740992", "9007199254740992"]),
])
def test_r5_udp_numerico_entero_grande_y_texto_escrito(qdb, raw, text, values):
    """Repro R12 (`test_f3_udp_numerico_entero_grande_exacto`): …993 se buscaba como «…992»."""
    status, data = _sql(raw, text)
    assert status == 200 and sorted(r["udp.u_num"] for r in data["rows"]) == values, data


def test_r5_udp_numerico_desde_el_builder_texto_y_numero(qdb, raw):
    """El texto guardado «10.0» lo hallan «10.0» y 10.0 (float: su forma escrita y
    la entera); el entero 10 busca «10», como `udp."Num" = 10` en el editor. Un
    texto entero se canoniza EXACTO («+…993» → «…993»); un float desde 2^53 no
    es exacto y no se vuelve entero (…992.0 podría venir de …993)."""
    for value, expected in (("10.0", ["C3", "C7"]), (10.0, ["C3", "C7"]), (10, []),
                            ("9007199254740993", ["C2", "C6"]), (9007199254740993, ["C2", "C6"]),
                            ("+9007199254740993", ["C2", "C6"]), (9007199254740992.0, [])):
        spec = {"projectId": "p1", "from": "columns", "select": ["physicalName"],
                "where": {"op": "and", "conditions": [{"field": "udp.u_num", "op": "eq", "value": value}]}}
        rows = raw.post("/api/reporting/query", json=spec).json()["data"]["rows"]
        assert sorted(r["physicalName"] for r in rows) == expected, (value, rows)
    status, data = _sql(raw, 'SELECT physicalName FROM columns WHERE udp."Num" = 10')
    assert status == 200 and data["rows"] == [], data


def test_r5_udp_numerico_in_con_null_busca_tambien_la_forma_canonica(qdb, raw):
    """Builder: `in ["5.0", null]` caía al camino genérico por el null y sólo
    buscaba «5.0» (el texto guardado es «5»). El null sigue buscando vacíos."""
    qdb.raw["canonical_columns"].insert_one({"_id": "c8", **ON, "tableId": "t1", "physicalName": "C8",
                                             "ordinal": 8, "udpValues": {}})
    spec = {"projectId": "p1", "from": "columns", "select": ["physicalName"],
            "where": {"op": "and", "conditions": [{"field": "udp.u_num", "op": "in", "value": ["5.0", None]}]}}
    res = raw.post("/api/reporting/query", json=spec)
    assert res.status_code == 200, res.text[:200]
    assert sorted(r["physicalName"] for r in res.json()["data"]["rows"]) == ["C0", "C4", "C8"], res.json()


@pytest.mark.parametrize("text, detail", [
    ('SELECT physicalName FROM columns WHERE udp."Num" = \'abc\'', 'Invalid number for udp."Num": \'abc\''),
    ('SELECT physicalName FROM columns WHERE udp."Num" IN (5, \'NaN\')',
     'Invalid number for udp."Num": it must be a finite number.'),
])
def test_r5_udp_numerico_sigue_validando_el_numero(qdb, raw, text, detail):
    """El arreglo de F3 (buscar el texto escrito) no debe dejar pasar lo que no
    es un número: en la ronda 4 era 400 (`_udp_text` → `_number`)."""
    assert _sql(raw, text) == (400, detail)
    spec = {"projectId": "p1", "from": "columns", "select": ["physicalName"],
            "where": {"op": "and", "conditions": [{"field": "udp.u_num", "op": "ne", "value": "abc"}]}}
    res = raw.post("/api/reporting/query", json=spec)
    assert res.status_code == 400 and res.json()["detail"] == 'Invalid number for udp."Num": \'abc\''


# ── F4: UDP booleano con sus grafías ────────────────────────────────────────
@pytest.mark.parametrize("value, rows", [("'Sí'", ["C0", "C2", "C3", "C4", "C6", "C7"]),
                                         ("'yes'", ["C0", "C2", "C3", "C4", "C6", "C7"]),
                                         ("'no'", ["C1", "C5"]), ("'VERDADERO'", ["C0", "C2", "C3", "C4", "C6", "C7"])])
def test_r5_udp_booleano_acepta_sus_grafias(qdb, raw, value, rows):
    """Repro R12 (`test_f4_udp_booleano_acepta_sus_grafias`): era 400."""
    status, data = _sql(raw, f'SELECT physicalName FROM columns WHERE udp."Flag" = {value}')
    assert status == 200 and sorted(r["physicalName"] for r in data["rows"]) == rows, data


# ── F5: los mensajes nombran el UDP por su nombre ────────────────────────────
@pytest.mark.parametrize("text", [
    'SELECT physicalName FROM columns WHERE udp."Flag" = 2',
    'SELECT SUM(udp."Flag") AS s FROM columns',
    'SELECT physicalName FROM columns WHERE udp."Flag" > 1',
    'SELECT physicalName FROM columns ORDER BY udp."Flag"',
    'SELECT udp."Flag", COUNT(*) AS n FROM columns GROUP BY dataType',
])
def test_r5_mensajes_nombran_el_udp_por_su_nombre(qdb, raw, text):
    """Repro R12 (`test_f5_…`): mostraban `udp.u_bool` (en producción, un UUID)."""
    status, detail = _sql(raw, text)
    assert status in (400, 422) and "u_bool" not in detail and 'udp."Flag"' in detail, (status, detail)


# ── F6: texto desmedido con un `detail` de texto ─────────────────────────────
def test_r5_texto_desmedido_con_mensaje_legible(qdb, raw):
    """Repro R12 (`test_f6_…`): era la lista de pydantic («Unexpected response shape»)."""
    for route in ("/api/reporting/query/sql", "/api/reporting/query/validate"):
        res = raw.post(route, json={"text": _chain(7000), "projectId": "p1"})
        assert res.status_code == 422 and res.json()["detail"] == \
            "The SQL text is too long: up to 100,000 characters.", (route, res.text[:160])


# ── F7: ORDER BY de un agregado equivalente ──────────────────────────────────
@pytest.mark.parametrize("text", [
    "SELECT tableId, COUNT(1) AS n FROM columns GROUP BY tableId ORDER BY COUNT(*) DESC, tableId",
    "SELECT tableId, COUNT(*) AS n FROM columns GROUP BY tableId ORDER BY COUNT(1) DESC, tableId",
    "SELECT c.tableId, MAX(c.ordinal) AS n FROM columns c GROUP BY c.tableId ORDER BY MAX(ordinal) DESC, tableId",
])
def test_r5_order_by_agregado_equivalente(qdb, raw, text):
    """Repro R12 (`test_f7_…`): se comparaba el TEXTO del agregado."""
    status, data = _sql(raw, text)
    assert status == 200, data
    assert [r["tableId"] for r in data["rows"]][:2] == (["t1", "t2"] if "COUNT" in text else ["t4", "t3"])


# ── F8: un alias en otra caja no tapa el campo agrupado ──────────────────────
def test_r5_alias_en_otra_caja_es_un_nombre_repetido(qdb, raw):
    """Repro R12 (`test_f8_…`): `ORDER BY dataType` ordenaba por el COUNT."""
    status, detail = _sql(raw, "SELECT dataType, COUNT(*) AS DATATYPE FROM columns GROUP BY dataType "
                               "ORDER BY dataType")
    assert status == 400 and "is repeated" in detail, (status, detail)


def test_r5_order_by_prefiere_el_campo_exacto(qdb, raw):
    status, data = _sql(raw, "SELECT dataType, COUNT(*) AS n FROM columns WHERE ordinal <> 1 "
                             "GROUP BY dataType ORDER BY dataType DESC")
    assert status == 200 and [r["dataType"] for r in data["rows"]] == ["STRING", "INT"]


@pytest.mark.parametrize("text", [
    'SELECT physicalName FROM columns ORDER BY udp."ordinal"',
    'SELECT ordinal, udp."ordinal", COUNT(*) AS n FROM columns GROUP BY ordinal, udp."ordinal" '
    'ORDER BY udp."ordinal" DESC, ordinal',
])
def test_r5_order_by_udp_con_nombre_de_campo_es_el_udp(qdb, raw, text):
    """Un UDP que se llama como un campo (`udp."ordinal"`): el ORDER BY es el
    UDP, no el campo `ordinal` (la precedencia del campo exacto no mira el `udp.`)."""
    qdb.raw["udp_definitions"].insert_one({"_id": "u_ord", **ON, "name": "ordinal", "level": "column",
                                           "dataType": "string"})
    qdb.raw["canonical_columns"].update_many({}, [{"$set": {"udpValues.u_ord": "z"}}])
    qdb.raw["canonical_columns"].update_one({"_id": "c0"}, {"$set": {"udpValues.u_ord": "a"}})
    status, data = _sql(raw, text)
    if "GROUP BY" not in text:
        assert status in (400, 422) and 'udp."ordinal"' in data, (status, data)
        return
    assert status == 200, data
    assert [(r["ordinal"], r["udp.u_ord"]) for r in data["rows"]][:3] == [(1, "z"), (2, "z"), (3, "z")], data


# ── F9: view_columns con desempate único ─────────────────────────────────────
def test_r5_view_columns_desempate_unico(qdb, raw):
    """Repro R12 (`test_f9_…`): la misma columna de dos tablas, sin alias."""
    qdb.raw["views"].insert_one({"_id": "v1", **ON, "name": "V", "schema": "s_vu",
                                 "sources": [{"tableId": "t2", "column": "ID"}, {"tableId": "t1", "column": "ID"}]})
    got, cursor = [], None
    for _ in range(5):
        res = raw.post("/api/reporting/query" + (f"?cursor={cursor}" if cursor else ""),
                       json={"projectId": "p1", "from": "view_columns", "limit": 1,
                             "select": ["viewName", "outputName", "sourceTableId"]})
        got += [(r["_id"], r["sourceTableId"]) for r in res.json()["data"]["rows"]]
        cursor = res.json()["data"]["nextCursor"]
        if not cursor:
            break
    assert [t for _, t in got] == ["t1", "t2"] and len({i for i, _ in got}) == 2, got


# ── Agrupados: formas SQL que se soportan en vez de rechazarse ───────────────
def _csv(raw, text: str) -> list[list[str]]:
    spec = raw.post("/api/reporting/query/validate", json={"text": text, "projectId": "p1"}).json()["data"]["spec"]
    return list(csv.reader(io.StringIO(raw.post("/api/reporting/export", json=spec).text)))


@pytest.mark.parametrize("text, keys, rows", [
    ("SELECT COUNT(*) AS n, dataType FROM columns GROUP BY dataType ORDER BY dataType",
     ["n", "dataType"], [[4, "INT"], [4, "STRING"]]),
    ("SELECT COUNT(*) AS n FROM columns GROUP BY dataType ORDER BY dataType DESC",
     ["n"], [[4], [4]]),
    ("SELECT MAX(ordinal) AS m, tableId, COUNT(*) AS n FROM columns GROUP BY tableId, dataType ORDER BY m DESC",
     ["m", "tableId", "n"], [[7, "t4", 2], [6, "t3", 2], [5, "t2", 2], [4, "t1", 2]]),
])
def test_r5_agrupado_en_el_orden_del_select_y_dimension_oculta(qdb, raw, text, keys, rows):
    status, data = _sql(raw, text)
    assert status == 200, (status, data)
    got_keys = [c["key"] for c in data["columns"]]
    assert got_keys == keys and [[r[k] for k in keys] for r in data["rows"]] == rows, data
    assert all(set(r) == set(keys) for r in data["rows"]), data["rows"]            # la oculta no sale
    lines = _csv(raw, text)
    assert len(lines[0]) == len(keys) and [[c for c in line] for line in lines[1:]] == \
        [[str(v) for v in row] for row in rows]


def test_r5_result_columns_invalidas_son_422(qdb, raw):
    res = raw.post("/api/reporting/query", json={"projectId": "p1", "from": "columns", "groupBy": ["dataType"],
                                                  "aggregations": [{"fn": "count", "as": "n"}],
                                                  "resultColumns": ["n", "ordinal"]})
    assert res.status_code == 422 and "resultColumns" in res.json()["detail"], res.text[:200]
    res = raw.post("/api/reporting/query", json={"projectId": "p1", "from": "columns", "select": ["physicalName"],
                                                  "resultColumns": ["physicalName"]})
    assert res.status_code == 422 and res.json()["detail"] == "resultColumns only applies to grouped queries."
