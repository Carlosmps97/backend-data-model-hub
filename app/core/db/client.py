"""Singleton de conexión a la BD del backend (Databricks Lakebase Postgres, doc 28).

El backend corre sobre Databricks Lakebase Postgres vía el adaptador JSONB
(`app/core/db/lakebase/`) con credenciales OAuth rotativas. El adaptador expone
una superficie estilo Mongo, así que repositorios y scripts no ven Postgres
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
        await db.ensure_base()
        await ensure_indexes(db)
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
    """Devuelve el handle de base de datos compartido (superficie estilo Mongo).

    Levanta `RuntimeError` si `connect()` no fue llamado todavía.
    """
    if _pg_db is None:
        raise RuntimeError(
            "Lakebase is not connected. "
            "Ensure connect() was called in the FastAPI lifespan."
        )
    return _pg_db
