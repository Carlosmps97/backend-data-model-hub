"""Doc 105 (ronda 3, revisor R5) — motor de consulta del Reporting.

1. `x NOT LIKE 'p'` se ejecutaba como LIKE (sqlglot 30 lo da como
   `Like(negate=True)` y el parser ignoraba `negate`): devolvía lo INVERTIDO.
2. Agrupar por un UDP daba la dimensión siempre en null: el `_id` del grupo
   guardaba la clave plana `udp.<defId>` y el `$project` la leía como ruta.
3. `FETCH FIRST n ROWS ONLY` → 500 (`_literal(None)`).
4. Un alias de agregación reservado (`_id`, `$x`, con punto, repetido o igual a
   un campo del groupBy) → 500 o columnas pisadas.
5. `IS TRUE`/`IS NOT TRUE` se traducían como `IS NULL`; `OFFSET`, `LIMIT a, b`
   y cláusulas fuera del allowlist (DISTINCT, HAVING, WITH…) se ignoraban.
6. `tablesWithoutPk` contaba como «con PK» columnas de tablas INACTIVAS.
7. Un surrogate UTF-16 suelto en el CURSOR pasaba (asyncpg no lo codifica)."""
from __future__ import annotations

import asyncio
import base64
import json

import pytest
from sqlglot import exp
from starlette.testclient import TestClient

from app.core.db import client as db_client
from app.features.reporting import views
from app.features.reporting.query.compiler import QueryError
from app.features.reporting.query.executor import _decode_cursor
from app.features.reporting.query.parser import SqlError, _condition, _literal
from app.features.reporting.query.schema import build_catalog
from app.main import app
from tests.support.fakedb import FakeDb

from .lakebase_sql_doc105 import LakebaseCheckingDb

S = "SELECT physicalName FROM columns "


@pytest.fixture
def qdb(monkeypatch) -> LakebaseCheckingDb:
    fake = LakebaseCheckingDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    fake.raw["udp_definitions"].insert_one(
        {"_id": "u_env", "projectId": "p1", "flgactive": True, "name": "Entorno", "level": "column",
         "dataType": "string"})
    fake.raw["canonical_columns"].insert_many([
        {"_id": f"c{i}", "projectId": "p1", "flgactive": True, "tableId": "t1", "physicalName": f"C{i}",
         "dataType": "INT" if i < 2 else "STRING", "ordinal": i, "isPrimaryKey": i == 0,
         "description": "texto" if i == 1 else None, "udpValues": {"u_env": "PROD" if i % 2 else "DEV"}}
        for i in range(4)])
    return fake


@pytest.fixture
def raw() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _sql(raw, text: str) -> tuple[int, object]:
    res = raw.post("/api/reporting/query/sql", json={"text": text, "projectId": "p1"})
    if res.status_code != 200:
        return res.status_code, res.json().get("detail")
    return 200, sorted(r["physicalName"] for r in res.json()["data"]["rows"])


def _validate_error(raw, text: str) -> str | None:
    res = raw.post("/api/reporting/query/validate", json={"text": text, "projectId": "p1"})
    assert res.status_code == 200, (res.status_code, res.text[:200])
    errors = res.json()["data"]["errors"]
    return errors[0]["message"] if errors else None


# ── 1: NOT LIKE ─────────────────────────────────────────────────────────────
def test_r3_not_like_no_se_ejecuta_como_like(qdb, raw):
    """Repro R5 (`test_not_like_se_ejecuta_como_like`): devolvía (200, ['C1'])."""
    assert _sql(raw, S + "WHERE NOT physicalName LIKE 'C1%'") == (200, ["C0", "C2", "C3"])     # control
    assert _sql(raw, S + "WHERE physicalName NOT LIKE 'C1%'") == (200, ["C0", "C2", "C3"])
    assert _sql(raw, S + "WHERE physicalName NOT LIKE '%1%' AND ordinal > 0") == (200, ["C2", "C3"])


def test_r3_ilike_negado_o_no_sigue_rechazado(qdb, raw):
    for text in (S + "WHERE physicalName ILIKE 'c1%'", S + "WHERE physicalName NOT ILIKE 'c1%'"):
        assert _sql(raw, text)[0] == 400


