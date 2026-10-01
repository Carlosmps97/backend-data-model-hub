"""Doc 105 (cierre del coordinador) — en el editor SQL, `LIMIT n` es el tope
TOTAL del resultado (como en SQL), no sólo el tamaño de la primera página.

Antes: `… LIMIT 10` mostraba 10 filas y un cursor; «Load more» y el Export CSV
seguían más allá (50 000 filas). Ahora el parser arma `maxRows = n`; las
páginas siguientes lo respetan (el cursor lleva cuántas filas se entregaron y
no hay cursor siguiente al llegar al tope) y `/export` también. El constructor
sigue usando `limit` como tamaño de página (sin `maxRows`: todo el resultado).

Y `countDistinct` (COUNT(DISTINCT x)) contaba el vacío como un valor más: ahora
ignora lo vacío (nulo, ausente o texto vacío), como SQL y como IS NULL del motor."""
from __future__ import annotations

import base64
import json

import pytest
from starlette.testclient import TestClient

from app.core.db import client as db_client
from app.features.reporting.query.compiler import QueryError
from app.features.reporting.query.executor import _decode_cursor
from app.main import app

from .lakebase_sql_doc105 import LakebaseCheckingDb

NAMES = [f"C{i:02d}" for i in range(12)]


@pytest.fixture
def qdb(monkeypatch) -> LakebaseCheckingDb:
    fake = LakebaseCheckingDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    desc = [None, "A", "", "B", "A", None, "B", "C", "", "A", None, "C"]      # 3 distintos no vacíos
    fake.raw["canonical_columns"].insert_many([
        {"_id": f"c{i:02d}", "projectId": "p1", "flgactive": True, "tableId": f"t{i % 3}", "physicalName": n,
         "dataType": "INT" if i % 2 else "STRING", "ordinal": i, **({"description": desc[i]} if i != 5 else {})}
        for i, n in enumerate(NAMES)])
    fake.raw["views"].insert_many([
        {"_id": f"v{i}", "projectId": "p1", "flgactive": True, "name": f"V{i}", "schema": "s_vu",
         "sources": [{"tableId": "t0", "column": f"K{j}", "outputAlias": f"K{j}"} for j in range(3)]} for i in range(3)])
    return fake


@pytest.fixture
def raw() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _all_pages(raw, path: str, body: dict) -> tuple[list[dict], int]:
    """Filas de TODAS las páginas siguiendo `nextCursor` (como «Load more»)."""
    rows, pages, cursor = [], 0, None
    while True:
        res = raw.post(path + (f"?cursor={cursor}" if cursor else ""), json=body)
        assert res.status_code == 200, res.text[:200]
        data = res.json()["data"]
        rows += data["rows"]
        pages += 1
        cursor = data["nextCursor"]
        assert data["hasMore"] == (cursor is not None)
        if not cursor or pages > 20:
            return rows, pages


def _sql(text: str) -> dict:
    return {"text": text, "projectId": "p1"}


# ── LIMIT = tope total ───────────────────────────────────────────────────────
def test_sql_limit_es_el_total_load_more_no_sigue(qdb, raw):
    rows, pages = _all_pages(raw, "/api/reporting/query/sql",
                             _sql("SELECT physicalName FROM columns ORDER BY physicalName LIMIT 3"))
    assert [r["physicalName"] for r in rows] == NAMES[:3] and pages == 1


def test_sql_sin_limit_trae_todo_por_paginas(qdb, raw):
    rows, _ = _all_pages(raw, "/api/reporting/query/sql", _sql("SELECT physicalName FROM columns"))
    assert [r["physicalName"] for r in rows] == NAMES


def test_el_parser_arma_max_rows(qdb, raw):
    spec = raw.post("/api/reporting/query/validate", json=_sql("SELECT physicalName FROM columns LIMIT 7")).json()
    assert (spec["data"]["spec"]["limit"], spec["data"]["spec"]["maxRows"]) == (7, 7)
    none = raw.post("/api/reporting/query/validate", json=_sql("SELECT physicalName FROM columns")).json()
    assert none["data"]["spec"]["maxRows"] is None


