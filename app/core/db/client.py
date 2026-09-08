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
from typing import Any

from app.core.config import settings
from app.core.db.indexes import ensure_indexes

log = logging.getLogger(__name__)

_pg_db: Any | None = None

# Clave fija del advisory lock que serializa el DDL de arranque entre
# workers uvicorn (procesos distintos). Cualquier bigint estable sirve.
_STARTUP_DDL_LOCK = 528491


async def connect() -> None:
    """Abre la conexión a Lakebase y asegura tablas/índices.

    Idempotente: una segunda llamada es no-op si ya está conectado.
    """
    global _pg_db
    if _pg_db is not None:
        return
    from app.core.db.lakebase import LakebaseDatabase, create_pool

    pool = await create_pool()
    db = LakebaseDatabase(pool, settings.LAKEBASE_PGSCHEMA)
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
