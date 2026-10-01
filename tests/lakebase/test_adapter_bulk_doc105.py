"""Doc 105 — camino rápido de `bulk_write` del adaptador Lakebase contra un pool
FALSO que registra cada sentencia (sin Postgres; patrón de
`tests/features/changesets/test_apply_revive_doc100.py`).

- P10: `_bulk_update_by_id` insertaba filas para TODOS los ids del lote si
  alguno traía `upsert` — la baja de algo creado y borrado en el mismo draft
  (un id que producción nunca vio) dejaba una fila fantasma sin `projectId`.
- A2-o1: `_bulk_replace_by_id` decidía el upsert con `all(...)`: un lote con
  banderas mezcladas perdía sus upserts (sólo UPDATE).

FakeDb (mongomock) no ve ninguno de los dos: el camino rápido es SQL puro."""
from __future__ import annotations

import asyncio
import json

from pymongo import ReplaceOne, UpdateOne

from app.core.db.lakebase.collection import PgCollection


class _Recorder:
    """Pool falso: guarda (sql, params) de cada sentencia. Ninguna fila existe:
    un UPDATE no encuentra nada y un INSERT inserta."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def acquire(self):
        rec = self

        class Conn:
            async def execute(self, sql, *params):
                rec.calls.append((sql, params))
                return "INSERT 0 1" if sql.lstrip().startswith("INSERT") else "UPDATE 0"

            async def fetch(self, sql, *params, timeout=None):
                rec.calls.append((sql, params))
                return []

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

    def statements(self) -> list[str]:
        return [sql.split()[0] for sql, _ in self.calls]

    def inserted(self) -> dict[str, dict]:
        """{id: doc} de TODOS los INSERT (lote por unnest o de a uno)."""
        out: dict[str, dict] = {}
        for sql, params in self.calls:
            if not sql.lstrip().startswith("INSERT"):
                continue
            if isinstance(params[0], list):                       # unnest($1::text[], $2::text[])
                out.update({i: json.loads(d) for i, d in zip(params[0], params[1])})
            else:                                                 # VALUES ($1, $2)
                out[params[0]] = json.loads(params[1])
        return out


def _coll(rec: _Recorder, name: str = "canonical_columns") -> PgCollection:
    class FakeLakebase:
        schema = "dmh"
        pool = rec

        async def ensure_table(self, name):
            return None

    return PgCollection(FakeLakebase(), name)


# ── P10 ─────────────────────────────────────────────────────────────────────
def test_p10_solo_los_upserts_insertan_filas():
    """Plan de un publish: una columna nueva (upsert) + la baja de una columna
    creada y borrada en el MISMO draft (sin upsert: producción nunca la vio)."""
    rec = _Recorder()
    asyncio.run(_coll(rec).bulk_write([
        UpdateOne({"_id": "c-nueva"}, {"$set": {"projectId": "p1", "tableId": "t1", "flgactive": True},
                                       "$setOnInsert": {"createdAt": "T"}}, upsert=True),
        UpdateOne({"_id": "c-creada-y-borrada"}, {"$set": {"flgactive": False, "deletedAt": "T"}}),
    ], ordered=False))
    assert rec.statements() == ["UPDATE", "INSERT"]               # sigue en el camino rápido
    assert rec.inserted() == {"c-nueva": {"_id": "c-nueva", "createdAt": "T", "projectId": "p1",
                                          "tableId": "t1", "flgactive": True}}


def test_p10_lote_sin_upserts_no_inserta_nada():
    rec = _Recorder()
    asyncio.run(_coll(rec).bulk_write([UpdateOne({"_id": "x"}, {"$set": {"flgactive": False}}),
                                       UpdateOne({"_id": "y"}, {"$set": {"flgactive": False}})]))
    assert rec.statements() == ["UPDATE"]


# ── A2-o1 ───────────────────────────────────────────────────────────────────
def test_a2o1_replace_con_banderas_mezcladas_conserva_los_upserts():
    rec = _Recorder()
    asyncio.run(_coll(rec, "x").bulk_write([ReplaceOne({"_id": "nuevo"}, {"v": 1}, upsert=True),
                                            ReplaceOne({"_id": "viejo"}, {"v": 2})]))
    assert rec.inserted() == {"nuevo": {"_id": "nuevo", "v": 1}}   # 'viejo' sin upsert: no se crea


def test_a2o1_replace_homogeneo_sigue_en_una_sentencia():
    rec = _Recorder()
    asyncio.run(_coll(rec, "x").bulk_write([ReplaceOne({"_id": f"r{i}"}, {"v": i}, upsert=True) for i in range(3)]))
    assert rec.statements() == ["INSERT"]
    assert sorted(rec.inserted()) == ["r0", "r1", "r2"]
    rec = _Recorder()
    asyncio.run(_coll(rec, "x").bulk_write([ReplaceOne({"_id": f"r{i}"}, {"v": i}) for i in range(3)]))
    assert rec.statements() == ["UPDATE"]
