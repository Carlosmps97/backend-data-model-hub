"""Doc 110 — el adaptador ARMA las sentencias del upsert por lote sin ejecutarlas.

El escritor resistente de los scripts (`app/core/db/lakebase/writer.py`) las
corre con tiempo máximo y reintento en otra conexión; para que una sentencia
lenta no sea de decenas de MB, se cortan por bytes (un documento nunca se parte).
La app sigue por `bulk_write`: una sola sentencia por lote (doc 109)."""
from __future__ import annotations

import asyncio

from pymongo import DeleteOne, ReplaceOne, UpdateOne

from tests.lakebase.test_adapter_bulk_doc105 import _coll, _Recorder


def _up(_id: str, size: int) -> UpdateOne:
    return UpdateOne({"_id": _id}, {"$set": {"v": "x" * size}, "$setOnInsert": {"c": 1}}, upsert=True)


def test_corta_por_bytes_sin_partir_un_documento_y_conserva_el_orden():
    ops = [_up(f"d{i}", 1000) for i in range(5)]
    stmts = _coll(_Recorder()).bulk_statements(ops, max_bytes=2500)
    assert [s.args[0] for s in stmts] == [["d0", "d1"], ["d2", "d3"], ["d4"]]
    assert [s.ndocs for s in stmts] == [2, 2, 1]
    assert all(2000 < s.nbytes <= 2500 for s in stmts[:2])


def test_un_documento_mas_grande_que_el_tope_va_solo():
    ops = [_up("a", 10), _up("grande", 5000), _up("b", 10)]
    stmts = _coll(_Recorder()).bulk_statements(ops, max_bytes=2000)
    assert [s.args[0] for s in stmts] == [["a"], ["grande"], ["b"]]


def test_sin_tope_es_una_sola_sentencia():
    ops = [_up(f"d{i}", 100) for i in range(50)]
    (st,) = _coll(_Recorder()).bulk_statements(ops)
    assert st.args[0] == [f"d{i}" for i in range(50)]
    assert st.ndocs == 50


def test_replace_tambien_se_corta_y_conserva_el_upsert():
    ops = [ReplaceOne({"_id": f"r{i}"}, {"v": "x" * 1000}, upsert=True) for i in range(3)]
    stmts = _coll(_Recorder(), "x").bulk_statements(ops, max_bytes=2200)
    assert [s.args[0] for s in stmts] == [["r0", "r1"], ["r2"]]
    assert {s.sql.split()[0] for s in stmts} == {"INSERT"}


def test_lo_que_no_es_idempotente_por_id_no_tiene_sentencias():
    c = _coll(_Recorder())
    assert c.bulk_statements([UpdateOne({"_id": "a"}, {"$inc": {"n": 1}})]) is None
    assert c.bulk_statements([DeleteOne({"_id": "a"})]) is None
    assert c.bulk_statements([UpdateOne({"_id": "a"}, {"$set": {"x.y": 1}})]) is None
    assert c.bulk_statements([UpdateOne({"tableId": "t"}, {"$set": {"x": 1}})]) is None
    assert c.bulk_statements([]) is None


def test_la_app_sigue_en_una_sola_sentencia_aunque_el_lote_pese_megas():
    rec = _Recorder()
    asyncio.run(_coll(rec).bulk_write([_up(f"d{i}", 3000) for i in range(2000)]))   # ~6 MB
    assert rec.statements() == ["WITH"]
