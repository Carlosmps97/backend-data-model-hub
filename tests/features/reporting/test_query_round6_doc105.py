"""Doc 105 (ronda 6, revisor R16) — motor de consulta del Reporting.

Base: `R16/test_r16_reporting.py` (`test_h1_*`, `test_h3_*`), adaptados.
- H1: el nombre AUTOMÁTICO de un agregado sin alias (`count`, `count_2`…)
  debe saltar un alias explícito que difiere sólo en mayúsculas (desde la ronda
  5 los nombres se comparan sin distinguirlas): era un 400 «is repeated».
- H3: `resultColumns` sin agrupar es 422 también en `view_columns` (se
  ignoraba en silencio, y `/query/validate` —`check_spec`— no lo marcaba)."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.core.db import client as db_client
from app.features.reporting.query import executor as ex
from app.features.reporting.query.compiler import QueryError
from app.features.reporting.query.schema import build_catalog
from app.features.reporting.query.spec import QuerySpec
from app.main import app

from .lakebase_sql_doc105 import LakebaseCheckingDb

ON = {"projectId": "p1", "flgactive": True}


@pytest.fixture
def qdb(monkeypatch) -> LakebaseCheckingDb:
    fake = LakebaseCheckingDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    raw = fake.raw
    raw["canonical_tables"].insert_many([{"_id": f"t{i}", **ON, "physicalName": f"T{i}"} for i in range(1, 5)])
    raw["canonical_columns"].insert_many([
        {"_id": f"c{i}", **ON, "tableId": f"t{1 + i % 4}", "physicalName": f"C{i}", "ordinal": i,
         "dataType": ["INT", "STRING"][i % 2]}
        for i in range(8)])
    raw["views"].insert_one({"_id": "v1", **ON, "name": "V", "schema": "s_vu",
                             "sources": [{"tableId": "t1", "column": "ID"}]})
    return fake


@pytest.fixture
def raw() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _sql(raw, text: str):
    res = raw.post("/api/reporting/query/sql", json={"text": text, "projectId": "p1"})
    return res.status_code, (res.json()["data"] if res.status_code == 200 else res.json().get("detail"))


# ── H1: el autonombre salta un alias que difiere sólo en mayúsculas ─────────
@pytest.mark.parametrize("text, keys, rows", [
    ("SELECT COUNT(*) AS Count, COUNT(DISTINCT tableId) FROM columns",
     ["Count", "count_2"], [[8, 4]]),
    ("SELECT dataType, MAX(ordinal) AS Max, MAX(physicalName) FROM columns GROUP BY dataType ORDER BY dataType",
     ["dataType", "Max", "max_2"], [["INT", 6, "C6"], ["STRING", 7, "C7"]]),
    ("SELECT dataType, COUNT(*) AS Count_2, COUNT(*), COUNT(*) FROM columns GROUP BY dataType ORDER BY dataType",
     ["dataType", "Count_2", "count", "count_3"], [["INT", 4, 4, 4], ["STRING", 4, 4, 4]]),
    ("SELECT COUNT(*) AS COUNT, SUM(ordinal) AS Count_2, COUNT(*) FROM columns",
     ["COUNT", "Count_2", "count_3"], [[8, 28, 8]]),
])
def test_r6_autonombre_no_choca_con_un_alias_en_otra_caja(qdb, raw, text, keys, rows):
    """Repro R16 (`test_h1_agregado_sin_alias_no_choca_con_un_alias_en_otra_caja`)."""
    status, data = _sql(raw, text)
    assert status == 200, (status, data)
    got = [c["key"] for c in data["columns"]]
    assert got == keys and [[r[k] for k in keys] for r in data["rows"]] == rows, data
    assert not qdb.problems, qdb.problems[:2]


def test_r6_alias_explicitos_que_solo_difieren_en_caja_siguen_siendo_repetidos(qdb, raw):
    """Consecuencia aceptada de F8 (R16 `test_h1b_*`): dos alias EXPLÍCITOS `n` y
    `N` son el mismo nombre de salida — el autonombre no los arregla."""
    status, detail = _sql(raw, "SELECT dataType, COUNT(*) AS n, SUM(ordinal) AS N FROM columns GROUP BY dataType")
    assert status == 400 and "'N' is repeated" in detail, (status, detail)


# ── H3: resultColumns sin agrupar, también en view_columns ───────────────────
MESSAGE = "resultColumns only applies to grouped queries."


@pytest.mark.parametrize("route", ["/api/reporting/query", "/api/reporting/export"])
def test_r6_result_columns_sin_agrupar_es_422_en_view_columns(qdb, raw, route):
    """Repro R16 (`test_h3_…`): era 200 y la opción se ignoraba en silencio."""
    body = {"projectId": "p1", "from": "view_columns", "select": ["viewName"], "resultColumns": ["zzz"]}
    res = raw.post(route, json=body)
    assert res.status_code == 422 and res.json()["detail"] == MESSAGE, (res.status_code, res.text[:200])


def test_r6_check_spec_marca_result_columns_en_view_columns():
    """`check_spec` (lo que usa `/query/validate`) tampoco lo marcaba."""
    spec = QuerySpec.model_validate({"projectId": "p1", "from": "view_columns", "select": ["viewName"],
                                     "resultColumns": ["viewName"]})
    with pytest.raises(QueryError) as exc:
        ex.check_spec(spec, build_catalog("view_columns", []))
    assert exc.value.code == 422 and str(exc.value) == MESSAGE


def test_r6_view_columns_sin_result_columns_sigue_igual(qdb, raw):
    res = raw.post("/api/reporting/query", json={"projectId": "p1", "from": "view_columns",
                                                  "select": ["viewName", "outputName"]})
    assert res.status_code == 200 and [(r["viewName"], r["outputName"]) for r in res.json()["data"]["rows"]] == \
        [("V", "ID")], res.text[:200]