def test_r3_un_negate_no_contemplado_es_sql_error():
    """Guarda genérica: cualquier nodo con `negate` que el parser no traduce."""
    cat = build_catalog("columns", [])
    node = exp.EQ(this=exp.column("ordinal"), expression=exp.Literal.number(1), negate=True)
    with pytest.raises(SqlError):
        _condition(node, cat)


# ── 2: agrupar por un UDP ────────────────────────────────────────────────────
def test_r3_agrupar_por_un_udp_trae_la_dimension(qdb, raw):
    """Repro R5 (`test_agrupar_por_un_udp_devuelve_la_dimension_en_null`)."""
    spec = {"projectId": "p1", "from": "columns", "groupBy": ["udp.u_env", "dataType"],
            "aggregations": [{"fn": "count", "as": "n"}], "orderBy": [{"field": "udp.u_env", "dir": "desc"}]}
    res = raw.post("/api/reporting/query", json=spec)
    assert res.status_code == 200, res.text[:200]
    rows = res.json()["data"]["rows"]
    assert sorted((r["udp.u_env"], r["dataType"], r["n"]) for r in rows) == [
        ("DEV", "INT", 1), ("DEV", "STRING", 1), ("PROD", "INT", 1), ("PROD", "STRING", 1)]
    assert [r["udp.u_env"] for r in rows] == ["PROD", "PROD", "DEV", "DEV"]      # ORDER BY la dimensión UDP
    assert not qdb.problems, qdb.problems


# ── 3: FETCH FIRST ───────────────────────────────────────────────────────────
def test_r3_fetch_first_es_400_no_500(qdb, raw):
    """Repro R5 (`test_fetch_first_es_500`)."""
    text = S + "FETCH FIRST 2 ROWS ONLY"
    assert _sql(raw, text) == (400, "FETCH isn't supported: use LIMIT n.")
    assert _validate_error(raw, text) == "FETCH isn't supported: use LIMIT n."


def test_r3_literal_ausente_es_sql_error():
    with pytest.raises(SqlError):
        _literal(None)


# ── 4: alias de agregación ───────────────────────────────────────────────────
@pytest.mark.parametrize("alias", ["_id", "$x", "$", "a.b", "", " ", "1n", "x" * 65, "dataType"])
def test_r3_alias_invalido_es_422(qdb, raw, alias):
    """Repro R5 (`test_alias_reservado_de_una_agregacion`): 500 o columnas pisadas."""
    spec = {"projectId": "p1", "from": "columns", "groupBy": ["dataType"],
            "aggregations": [{"fn": "count", "as": alias}]}
    res = raw.post("/api/reporting/query", json=spec)
    assert res.status_code == 422, (res.status_code, res.text[:200], qdb.problems)
    assert "aggregation name" in res.json()["detail"]


def test_r3_alias_repetido_es_422(qdb, raw):
    spec = {"projectId": "p1", "from": "columns", "groupBy": ["dataType"],
            "aggregations": [{"fn": "count", "as": "n"}, {"fn": "max", "field": "ordinal", "as": "n"}]}
    res = raw.post("/api/reporting/query", json=spec)
    assert res.status_code == 422 and "aggregation name" in res.json()["detail"]


def test_r3_alias_id_desde_el_editor_sql(qdb, raw):
    """Repro R5 (`test_alias_id_desde_el_editor_sql`)."""
    text = "SELECT dataType, COUNT(*) AS _id FROM columns GROUP BY dataType"
    assert _sql(raw, text)[0] == 400
    assert "aggregation name" in (_validate_error(raw, text) or "")


def test_r3_alias_validos_siguen(qdb, raw):
    spec = {"projectId": "p1", "from": "columns", "groupBy": ["dataType"],
            "aggregations": [{"fn": "count", "as": "count"}, {"fn": "max", "field": "ordinal", "as": "máximo"}],
            "orderBy": [{"field": "máximo", "dir": "desc"}]}
    res = raw.post("/api/reporting/query", json=spec)
    assert res.status_code == 200, res.text[:200]
    assert res.json()["data"]["rows"] == [{"dataType": "STRING", "count": 2, "máximo": 3},
                                          {"dataType": "INT", "count": 2, "máximo": 1}]
    assert not qdb.problems, qdb.problems