@pytest.mark.parametrize("page, total, expected_pages", [(2, 5, 3), (4, 8, 2), (5, 5, 1), (5, 100, 3)])
def test_max_rows_se_respeta_entre_paginas(qdb, raw, page, total, expected_pages):
    """Tope mayor que la página (en SQL, `LIMIT` > 5000): páginas hasta el tope."""
    body = {"projectId": "p1", "from": "columns", "select": ["physicalName"], "limit": page, "maxRows": total}
    rows, pages = _all_pages(raw, "/api/reporting/query", body)
    assert [r["physicalName"] for r in rows] == NAMES[:min(total, 12)] and pages == expected_pages


def test_el_constructor_sin_max_rows_pagina_todo(qdb, raw):
    rows, pages = _all_pages(raw, "/api/reporting/query", {"projectId": "p1", "from": "columns",
                                                           "select": ["physicalName"], "limit": 5})
    assert [r["physicalName"] for r in rows] == NAMES and pages == 3


def test_view_columns_respeta_max_rows(qdb, raw):
    body = {"projectId": "p1", "from": "view_columns", "select": ["viewName", "outputName"], "limit": 2, "maxRows": 5}
    rows, pages = _all_pages(raw, "/api/reporting/query", body)
    assert len(rows) == 5 and pages == 3


# ── Export ───────────────────────────────────────────────────────────────────
def _csv_rows(res) -> list[str]:
    assert res.status_code == 200, res.text[:200]
    return res.text.splitlines()[1:]


def test_export_del_sql_respeta_el_limit(qdb, raw):
    """El flujo del front: `validateSql` → `{...spec}` → `/export` (opaco)."""
    spec = raw.post("/api/reporting/query/validate",
                    json=_sql("SELECT physicalName FROM columns ORDER BY physicalName LIMIT 4")).json()["data"]["spec"]
    assert _csv_rows(raw.post("/api/reporting/export", json={**spec, "projectId": "p1"})) == NAMES[:4]


def test_export_agrupado_del_sql_respeta_el_limit(qdb, raw):
    spec = raw.post("/api/reporting/query/validate", json=_sql(
        "SELECT tableId, COUNT(*) AS n FROM columns GROUP BY tableId ORDER BY tableId LIMIT 2")).json()["data"]["spec"]
    assert _csv_rows(raw.post("/api/reporting/export", json=spec)) == ["t0,4", "t1,4"]


def test_export_sin_limit_sigue_exportando_todo(qdb, raw):
    spec = raw.post("/api/reporting/query/validate", json=_sql("SELECT physicalName FROM columns")).json()["data"]["spec"]
    assert _csv_rows(raw.post("/api/reporting/export", json=spec)) == NAMES


# ── Agrupado: el tope y el aviso ─────────────────────────────────────────────
def test_agrupado_sql_limit_es_el_total(qdb, raw):
    res = raw.post("/api/reporting/query/sql", json=_sql(
        "SELECT physicalName, COUNT(*) AS n FROM columns GROUP BY physicalName ORDER BY physicalName LIMIT 3"))
    data = res.json()["data"]
    assert [r["physicalName"] for r in data["rows"]] == NAMES[:3] and data["meta"]["warnings"] == []


def test_agrupado_truncado_por_la_pagina_avisa(qdb, raw):
    """Sin `maxRows` (constructor) el agrupado devuelve a lo más `limit` grupos
    y no pagina: si hay más, ahora lo AVISA (antes cortaba en silencio)."""
    res = raw.post("/api/reporting/query", json={"projectId": "p1", "from": "columns", "groupBy": ["physicalName"],
                                                  "aggregations": [{"fn": "count", "as": "n"}], "limit": 5})
    data = res.json()["data"]
    assert len(data["rows"]) == 5
    assert data["meta"]["warnings"] == ["Only the first 5 groups are shown: filter or group by fewer values."]


# ── Cursor ───────────────────────────────────────────────────────────────────
def _b64(value) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()


