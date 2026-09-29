"""Doc 100 (7.2 del doc 98) — la paginación por keyset del Reporting recorre
TODAS las filas, página a página, con filtros que Lakebase sabe traducir.

El orden por defecto era `physicalName`, que las relaciones y las vistas no
tienen: cada cursor llevaba `null` y la página 2 armaba `{campo: {$gt: null}}`,
que el traductor de Lakebase rechaza (`NotImplementedError` → 500 en «load
more» y a mitad del export). mongomock NO lo rechaza: por eso cada filtro que
llega a `find` pasa además por el traductor REAL de Lakebase."""
from __future__ import annotations

import asyncio

import pytest

from app.core.db import client as db_client
from app.core.db.lakebase.translate import Sql, filter_sql
from app.features.reporting.query import executor as ex
from app.features.reporting.query.spec import QuerySpec
from tests.support.fakedb import FakeCollection, FakeDb


@pytest.fixture
def db(monkeypatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    # Sin UDP ni hidrataciones: el catálogo estático alcanza.
    monkeypatch.setattr(ex, "_udp_defs", lambda project_id: _no_udp())
    orig = FakeCollection.find
    fake.filters = []

    def find(self, filter=None, projection=None):
        filter_sql(filter or {}, Sql(), mode="table")   # levanta como en Lakebase
        fake.filters.append((self.name, filter))
        return orig(self, filter, projection)

    monkeypatch.setattr(FakeCollection, "find", find)
    return fake


async def _no_udp() -> list[dict]:
    return []


def _all_pages(spec: QuerySpec) -> tuple[list[str], int]:
    ids: list[str] = []
    cursor, pages = None, 0
    while True:
        out = asyncio.run(ex.run_query(spec, cursor=cursor))
        ids += [r["_id"] for r in out["rows"]]
        pages += 1
        if not out["hasMore"]:
            return ids, pages
        cursor = out["nextCursor"]


def _spec(**kw) -> QuerySpec:
    return QuerySpec.model_validate({"projectId": "p1", "limit": 2, **kw})


def test_las_relaciones_paginan_completas(db):
    for i in range(5):
        db.raw["relationships"].insert_one({"_id": f"r{i}", "projectId": "p1", "flgactive": True,
                                            "parentTableId": "t0", "childTableId": f"t{i + 1}"})
    ids, pages = _all_pages(_spec(**{"from": "relationships"}))
    assert ids == ["r0", "r1", "r2", "r3", "r4"] and pages == 3
    # Ordena y corta por la PK (índice): nada de `physicalName`, que no existe.
    last = [f for coll, f in db.filters if coll == "relationships"][-1]
    assert last["$and"][1] == {"_id": {"$gt": "r3"}} and "physicalName" not in str(last)


def test_las_vistas_paginan_completas(db):
    for i in range(3):
        db.raw["views"].insert_one({"_id": f"v{i}", "projectId": "p1", "flgactive": True,
                                    "name": f"V_{i}", "schema": "S"})
    ids, pages = _all_pages(_spec(**{"from": "views"}))
    assert ids == ["v0", "v1", "v2"] and pages == 2


@pytest.mark.parametrize("direction, expected", [
    ("asc", ["t-sin-nombre", "t-a", "t-b"]),        # sin valor primero (como Mongo)
    ("desc", ["t-b", "t-a", "t-sin-nombre"]),       # y último al revés
])
def test_un_registro_sin_el_campo_de_orden_no_corta_la_paginacion(db, direction, expected):
    db.raw["canonical_tables"].insert_many([
        {"_id": "t-a", "projectId": "p1", "flgactive": True, "physicalName": "A"},
        {"_id": "t-b", "projectId": "p1", "flgactive": True, "physicalName": "B"},
        {"_id": "t-sin-nombre", "projectId": "p1", "flgactive": True},
    ])
    spec = _spec(**{"from": "tables", "limit": 1, "orderBy": [{"field": "physicalName", "dir": direction}]})
    ids, _pages = _all_pages(spec)
    assert ids == expected


def test_las_tablas_siguen_ordenadas_por_nombre(db):
    db.raw["canonical_tables"].insert_many([
        {"_id": "t1", "projectId": "p1", "flgactive": True, "physicalName": "C"},
        {"_id": "t2", "projectId": "p1", "flgactive": True, "physicalName": "A"},
        {"_id": "t3", "projectId": "p1", "flgactive": True, "physicalName": "B"},
    ])
    ids, pages = _all_pages(_spec(**{"from": "tables"}))
    assert ids == ["t2", "t3", "t1"] and pages == 2


# ── Revisión independiente 2026-09-28: el mensaje del orden sin índice ───────

from app.features.reporting.query.compiler import QueryError, compile_spec  # noqa: E402
from app.features.reporting.query.schema import build_catalog  # noqa: E402


@pytest.mark.parametrize("entity, field, hint", [
    ("tables", "logicalName", "physicalName"),
    ("models", "folderId", "name"),
    ("relationships", "identifying", None),
    ("views", "name", None),
])
def test_el_error_de_orden_nombra_los_campos_ordenables_de_esa_entidad(entity, field, hint):
    catalog = build_catalog(entity, [])
    spec = QuerySpec.model_validate({"from": entity, "projectId": "p1", "orderBy": [{"field": field, "dir": "asc"}]})
    with pytest.raises(QueryError) as e:
        compile_spec(spec, catalog)
    msg = str(e.value)
    assert f"Can't sort by {field!r}" in msg
    if hint:
        assert msg.endswith(f"Sort by an indexed field: {hint}."), msg
    else:
        assert "physicalName" not in msg and "default order" in msg, msg
