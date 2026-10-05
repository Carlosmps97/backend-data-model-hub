"""Doc 100 (P3) — el publish reactiva una entidad borrada SIN dejarle la marca
de borrado, y la revisión puede saber qué entidades del draft están borradas
en producción. Contra una BD falsa REAL (mongomock), no un mock."""
from __future__ import annotations

import asyncio

import pytest

from app.core.db import client as db_client
from app.features.changesets import repository
from tests.support.fakedb import FakeDb

DELETED_AT = "2026-09-28T11:00:00+00:00"


@pytest.fixture
def db(monkeypatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    return fake


def _seed(db: FakeDb) -> None:
    db.raw["subject_areas"].insert_many([
        {"_id": "sa-borrado", "projectId": "p1", "name": "Viejo", "flgactive": False,
         "deletedAt": DELETED_AT, "deletedIn": "cs-borro", "createdAt": "2026-09-01T00:00:00+00:00"},
        {"_id": "sa-vivo", "projectId": "p1", "name": "Vivo", "flgactive": True,
         "createdAt": "2026-09-01T00:00:00+00:00"},
    ])


def test_el_publish_reactiva_sin_marca_de_borrado(db):
    _seed(db)
    plan = [
        ("subject_areas", "sa-borrado", "upsert", {"projectId": "p1", "name": "Vuelve",
                                                   "deletedAt": "2026-01-01T00:00:00+00:00"}),
        ("subject_areas", "sa-vivo", "upsert", {"projectId": "p1", "name": "Vivo 2"}),
        ("subject_areas", "sa-nuevo", "upsert", {"projectId": "p1", "name": "Nuevo"}),
    ]
    counts = asyncio.run(repository.apply_changes(plan))
    assert counts == {"subject_areas": 3}
    revived = db.raw["subject_areas"].find_one({"_id": "sa-borrado"})
    assert revived["flgactive"] is True and revived["name"] == "Vuelve"
    assert "deletedAt" not in revived and "deletedIn" not in revived
    assert revived["createdAt"] == "2026-09-01T00:00:00+00:00"          # no es un alta
    vivo = db.raw["subject_areas"].find_one({"_id": "sa-vivo"})
    assert vivo["name"] == "Vivo 2" and "deletedAt" not in vivo
    nuevo = db.raw["subject_areas"].find_one({"_id": "sa-nuevo"})
    assert nuevo["flgactive"] is True and nuevo["createdAt"]


def test_un_payload_no_puede_marcar_como_borrada_una_entidad_activa(db):
    _seed(db)
    asyncio.run(repository.apply_changes(
        [("subject_areas", "sa-vivo", "upsert", {"projectId": "p1", "name": "Vivo", "deletedAt": DELETED_AT,
                                                 "deletedIn": "cs-x"})]))
    vivo = db.raw["subject_areas"].find_one({"_id": "sa-vivo"})
    assert "deletedAt" not in vivo and "deletedIn" not in vivo


def test_deleted_at_devuelve_solo_las_borradas(db):
    _seed(db)
    out = asyncio.run(repository.deleted_at("subject_areas", ["sa-borrado", "sa-vivo", "sa-no-existe"]))
    assert out == {"sa-borrado": DELETED_AT}
    assert asyncio.run(repository.deleted_at("subject_areas", [])) == {}


# ── Revisión independiente 2026-09-28: costo contra el adaptador REAL ──────
# Un rollback de una versión que borró N entidades publica N reactivaciones.
# Con el `$unset` dentro de cada op, el adaptador sacaba el lote del camino
# rápido (una sentencia por fila) y la lectura previa con `{"_id": 1}` traía
# documentos completos (el adaptador la leía como «sin exclusiones» — corregido
# en el doc 105, P9).

import json  # noqa: E402

from app.core.db.lakebase.collection import PgCollection  # noqa: E402


class _Recorder:
    """Pool falso del adaptador Lakebase: cuenta sentencias; `fetch` devuelve
    como borrados los ids de `deleted`."""

    def __init__(self, deleted: set[str]) -> None:
        self.deleted = deleted
        self.sql: list[str] = []
        self.params: list[tuple] = []

    def conn(self):
        rec = self

        class Conn:
            async def execute(self, sql, *params):
                rec.sql.append(sql)
                rec.params.append(params)
                return "UPDATE 1"

            async def fetch(self, sql, *params, timeout=None):
                rec.sql.append(sql)
                rec.params.append(params)
                ids = max((p for p in params if isinstance(p, list)), key=len)
                return [{"doc": json.dumps({"_id": i, "flgactive": False})} for i in ids if i in rec.deleted]

            async def fetchrow(self, sql, *params, timeout=None):
                # doc 109: el upsert por lote es UNA sentencia que devuelve conteos
                rec.sql.append(sql)
                rec.params.append(params)
                return {"updated": 0, "inserted": len(params[0])}

            def transaction(self):
                class T:
                    async def __aenter__(s):
                        return s

                    async def __aexit__(s, *a):
                        return False
                return T()

        class Acq:
            async def __aenter__(self):
                return Conn()

            async def __aexit__(self, *a):
                return False
        return Acq()


def _lakebase(monkeypatch, deleted: set[str]) -> _Recorder:
    rec = _Recorder(deleted)

    class FakeLakebase:
        schema = "dmh"

        class pool:
            @staticmethod
            def acquire():
                return rec.conn()

        async def ensure_table(self, name):
            return None

        def __getitem__(self, name):
            return PgCollection(self, name)

    monkeypatch.setattr(db_client, "_pg_db", FakeLakebase())
    return rec


@pytest.mark.parametrize("n", [10, 1000])
def test_reactivar_n_entidades_cuesta_lo_mismo_que_publicarlas(monkeypatch, n):
    ids = [f"c{i}" for i in range(n)]
    plan = [("canonical_columns", i, "upsert", {"tableId": "t", "physicalName": i}) for i in ids]
    normal = _lakebase(monkeypatch, set())
    asyncio.run(repository.apply_changes(plan))
    revive = _lakebase(monkeypatch, set(ids))
    asyncio.run(repository.apply_changes(plan))
    assert len(revive.sql) <= len(normal.sql) + 1, (len(normal.sql), len(revive.sql))
    unset = [q for q, prm in zip(revive.sql, revive.params) if q.lstrip().startswith("UPDATE") and "deletedAt" in prm]
    assert len(unset) == 1                                   # las marcas salen en UNA sentencia


def test_la_lectura_previa_no_trae_documentos_completos(monkeypatch):
    rec = _lakebase(monkeypatch, {"c1"})
    asyncio.run(repository.apply_changes([("canonical_columns", "c1", "upsert", {"tableId": "t"})]))
    pre = next(q for q in rec.sql if q.lstrip().startswith("SELECT"))
    assert "jsonb_build_object" in pre and "(doc - " not in pre, pre