@pytest.mark.parametrize("cursor", [["A", "c1", -1], ["A", "c1", True], ["A", "c1", 1.5], ["A", "c1", "3"],
                                    ["A", "c1", 10 ** 12], ["A", "c1", 3, 4]])
def test_cursor_con_conteo_invalido_es_400(cursor):
    with pytest.raises(QueryError) as exc:
        _decode_cursor(_b64(cursor))
    assert exc.value.code == 400


def test_cursor_con_conteo_valido_y_el_viejo_siguen():
    assert _decode_cursor(_b64(["A", "c1", 3])) == ["A", "c1", 3]
    assert _decode_cursor(_b64(["A", "c1"])) == ["A", "c1"]


# ── countDistinct ignora lo vacío ────────────────────────────────────────────
def test_count_distinct_sql_ignora_vacios(qdb, raw):
    res = raw.post("/api/reporting/query/sql", json=_sql("SELECT COUNT(DISTINCT description) AS d FROM columns"))
    assert res.status_code == 200 and res.json()["data"]["rows"] == [{"d": 3}]      # A, B, C
    assert not qdb.problems, qdb.problems


def test_count_distinct_constructor_coincide_con_sql(qdb, raw):
    spec = {"projectId": "p1", "from": "columns", "groupBy": ["tableId"],
            "aggregations": [{"fn": "countDistinct", "field": "description", "as": "d"}],
            "orderBy": [{"field": "d", "dir": "desc"}]}
    res = raw.post("/api/reporting/query", json=spec)
    rows = res.json()["data"]["rows"]
    sql = raw.post("/api/reporting/query/sql", json=_sql(
        "SELECT tableId, COUNT(DISTINCT description) AS d FROM columns GROUP BY tableId ORDER BY d DESC")).json()
    # t0: None, B, B, A → 2 · t1: A, A, C, None → 2 · t2: '', (ausente), '', C → 1
    assert sorted((r["tableId"], r["d"]) for r in rows) == [("t0", 2), ("t1", 2), ("t2", 1)]
    assert sql["data"]["rows"] == rows
    assert not qdb.problems, qdb.problems


@pytest.mark.parametrize("values, expected", [([None, "", "A", "B"], 2), (["A"], 1), ([], 0), ([""], 0),
                                              ([None], 0), (["", "A"], 1), ([0, False, "x"], 3)])
def test_la_expresion_de_count_distinct_descuenta_nulo_y_texto_vacio(values, expected):
    """La expresión sobre el conjunto YA armado: en Lakebase el `$addToSet` trae
    el `null` guardado y el texto vacío (mongomock no guarda el ''), así que se
    prueba aparte — y que el traductor de Lakebase la compila."""
    import mongomock

    from app.core.db.lakebase.aggregate import compile_pipeline
    from app.core.db.lakebase.translate import Sql
    from app.features.reporting.query.compiler import _distinct_count

    coll = mongomock.MongoClient()["x"]["y"]
    coll.insert_one({"_id": 1, "a0": values})
    pipe = [{"$project": {"_id": 0, "n": _distinct_count("a0")}}]
    assert list(coll.aggregate(pipe)) == [{"n": expected}]
    compile_pipeline(pipe, "t", Sql())


def test_un_cursor_ya_en_el_tope_da_pagina_vacia(qdb, raw):
    """El servidor no emite ese cursor; uno armado a mano no pasa del tope."""
    body = {"projectId": "p1", "from": "columns", "select": ["physicalName"], "limit": 2, "maxRows": 4}
    res = raw.post(f"/api/reporting/query?cursor={_b64(['C01', 'c01', 4])}", json=body)
    data = res.json()["data"]
    assert res.status_code == 200 and (data["rows"], data["nextCursor"], data["hasMore"]) == ([], None, False)
    vc = raw.post(f"/api/reporting/query?cursor={base64.urlsafe_b64encode(b'5').decode()}",
                  json={"projectId": "p1", "from": "view_columns", "limit": 2, "maxRows": 5})
    assert vc.status_code == 200 and (vc.json()["data"]["rows"], vc.json()["data"]["nextCursor"]) == ([], None)
