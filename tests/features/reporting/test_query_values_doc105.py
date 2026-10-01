"""Doc 105 (revisión de los arreglos) — valores del motor de consulta del
Reporting que llegaban al SQL sin tipo (P12 incompleto).

- Hallazgo 2 (constructor): `_coerce` hacía `float(value)` y sólo atrapaba
  TypeError/ValueError — un entero JSON desmedido levantaba OverflowError (500
  en cualquier BD); "NaN", "Infinity", `1e999` (y `NaN` desnudo, que el JSON de
  Python acepta) pasaban como float y el traductor de Lakebase los escribe como
  literal jsonpath que Postgres rechaza (500 sólo en Lakebase: FakeDb los traga).
  Misma clase: una comparación SIN valor (el constructor manda `value` ausente al
  elegir campo u operador) llegaba como `{"$gt": null}` y el traductor de
  Lakebase no la soporta (NotImplementedError → 500).
- Hallazgo 2 (editor SQL): `int('1e3')`/`int('1e999')` reventaban (500 aun con
  FakeDb; `1e3` es un número legítimo), un literal de miles de dígitos pasa el
  tope de conversión de Python, y `LIMIT 1.5`/`'a'`/`NULL`, `-'x'`, `LIKE 5`,
  `WHERE 1 = ordinal`, `ORDER BY 1`, `SUM(1)` eran 500 por suponer la forma.
- Hallazgo 6: un cursor keyset con NUL (`\\u0000`) en el `_id` (o en el valor de
  orden) pasaba `_decode_cursor`: Postgres rechaza 0x00 en `text` → 500 en
  Lakebase."""
from __future__ import annotations

import base64
import json

import pytest
from starlette.testclient import TestClient

from app.core.db import client as db_client
from app.core.db.lakebase.translate import Sql, filter_sql
from app.features.reporting.query.compiler import QueryError, build_match
from app.features.reporting.query.executor import _decode_cursor
from app.features.reporting.query.schema import build_catalog
from app.features.reporting.query.spec import Condition
from app.main import app
from tests.support.fakedb import FakeDb

COLS = build_catalog("columns", [])
S = "SELECT physicalName FROM columns "


