"""Doc 102 (revisión A2): con una versión propia, las columnas de un LOTE leen
solo los cambios del ledger que tocan ese lote — nunca el ledger entero de la
versión por cada lote (el export por niveles manda decenas de lotes y un draft
de carga Excel puede traer cientos de miles de columnas)."""
from __future__ import annotations

import asyncio

import pytest

from app.core.db import client as db_client
from app.features.changesets import repository as cs_repo
from app.features.reporting import draft, repository, service
from tests.support.fakedb import FakeDb

P = "p1"
AT = "2026-09-29T00:00:00+00:00"
ANA = {"X-Dev-User": "ana"}


def _col(cid, tid, name, active=True):
    return {"_id": cid, "projectId": P, "flgactive": active, "tableId": tid, "physicalName": name,
            "logicalName": name.title(), "dataType": "INT", "ordinal": 0}


def _ch(eid, op, payload=None):
    doc = {"_id": cs_repo.change_key("cs1", "canonical_columns", eid), "csId": "cs1",
           "collection": "canonical_columns", "entityId": eid, "op": op, "at": AT}
    if payload is not None:
        doc["payload"] = {"projectId": P, "logicalName": "x", "dataType": "INT", "ordinal": 0, **payload}
    return doc


@pytest.fixture
def db(monkeypatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    raw = fake.raw
    raw["projects"].insert_one({"_id": P, "name": "MODELO DDV", "flgactive": True})
    raw["canonical_tables"].insert_many([{"_id": t, "projectId": P, "flgactive": True, "physicalName": t.upper()}
                                         for t in ("t1", "t2", "t3")])
    raw["canonical_columns"].insert_many([
        _col("a1", "t1", "EDITADA"), _col("a2", "t1", "BORRADA"), _col("a3", "t1", "SE_VA"),
        _col("b1", "t3", "LLEGA"), _col("b2", "t2", "IGUAL"), _col("r1", "t2", "REVIVE", active=False),
        _col("z1", "t3", "OTRA")])
    raw["changesets"].insert_one({"_id": "cs1", "title": "v2", "owner": "ana", "projectId": P, "status": "draft"})
    raw["changeset_changes"].insert_many([
        _ch("a1", "upsert", {"tableId": "t1", "physicalName": "EDITADA_V2"}),   # edición en sitio
        _ch("a2", "delete"),                                                      # baja
        _ch("a3", "upsert", {"tableId": "t3", "physicalName": "SE_VA"}),        # se muda FUERA del lote
        _ch("b1", "upsert", {"tableId": "t2", "physicalName": "LLEGA"}),        # se muda HACIA el lote
        _ch("n1", "upsert", {"tableId": "t2", "physicalName": "NUEVA"}),        # alta
        _ch("r1", "upsert", {"tableId": "t2", "physicalName": "REVIVE"}),       # revive
        _ch("x1", "upsert", {"tableId": "t3", "physicalName": "AFUERA"}),       # alta de otra tabla
        _ch("z1", "delete"),                                                      # baja de otra tabla
    ])
    return fake


def _rows(lot, table_id=None):
    got = asyncio.run(service.list_column_rows(P, table_id, lot, changeset_id="cs1"))
    return {r["id"]: r["physicalName"] for r in got}


def test_lote_con_la_version_trae_ediciones_bajas_mudanzas_altas_y_revividas(db):
    assert _rows(["t1", "t2"]) == {"a1": "EDITADA_V2", "b1": "LLEGA", "b2": "IGUAL", "n1": "NUEVA", "r1": "REVIVE"}
    assert _rows(["t3"]) == {"a3": "SE_VA", "x1": "AFUERA"}
    assert _rows(None, table_id="t1") == {"a1": "EDITADA_V2"}


def test_mismo_resultado_que_superponer_el_ledger_entero(db):
    full = asyncio.run(cs_repo.changes_map("cs1", ["canonical_columns"]))["canonical_columns"]
    for lot in (["t1"], ["t2"], ["t3"], ["t1", "t2", "t3"]):
        published = asyncio.run(repository.columns(P, None, lot, None))
        expected = {c["id"]: c["physicalName"] for c in draft.overlay_columns(published, full, P, set(lot))}
        assert _rows(lot) == expected, lot


def test_sin_lote_es_toda_la_version(db):
    assert set(_rows(None)) == {"a1", "a3", "b1", "b2", "n1", "r1", "x1"}


def test_post_de_un_lote_no_lee_el_ledger_entero_de_la_version(db, client, monkeypatch):
    async def _full_read(*_a, **_k):
        raise AssertionError("leyó el ledger ENTERO de la versión para un lote")
    monkeypatch.setattr(cs_repo, "changes_map", _full_read)
    res = client.post("/api/reporting/columns/query", headers=ANA,
                      json={"projectId": P, "tableIds": ["t1", "t2"], "changesetId": "cs1"})
    assert res.status_code == 200
    assert sorted(c["id"] for c in res.json()["data"]) == ["a1", "b1", "b2", "n1", "r1"]
