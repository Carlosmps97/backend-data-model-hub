"""Singleton de conexión a la BD del backend (Databricks Lakebase Postgres, doc 28).

El backend corre sobre Databricks Lakebase Postgres vía el adaptador JSONB
(`app/core/db/lakebase/`) con credenciales OAuth rotativas. El adaptador expone
una superficie estilo pymongo, así que repositorios y scripts no ven Postgres
directamente.

Contrato intacto: `connect()` una vez en el lifespan, `get_db()` en
handlers/repos, `disconnect()` en el teardown.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.core.config import settings
from app.core.db.indexes import ensure_indexes

log = logging.getLogger(__name__)

_pg_db: Any | None = None

# Doc 109: variable con la que el orquestador del one-shot evita repetir el DDL
# de arranque en los pasos que corren después de create_admin.
SKIP_STARTUP_DDL_ENV = "DMH_SKIP_STARTUP_DDL"

# Clave fija del advisory lock que serializa el DDL de arranque entre
# workers uvicorn (procesos distintos). Cualquier bigint estable sirve.
_STARTUP_DDL_LOCK = 528491


async def connect(probe: bool = False) -> None:
    """Abre la conexión a Lakebase y asegura tablas/índices.

    Idempotente: una segunda llamada es no-op si ya está conectado. `probe`
    (doc 110, sólo el puente de scripts): cada conexión nueva se prueba y la
    que va lenta se descarta (`lakebase/pool.probing_connect`).
    """
    global _pg_db
    if _pg_db is not None:
        return
    from app.core.db.lakebase import LakebaseDatabase, create_pool

    pool = await create_pool(probe=probe)
    db = LakebaseDatabase(pool, settings.LAKEBASE_PGSCHEMA)
    if os.getenv(SKIP_STARTUP_DDL_ENV) == "1":
        # Doc 109: un paso del one-shot posterior a create_admin (que acaba de
        # crear tablas e índices) no repite el DDL de arranque: con la latencia
        # de Lakebase eran ~7 s por proceso. Sólo lo setea el orquestador
        # (`scripts/run_migration.py`), nunca la app. Si falta alguna tabla
        # propia (BD de otra versión), cae al arranque completo.
        try:
            assumed = await db.assume_base()
        except Exception:
            await pool.close()
            raise
        if assumed:
            _pg_db = db
            log.info("lakebase connected (startup DDL skipped)", extra={"schema": settings.LAKEBASE_PGSCHEMA})
            return
    try:
        # Con varios workers uvicorn (procesos separados) cada uno corre este
        # arranque a la vez; el _ddl_lock del adaptador es POR proceso y no
        # serializa entre ellos. Un advisory lock de Postgres (global al
        # servidor) deja que solo UN worker corra el DDL de arranque a la vez
        # — el resto espera y encuentra todo creado (IF NOT EXISTS = no-op).
        # Evita la carrera de CREATE INDEX/TABLE concurrente del 1er arranque.
        async with pool.acquire() as ddl_conn:
            await ddl_conn.execute("SELECT pg_advisory_lock($1)", _STARTUP_DDL_LOCK)
            try:
                await db.ensure_base()
                await ensure_indexes(db)
            finally:
                await ddl_conn.execute("SELECT pg_advisory_unlock($1)", _STARTUP_DDL_LOCK)
    except Exception:
        # Sin esto, cada reintento del lifespan filtraba un pool a medio
        # abrir cuando el DDL de arranque fallaba (visto en Apps).
        await pool.close()
        raise
    _pg_db = db
    log.info(
        "lakebase connected",
        extra={"database": settings.PGDATABASE, "schema": settings.LAKEBASE_PGSCHEMA},
    )


async def disconnect() -> None:
    """Cierra la conexión activa. Seguro de llamar aunque no esté conectado."""
    global _pg_db
    if _pg_db is not None:
        await _pg_db.pool.close()
        _pg_db = None
        log.info("lakebase disconnected")


async def get_db() -> Any:
    """Devuelve el handle de base de datos compartido (superficie estilo pymongo).

    Levanta `RuntimeError` si `connect()` no fue llamado todavía.
    """
    if _pg_db is None:
        raise RuntimeError(
            "Lakebase is not connected. "
            "Ensure connect() was called in the FastAPI lifespan."
        )
    return _pg_db
