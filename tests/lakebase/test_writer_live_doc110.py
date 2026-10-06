"""Doc 110 — escritor resistente contra el Lakebase REAL (suite viva).

Schema efímero `dmh_test_<rand>` que se dropea al final; sólo corre con
`LAKEBASE_TESTS=1` (igual que `test_adapter_live.py`). Conexiones probadas y 3
conexiones a la vez: dos versiones de 5 000 documentos en sentencias chicas
(muchas, repartidas) y la segunda versión debe quedar en todos."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from pymongo import UpdateOne

pytestmark = pytest.mark.skipif(
    os.getenv("LAKEBASE_TESTS") != "1",
    reason="Suite viva contra Lakebase: correr con LAKEBASE_TESTS=1",
)


def test_tres_conexiones_probadas_escriben_dos_versiones_en_orden():
    from app.core.db.lakebase import LakebaseDatabase, create_pool
    from app.core.db.lakebase.writer import ResilientWriter

    async def go():
        schema = f"dmh_test_{uuid.uuid4().hex[:8]}"
        pool = await create_pool(probe=True)
        try:
            async with pool.acquire() as conn:
                await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            coll = LakebaseDatabase(pool, schema)["docs"]
            writer = ResilientWriter(pool, workers=3)
            for version in (1, 2):
                ops = [UpdateOne({"_id": f"d{i:05d}"},
                                 {"$set": {"v": version, "pad": "x" * 200},
                                  "$setOnInsert": {"createdAt": "T"}}, upsert=True)
                       for i in range(5000)]
                await writer.submit(coll, coll.bulk_statements(ops, 256 * 1024))
            await writer.drain()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    f'SELECT doc->>\'v\' AS v, count(*) AS n FROM "{schema}"."docs" GROUP BY 1')
                created = await conn.fetchval(
                    f'SELECT count(*) FROM "{schema}"."docs" WHERE doc->>\'createdAt\' = \'T\'')
            return {r["v"]: r["n"] for r in rows}, created, writer.stats
        finally:
            async with pool.acquire() as conn:
                await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await pool.close()

    versions, created, stats = asyncio.run(go())
    assert versions == {"2": 5000} and created == 5000
    assert stats["docs"] == 10000


def test_terminar_la_sesion_abandonada_evita_que_confirme_tarde():
    """Revisión del doc 110 (ALTO): una sentencia que el servidor ya recibió
    sigue corriendo aunque el cliente cierre su conexión, y confirma TARDE,
    encima de la versión nueva. Terminando su sesión (`_BURY_SQL`, la de antes
    de cada reintento del escritor) antes de escribir la nueva, no confirma."""
    from app.core.db.lakebase import create_pool
    from app.core.db.lakebase.writer import _BURY_SQL, _SESSION_SQL

    async def scenario(bury: bool) -> tuple[int, bool]:
        schema = f"dmh_test_{uuid.uuid4().hex[:8]}"
        table = f'"{schema}"."t"'
        upsert = f"INSERT INTO {table} (id, v) VALUES ($1, $2) ON CONFLICT (id) DO UPDATE SET v = EXCLUDED.v"
        pool = await create_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(f'CREATE SCHEMA "{schema}"; CREATE TABLE {table} (id text PRIMARY KEY, v int)')
            a = await pool.acquire()
            pid, started = (await a.fetchrow(_SESSION_SQL)).values()
            slow = asyncio.create_task(a.execute(
                f"WITH s AS (SELECT pg_sleep(3)) INSERT INTO {table} (id, v) SELECT 'a', 1 FROM s "
                "ON CONFLICT (id) DO UPDATE SET v = EXCLUDED.v"))
            await asyncio.sleep(1.0)                  # el servidor ya la recibió y está corriendo
            slow.cancel()
            a.terminate()                             # el cliente la abandona (como el escritor)
            buried = False
            async with pool.acquire() as b:
                if bury:
                    buried = await b.fetchval(_BURY_SQL, pid, started)
                await b.execute(upsert, "a", 2)       # el reintento / la versión nueva
            await asyncio.sleep(3.5)                  # la abandonada ya habría confirmado
            async with pool.acquire() as conn:
                return await conn.fetchval(f"SELECT v FROM {table} WHERE id = 'a'"), buried
        finally:
            async with pool.acquire() as conn:
                await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await pool.close()

    without, _ = asyncio.run(scenario(bury=False))
    print(f"\nsin terminar la sesión abandonada queda v={without} (1 = confirmó tarde; "
          "depende de cuándo le llegan los bytes al servidor)")
    value, buried = asyncio.run(scenario(bury=True))
    assert buried is True and value == 2


def test_la_terminacion_mata_la_sesion_viva_y_respeta_un_pid_de_otra_sesion():
    """`_BURY_SQL` termina de verdad una sesión que está corriendo, pero sólo si
    pid E inicio coinciden (un pid reusado por otra sesión no se toca). En
    Lakebase el pid que asyncpg recibe del proxy NO es el del backend: por eso
    la sesión se identifica con `pg_backend_pid()` (`_SESSION_SQL`)."""
    from datetime import timedelta

    import asyncpg

    from app.core.db.lakebase import create_pool
    from app.core.db.lakebase.writer import _BURY_SQL, _SESSION_SQL

    async def go():
        pool = await create_pool()
        try:
            a = await pool.acquire()
            pid, started = (await a.fetchrow(_SESSION_SQL)).values()
            running = asyncio.create_task(a.fetchval("SELECT pg_sleep(20)"))
            await asyncio.sleep(0.5)
            async with pool.acquire() as b:
                untouched = await b.fetchval(_BURY_SQL, pid, started + timedelta(microseconds=1))
                still_running = not running.done()
                gone = await b.fetchval(_BURY_SQL, pid, started)
            with pytest.raises((asyncpg.exceptions.PostgresConnectionError,     # «connection was closed…»
                                asyncpg.exceptions.AdminShutdownError, asyncpg.exceptions.InterfaceError,
                                ConnectionError, OSError)):
                await asyncio.wait_for(running, 10)
            return untouched, still_running, gone
        finally:
            await pool.close()

    untouched, still_running, gone = asyncio.run(go())
    assert untouched is True and still_running and gone is True
