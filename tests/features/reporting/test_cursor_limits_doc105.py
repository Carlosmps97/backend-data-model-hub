"""Doc 105 — entradas del Reporting que llegaban al SQL sin tipo ni tope.

- P12: un cursor keyset armado a mano con booleano, NaN o ±Infinity pasaba
  `_decode_cursor` (sólo rechazaba dict/list) y en Lakebase el traductor
  revienta (booleano: NotImplementedError; NaN: jsonpath inválido) → 500. Con
  FakeDb la suite respondía 200 y no lo veía.
- A2-o4: `offset`/`limit` de `GET /api/reporting/tables` y el offset del cursor
  de `view_columns` sin tope: un entero fuera de int8 revienta asyncpg → 500."""
from __future__ import annotations

import base64
import json

import pytest

from app.core.db import client as db_client
from app.features.reporting.query.compiler import QueryError
from app.features.reporting.query.executor import _decode_cursor
from tests.support.fakedb import FakeDb

HUGE = 10 ** 20          # > int8 (9.2e18): asyncpg lo rechaza


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


@pytest.fixture
def db(monkeypatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    on = {"projectId": "p1", "flgactive": True}
    fake.raw["canonical_tables"].insert_many([{"_id": "t1", **on, "physicalName": "A"},
                                              {"_id": "t2", **on, "physicalName": "B"}])
    fake.raw["views"].insert_one({"_id": "v1", **on, "name": "V", "schema": "s_vu",
                                  "sources": [{"tableId": "t1", "column": "A"}]})
    return fake


# ── P12 ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw", ['[true, "t1"]', '[false, "t1"]', '[NaN, "t1"]', '[Infinity, "t1"]',
                                 '[-Infinity, "t1"]', '[1e400, "t1"]'])
def test_p12_cursor_con_valor_no_comparable_es_400(raw):
    with pytest.raises(QueryError) as exc:
        _decode_cursor(_b64(raw))
    assert exc.value.code == 400


@pytest.mark.parametrize("raw", ['[null, "t1"]', '["M_CLIENTE", "t1"]', '[3, "t1"]', '[2.5, "t1"]', '[-7, "t1"]'])
def test_p12_cursores_legitimos_siguen_pasando(raw):
    assert _decode_cursor(_b64(raw)) == json.loads(raw)


def test_p12_entero_enorme_no_revienta_la_validacion():
    """Un entero JSON enorme es `int` (no `inf`) y va al jsonpath como numeric:
    validarlo con `math.isfinite` a secas levantaría OverflowError (otro 500)."""
    big = 10 ** 400
    assert _decode_cursor(_b64(json.dumps([big, "t1"]))) == [big, "t1"]


def test_p12_cursor_booleano_por_http_es_400_no_500(db, client):
    res = client.post(f"/api/reporting/query?cursor={_b64(json.dumps([True, 't1']))}",
                      json={"projectId": "p1", "from": "tables"})
    assert res.status_code == 400 and res.json()["detail"] == "Invalid cursor"


# ── A2-o4 ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("params", [{"offset": HUGE}, {"limit": HUGE}, {"limit": 50, "offset": HUGE}])
def test_a2o4_tables_con_offset_o_limit_desmedido_es_422(db, client, params):
    res = client.get("/api/reporting/tables", params={"projectId": "p1", **params})
    assert res.status_code == 422, res.text[:200]


def test_a2o4_tables_pagina_legitima_sigue_igual(db, client):
    res = client.get("/api/reporting/tables", params={"projectId": "p1", "limit": 1, "offset": 1})
    assert res.status_code == 200 and [r["physicalName"] for r in res.json()["data"]] == ["B"]


def test_a2o4_cursor_de_view_columns_desmedido_es_400(db, client):
    body = {"projectId": "p1", "from": "view_columns"}
    bad = client.post(f"/api/reporting/query?cursor={_b64(str(HUGE))}", json=body)
    assert bad.status_code == 400 and bad.json()["detail"] == "Invalid cursor"
    ok = client.post(f"/api/reporting/query?cursor={_b64('0')}", json=body)
    assert ok.status_code == 200 and [r["viewName"] for r in ok.json()["data"]["rows"]] == ["V"]