# ── 5: IS TRUE, OFFSET y cláusulas fuera del allowlist ───────────────────────
@pytest.mark.parametrize("text", [
    S + "WHERE description IS TRUE",
    S + "WHERE description IS NOT TRUE",                 # repro R5: devolvía los NULOS
    S + "WHERE isPrimaryKey IS FALSE",
    S + "WHERE ordinal >= 0 ORDER BY physicalName LIMIT 2 OFFSET 2",   # repro R5: ignoraba el OFFSET
    S + "OFFSET 1",
    S + "LIMIT 1, 2",
    "SELECT DISTINCT physicalName FROM columns",
    "SELECT dataType, COUNT(*) AS n FROM columns GROUP BY dataType HAVING COUNT(*) > 1",
    S + "QUALIFY ordinal > 1",
    "WITH x AS (SELECT physicalName FROM columns) SELECT physicalName FROM x",
    "SELECT dataType, COUNT(*) AS n FROM columns GROUP BY ROLLUP (dataType)",
    "SELECT COUNT(*) AS n FROM columns GROUP BY ALL",                # se volvía un conteo global
    "SELECT dataType, COUNT(*) AS n FROM columns GROUP BY dataType WITH ROLLUP",
    "SELECT dataType, COUNT(*) AS n FROM columns GROUP BY dataType WITH TOTALS",
    S + "WINDOW w AS (PARTITION BY dataType)",
    S + "FOR UPDATE",
    "SELECT physicalName INTO t FROM columns",
    S + "SORT BY physicalName",
    S + "LIMIT 5 PERCENT",
    S + "ORDER BY physicalName NULLS LAST",
    S + "ORDER BY physicalName DESC NULLS FIRST",
    "SELECT physicalName FROM dmh.columns",
    S + "TABLESAMPLE (10 PERCENT)",
])
def test_r3_lo_no_soportado_es_400(qdb, raw, text):
    status, detail = _sql(raw, text)
    assert status == 400, (status, detail)
    assert _validate_error(raw, text), text


@pytest.mark.parametrize("text, rows", [
    (S + "WHERE description IS NULL", ["C0", "C2", "C3"]),
    (S + "WHERE description IS NOT NULL", ["C1"]),
    (S + "ORDER BY physicalName ASC NULLS FIRST LIMIT 2", ["C0", "C1"]),     # el orden que ya aplicamos
    (S + "ORDER BY physicalName DESC NULLS LAST LIMIT 2", ["C2", "C3"]),
    ("SELECT c.physicalName FROM columns AS c WHERE c.ordinal > 2", ["C3"]),
])
def test_r3_lo_soportado_sigue(qdb, raw, text, rows):
    assert _sql(raw, text) == (200, rows)
    assert not qdb.problems, qdb.problems


# ── 6: tablesWithoutPk ───────────────────────────────────────────────────────
def test_r3_tablas_sin_pk_no_cuenta_columnas_de_tablas_inactivas(monkeypatch):
    """Repro R5 (`test_tablas_sin_pk_no_cuenta_columnas_de_tablas_inactivas`): daba 1."""
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    on = {"projectId": "p1", "flgactive": True}
    fake.raw["canonical_tables"].insert_many([
        {"_id": "t1", **on, "physicalName": "A"}, {"_id": "t2", **on, "physicalName": "B"},
        {"_id": "t3", **on, "physicalName": "C"},
        {"_id": "t9", "projectId": "p1", "flgactive": False, "physicalName": "BORRADA"}])
    fake.raw["canonical_columns"].insert_many([
        {"_id": "c1", **on, "tableId": "t1", "isPrimaryKey": False},
        {"_id": "c2", **on, "tableId": "t2", "isPrimaryKey": False},
        {"_id": "c3", **on, "tableId": "t3", "isPrimaryKey": True},
        {"_id": "c9", **on, "tableId": "t9", "isPrimaryKey": True}])     # huérfana (tabla borrada)
    card = asyncio.run(views.scorecard("p1"))
    assert (card["tables"], card["tablesWithoutPk"]) == (3, 2)


# ── 7: surrogate suelto en el cursor ─────────────────────────────────────────
@pytest.mark.parametrize("raw_json", [b'["\\ud800", "c1"]', b'["A", "c\\udfff"]', b'[null, "\\udc00x"]'])
def test_r3_cursor_con_surrogate_suelto_es_400(raw_json):
    """Repro R5 (`test_cursor_con_surrogate_suelto_pasa_la_validacion`)."""
    with pytest.raises(QueryError) as exc:
        _decode_cursor(base64.urlsafe_b64encode(raw_json).decode())
    assert exc.value.code == 400 and str(exc.value) == "Invalid cursor"