@pytest.fixture
def qdb(monkeypatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    fake.raw["canonical_columns"].insert_many([
        {"_id": "c1", "projectId": "p1", "flgactive": True, "tableId": "t1", "physicalName": "A", "ordinal": 1},
        {"_id": "c2", "projectId": "p1", "flgactive": True, "tableId": "t1", "physicalName": "B", "ordinal": 2000}])
    return fake


@pytest.fixture
def raw() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _where(*conditions: dict) -> dict:
    return {"projectId": "p1", "from": "columns", "select": ["physicalName"],
            "where": {"op": "and", "conditions": list(conditions)}}


# ── Hallazgo 2: constructor de consultas ─────────────────────────────────────
def test_h2_entero_desmedido_en_el_where_es_400(qdb, raw):
    """Repro del revisor R2 (`test_p12_where_con_entero_enorme_da_500`)."""
    body = ('{"projectId": "p1", "from": "columns", "where": {"op": "and", "conditions": ['
            '{"field": "ordinal", "op": "gt", "value": ' + "9" * 400 + '}]}}')
    res = raw.post("/api/reporting/query", content=body, headers={"content-type": "application/json"})
    assert res.status_code == 400, (res.status_code, res.text[:200])
    assert res.json()["detail"] == "Invalid number for ordinal: it must be a finite number."


@pytest.mark.parametrize("op, value", [
    ("gt", "NaN"), ("gt", "Infinity"), ("lt", "-inf"), ("gte", "1e999"), ("gt", float("nan")),
    ("eq", float("inf")), ("ne", "NaN"), ("in", [1, "Infinity"]), ("between", ["1", "NaN"]),
    ("lte", 10 ** 400)])
def test_h2_numero_no_finito_es_query_error_400(op, value):
    """Repro del revisor R2 (`test_p12_where_nan_o_infinito_llega_al_traductor`):
    antes llegaba al traductor como literal jsonpath `NaN`/`Infinity`."""
    with pytest.raises(QueryError) as exc:
        build_match(Condition(field="ordinal", op=op, value=value), COLS)
    assert exc.value.code == 400
    assert str(exc.value) == "Invalid number for ordinal: it must be a finite number."


@pytest.mark.parametrize("payload", [
    '{"field": "ordinal", "op": "gt", "value": "NaN"}',
    '{"field": "ordinal", "op": "gt", "value": NaN}',                 # NaN desnudo: el json de Python lo acepta
    '{"field": "ordinal", "op": "in", "value": [1, Infinity]}'])
def test_h2_no_finito_por_http_es_400(qdb, raw, payload):
    body = '{"projectId": "p1", "from": "columns", "where": {"op": "and", "conditions": [' + payload + ']}}'
    res = raw.post("/api/reporting/query", content=body, headers={"content-type": "application/json"})
    assert res.status_code == 400, (res.status_code, res.text[:200])


@pytest.mark.parametrize("op, value", [("gt", None), ("gte", None), ("lt", None), ("lte", None),
                                       ("between", [None, 5]), ("between", [1, None])])
def test_h2_comparacion_sin_valor_es_400(op, value):
    with pytest.raises(QueryError) as exc:
        build_match(Condition(field="ordinal", op=op, value=value), COLS)
    assert exc.value.code == 400
    assert str(exc.value) == f"The {op} filter on ordinal needs a value."


def test_h2_comparacion_sin_valor_por_http_es_400(qdb, raw):
    """El constructor manda `value` AUSENTE al elegir campo u operador."""
    res = raw.post("/api/reporting/query", json=_where({"field": "ordinal", "op": "gt"}))
    assert res.status_code == 400 and res.json()["detail"] == "The gt filter on ordinal needs a value."


def test_h2_la_premisa_el_traductor_no_soporta_comparar_con_null():
    """Por qué una comparación sin valor era 500 en Lakebase (FakeDb la traga)."""
    with pytest.raises(NotImplementedError):
        filter_sql({"ordinal": {"$gt": None}}, Sql())


@pytest.mark.parametrize("op, value, expected", [
    ("gt", "5", {"$gt": 5}), ("gt", 5, {"$gt": 5}), ("gte", 2.5, {"$gte": 2.5}), ("lt", "1e3", {"$lt": 1000}),
    ("lte", -3, {"$lte": -3}), ("gt", 10 ** 20, {"$gt": 10 ** 20}), ("gt", True, {"$gt": 1}),
    ("between", ["1", 9], {"$gte": 1, "$lte": 9}), ("ne", None, {"$ne": None})])
def test_h2_valores_legitimos_siguen_igual(op, value, expected):
    match = build_match(Condition(field="ordinal", op=op, value=value), COLS)
    assert match == {"ordinal": expected}
    filter_sql(match, Sql())                                           # y el traductor de Lakebase los acepta


def test_h2_igualdad_con_null_sigue_siendo_vacio_o_ausente():
    match = build_match(Condition(field="ordinal", op="eq", value=None), COLS)
    assert match == {"ordinal": None}
    filter_sql(match, Sql())


def test_h2_texto_no_numerico_sigue_con_su_mensaje():
    with pytest.raises(QueryError) as exc:
        build_match(Condition(field="ordinal", op="gt", value="abc"), COLS)
    assert exc.value.code == 400 and str(exc.value) == "Invalid number for ordinal: 'abc'"


@pytest.mark.parametrize("condition, detail", [
    ({"field": "ordinal", "op": "gt", "value": "NaN"}, "Invalid number for ordinal: it must be a finite number."),
    ({"field": "ordinal", "op": "gt"}, "The gt filter on ordinal needs a value."),
    ({"field": "nope", "op": "eq", "value": 1}, "Unknown field: 'nope'")])
def test_h2_export_csv_con_where_invalido_es_400_no_500(qdb, raw, condition, detail):
    """La TERCERA puerta del mismo WHERE: el export CSV compilaba dentro del
    generador del stream — un `QueryError` ahí era 500 (o un stream cortado) y
    el front espera un 4xx con `detail` para mostrar el motivo."""
    res = raw.post("/api/reporting/export", json=_where(condition))
    assert res.status_code == 400, (res.status_code, res.text[:200])
    assert res.json()["detail"] == detail


def test_h2_export_csv_legitimo_sigue_en_streaming(qdb, raw):
    res = raw.post("/api/reporting/export", json=_where({"field": "ordinal", "op": "gt", "value": "1e3"}))
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
    assert res.text.splitlines() == ["Physical name", "B"]


# ── Hallazgo 2: editor SQL ───────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    S + "WHERE ordinal > " + "9" * 400,                     # repro R2: OverflowError al compilar
    S + "WHERE ordinal > 1e999",                            # repro R2: inf → Infinity en el jsonpath
    S + "WHERE ordinal > -1e999",
    S + "WHERE ordinal IN (1e999, 2)",
    S + "WHERE ordinal > " + "9" * 5000,                    # pasa el tope de int() de Python
    S + "WHERE ordinal > 'NaN'",
    S + "WHERE ordinal > NULL",
    S + "WHERE ordinal = -NULL",
    S + "WHERE ordinal = -'x'",
    S + "WHERE ordinal = -TRUE",
    S + "WHERE physicalName LIKE 5",
    S + "WHERE 1 = ordinal",
    "SELECT SUM(1) FROM columns",
    S + "LIMIT 1.5",
    S + "LIMIT 'a'",
    S + "LIMIT NULL",
    S + "LIMIT physicalName",
    S + "LIMIT " + "9" * 5000,
])
def test_h2_editor_sql_responde_400_no_500(qdb, raw, text):
    res = raw.post("/api/reporting/query/sql", json={"text": text, "projectId": "p1"})
    assert res.status_code == 400, (res.status_code, res.text[:200])
    val = raw.post("/api/reporting/query/validate", json={"text": text, "projectId": "p1"})
    assert val.status_code == 200, (val.status_code, val.text[:200])


