"""Singleton de conexión a la BD del backend (seam de doble backend, doc 28).

`DB_BACKEND` decide la implementación:
- `lakebase` → Databricks Lakebase Postgres vía el adaptador JSONB
  (`app/core/db/lakebase/`) con credenciales OAuth rotativas.
- `cosmos`   → Motor (Cosmos DB Mongo API), el camino legacy/rollback.

Contrato intacto para repositorios y scripts: `connect()` una vez en el
lifespan, `get_db()` en handlers/repos (devuelve un handle con superficie
Motor), `disconnect()` en el teardown.
"""

from __future__ import annotations

import logging
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings
from app.core.db.indexes import ensure_indexes

log = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None
_pg_db: Any | None = None


async def connect() -> None:
    """Abre la conexión del backend configurado y asegura tablas/índices.

    Idempotente: una segunda llamada es no-op si ya está conectado.
    """
    global _client, _pg_db
    if settings.DB_BACKEND == "lakebase":
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
        return

    if _client is not None:
        return
    if not settings.COSMOS_CONNECTION_STRING:
        raise RuntimeError(
            "COSMOS_CONNECTION_STRING is not set. "
            "Async DB operations require Azure Cosmos DB."
        )
    _client = AsyncIOMotorClient(
        settings.COSMOS_CONNECTION_STRING,
        serverSelectionTimeoutMS=15_000,
        # Sin estos timeouts, un socket colgado bloquea el request por el
        # default del SO (minutos) y con varios usuarios se agota el pool.
        connectTimeoutMS=10_000,
        socketTimeoutMS=60_000,
        maxPoolSize=50,
    )
    db = _client[settings.COSMOS_DATABASE]
    await ensure_indexes(db)
    log.info("motor connected", extra={"database": settings.COSMOS_DATABASE})


async def disconnect() -> None:
    """Cierra la conexión activa. Seguro de llamar aunque no esté conectado."""
    global _client, _pg_db
    if _pg_db is not None:
        await _pg_db.pool.close()
        _pg_db = None
        log.info("lakebase disconnected")
    if _client is not None:
        _client.close()
        _client = None
        log.info("motor disconnected")


async def get_db() -> Any:
    """Devuelve el handle de base de datos compartido (superficie Motor).

    Levanta `RuntimeError` si `connect()` no fue llamado todavía.
    """
    if settings.DB_BACKEND == "lakebase":
        if _pg_db is None:
            raise RuntimeError(
                "Lakebase is not connected. "
                "Ensure connect() was called in the FastAPI lifespan."
            )
        return _pg_db
    if _client is None:
        raise RuntimeError(
            "Motor client is not connected. "
            "Ensure connect() was called in the FastAPI lifespan."
        )
    return _client[settings.COSMOS_DATABASE]