def test_r3_cursor_con_surrogate_por_http_es_400(qdb, raw):
    cur = base64.urlsafe_b64encode(b'["\\ud800", "c1"]').decode()
    res = raw.post(f"/api/reporting/query?cursor={cur}", json={"projectId": "p1", "from": "columns"})
    assert res.status_code == 400 and res.json()["detail"] == "Invalid cursor"


def test_r3_cursor_con_texto_valido_sigue():
    for cur in (["ñandú 😀", "c1"], ["A", "id-😀"]):
        assert _decode_cursor(base64.urlsafe_b64encode(json.dumps(cur).encode()).decode()) == cur


# ── Hallados por el fuzz propio (SQLite de oráculo): se ignoraban en silencio ──
@pytest.mark.parametrize("text, message", [
    (S + "WHERE physicalName LIKE '%1'", "LIKE '%text' (ends with) isn't supported: use '%text%' or 'text%'."),
    (S + "WHERE physicalName LIKE 'C%1'", "LIKE supports 'text%', '%text%' or 'text' — not '%' inside the text."),
    (S + "WHERE ordinal = NULL", "Compare with NULL using IS NULL or IS NOT NULL."),
    (S + "WHERE ordinal <> NULL", "Compare with NULL using IS NULL or IS NOT NULL."),
    (S + "WHERE ordinal IN (NULL, 1)", "Compare with NULL using IS NULL or IS NOT NULL."),
    (S + "LIMIT 0", "LIMIT must be at least 1."),
    (S + "LIMIT -1", "LIMIT must be at least 1."),
    ("SELECT physicalName AS nombre FROM columns",
     "Column aliases aren't supported (only on aggregations): physicalName AS nombre"),
    ("SELECT COUNT(physicalName) AS n FROM columns", "COUNT(field) isn't supported: use COUNT(*) or COUNT(DISTINCT field)."),
    ("SELECT SUM(DISTINCT ordinal) AS s FROM columns", "DISTINCT is only supported as COUNT(DISTINCT field)."),
    ("SELECT physicalName, COUNT(*) AS n FROM columns GROUP BY dataType",
     "Field 'physicalName' must be in GROUP BY (or inside an aggregation)."),
    ("SELECT *, COUNT(*) AS n FROM columns", "SELECT * can't be combined with GROUP BY or aggregations."),
    ("SELECT dataType, COUNT(*) AS n FROM columns GROUP BY dataType ORDER BY ordinal",
     "Can't sort by 'ordinal': in a grouped query sort by a GROUP BY field or an aggregation name."),
    (S + "ORDER BY physicalName, ordinal", "Sort by one field: rows are paged by a single sort field."),
    ("SELECT COUNT(*) AS n, COUNT(*) AS n FROM columns", "The aggregation name 'n' is repeated"),
])
def test_r3_fuzz_lo_que_se_ignoraba_es_400_con_motivo(qdb, raw, text, message):
    status, detail = _sql(raw, text)
    assert status == 400 and detail.startswith(message), (status, detail)
    assert (_validate_error(raw, text) or "").startswith(message)


def test_r3_fuzz_count_distinct_cuenta_valores_distintos(qdb, raw):
    """COUNT(DISTINCT x) contaba FILAS: ahora es `countDistinct`."""
    res = raw.post("/api/reporting/query/sql",
                   json={"text": "SELECT COUNT(DISTINCT dataType) AS tipos, COUNT(*) AS n FROM columns", "projectId": "p1"})
    assert res.status_code == 200 and res.json()["data"]["rows"] == [{"tipos": 2, "n": 4}]


def test_r3_fuzz_agregado_global_sin_filas_da_una_fila(qdb, raw):
    """Como SQL: conteos en 0 y el resto vacío (el `$group` no devolvía grupo)."""
    text = ("SELECT COUNT(*) AS n, COUNT(DISTINCT dataType) AS d, MAX(ordinal) AS hi FROM columns "
            "WHERE physicalName = 'NO_EXISTE'")
    res = raw.post("/api/reporting/query/sql", json={"text": text, "projectId": "p1"})
    assert res.status_code == 200 and res.json()["data"]["rows"] == [{"n": 0, "d": 0, "hi": None}]
    grouped = raw.post("/api/reporting/query/sql", json={
        "text": "SELECT dataType, COUNT(*) AS n FROM columns WHERE physicalName = 'NO_EXISTE' GROUP BY dataType",
        "projectId": "p1"})
    assert grouped.status_code == 200 and grouped.json()["data"]["rows"] == []   # con GROUP BY: sin grupos