@pytest.mark.parametrize("text, rows", [
    (S + "WHERE ordinal > 1e3", ["B"]),                      # notación científica legítima (antes 500)
    (S + "WHERE ordinal IN (1e3, 2e3)", ["B"]),
    (S + "WHERE ordinal > 0.5 ORDER BY physicalName", ["A", "B"]),
    (S + "WHERE ordinal > -5 LIMIT 1e3", ["A", "B"]),
    (S + "LIMIT " + "9" * 30, ["A", "B"]),                  # sigue topado en 5000
])
def test_h2_editor_sql_numeros_legitimos(qdb, raw, text, rows):
    res = raw.post("/api/reporting/query/sql", json={"text": text, "projectId": "p1"})
    assert res.status_code == 200, (res.status_code, res.text[:200])
    assert sorted(r["physicalName"] for r in res.json()["data"]["rows"]) == rows


def test_h2_editor_sql_explica_el_numero_fuera_de_rango(qdb, raw):
    val = raw.post("/api/reporting/query/validate", json={"text": S + "WHERE ordinal > 1e999", "projectId": "p1"})
    assert val.json()["data"]["errors"][0]["message"] == "Number out of range: 1e999"
    lim = raw.post("/api/reporting/query/sql", json={"text": S + "LIMIT 1.5", "projectId": "p1"})
    assert lim.json()["detail"] == "LIMIT must be a whole number."


# ── Hallazgo 6: cursor con NUL ───────────────────────────────────────────────
def _b64(value) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()


@pytest.mark.parametrize("cursor", [["x", "a\u0000b"], ["\u0000", "t1"], [None, "\u0000"]])
def test_h6_cursor_con_nul_es_400(cursor):
    """Repro del revisor R2 (`test_p12_cursor_con_nul_pasa_la_validacion`)."""
    with pytest.raises(QueryError) as exc:
        _decode_cursor(_b64(cursor))
    assert exc.value.code == 400 and str(exc.value) == "Invalid cursor"


def test_h6_cursor_con_nul_por_http_es_400(qdb, raw):
    res = raw.post(f"/api/reporting/query?cursor={_b64(['A', 'c1' + chr(0)])}", json=_where())
    assert res.status_code == 400 and res.json()["detail"] == "Invalid cursor"


def test_h6_cursores_legitimos_siguen_pasando():
    for cur in (["A", "c1"], [None, "c1"], [3, "id-con-ñ"], ["texto con\ttab", "x"]):
        assert _decode_cursor(_b64(cur)) == cur
