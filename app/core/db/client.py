"""Singleton de conexión Motor (async MongoDB / Cosmos DB).

Un único cliente compartido por todos los repositorios async. Se llama
`connect()` una vez en el lifespan de FastAPI, se usa `get_db()` dentro de
los handlers/repositorios, y `disconnect()` en el teardown del lifespan.
"""

from __future__ import annotations

import logging

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import settings
from app.core.db.indexes import ensure_indexes

log = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None


async def connect() -> None:
    """Abre la conexión Motor y asegura los índices de colección.

    Idempotente: una segunda llamada es no-op si ya está conectado.
    Levanta `RuntimeError` si `COSMOS_CONNECTION_STRING` no está seteado.
    """
    global _client
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
    """Cierra la conexión Motor. Seguro de llamar aunque no esté conectado."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
        log.info("motor disconnected")


async def get_db() -> AsyncIOMotorDatabase:
    """Devuelve el handle de base de datos compartido.

    Levanta `RuntimeError` si `connect()` no fue llamado todavía.
    """
    if _client is None:
        raise RuntimeError(
            "Motor client is not connected. "
            "Ensure connect() was called in the FastAPI lifespan."
        )
    return _client[settings.COSMOS_DATABASE]