def test_r3_fuzz_estrella_con_un_campo_trae_todos(qdb, raw):
    """`SELECT *, x` devolvía sólo `x` (el `*` se perdía)."""
    res = raw.post("/api/reporting/query/sql", json={"text": "SELECT *, physicalName FROM columns", "projectId": "p1"})
    keys = [c["key"] for c in res.json()["data"]["columns"]]
    assert res.status_code == 200 and {"physicalName", "logicalName", "dataType", "ordinal"} <= set(keys)


@pytest.mark.parametrize("value", [2, "yes", "si", -1, 0.5, [True], {"a": 1}])
def test_r3_fuzz_booleano_no_reconocible_es_400(qdb, raw, value):
    """`isPrimaryKey = 2` se volvía `false` y devolvía los registros SIN PK."""
    spec = {"projectId": "p1", "from": "columns",
            "where": {"op": "and", "conditions": [{"field": "isPrimaryKey", "op": "eq", "value": value}]}}
    res = raw.post("/api/reporting/query", json=spec)
    assert res.status_code == 400 and res.json()["detail"].startswith("Invalid boolean for isPrimaryKey")


@pytest.mark.parametrize("value, rows", [(True, ["C0"]), ("true", ["C0"]), (1, ["C0"]), ("0", ["C1", "C2", "C3"]),
                                         (False, ["C1", "C2", "C3"])])
def test_r3_fuzz_booleanos_legitimos_siguen(qdb, raw, value, rows):
    spec = {"projectId": "p1", "from": "columns", "select": ["physicalName"],
            "where": {"op": "and", "conditions": [{"field": "isPrimaryKey", "op": "eq", "value": value}]}}
    res = raw.post("/api/reporting/query", json=spec)
    assert res.status_code == 200 and sorted(r["physicalName"] for r in res.json()["data"]["rows"]) == rows


@pytest.mark.parametrize("spec, detail", [
    ({"groupBy": ["dataType"], "aggregations": [{"fn": "count", "field": "ordinal", "as": "n"}]},
     "count counts rows and takes no field: use countDistinct to count distinct values."),
    ({"orderBy": [{"field": "physicalName"}, {"field": "physicalName", "dir": "desc"}]},
     "Sort by one field: rows are paged by a single sort field."),
    ({"from": "view_columns", "select": ["viewName", "nope"]}, "Unknown field: 'nope'"),
    ({"from": "view_columns", "orderBy": [{"field": "nope"}]}, "Unknown field: 'nope'"),
    ({"from": "view_columns", "orderBy": [{"field": "viewName"}, {"field": "schema"}]}, "Sort by one field."),
])
def test_r3_fuzz_constructor_lo_que_se_ignoraba_es_4xx(qdb, raw, spec, detail):
    res = raw.post("/api/reporting/query", json={"projectId": "p1", "from": "columns", **spec})
    assert res.status_code in (400, 422) and res.json()["detail"] == detail, (res.status_code, res.text[:200])


def test_r3_fuzz_constructor_orden_fuera_del_grupo_avisa(qdb, raw):
    """El Builder conserva el orden de antes de agrupar: se ignora (como antes)
    pero ya NO en silencio — `meta.warnings` (el front lo muestra como aviso)."""
    spec = {"projectId": "p1", "from": "columns", "groupBy": ["dataType"],
            "aggregations": [{"fn": "count", "as": "n"}], "orderBy": [{"field": "physicalName", "dir": "desc"}]}
    res = raw.post("/api/reporting/query", json=spec)
    assert res.status_code == 200, res.text[:200]
    data = res.json()["data"]
    assert sorted((r["dataType"], r["n"]) for r in data["rows"]) == [("INT", 2), ("STRING", 2)]
    assert data["meta"]["warnings"] == ["Sort by 'physicalName' was ignored: in a grouped query sort by a "
                                        "grouped field or an aggregation name."]
