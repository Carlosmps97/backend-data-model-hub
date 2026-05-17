"""Motor (async MongoDB) connection singleton.

Single client shared across all async DB modules. Call `connect()` once
in the FastAPI lifespan, use `get_db()` inside endpoint handlers, and
`disconnect()` during lifespan teardown.
"""

from __future__ import annotations

import asyncio
import logging

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from src.config import settings

log = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None


# ── Lifecycle ──────────────────────────────────────────────────────────────

async def connect() -> None:
    """Open the Motor connection and ensure collection indexes.

    Idempotent: calling a second time is a no-op if already connected.
    Raises `RuntimeError` if `COSMOS_CONNECTION_STRING` is not set.
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
    )
    db = _client[settings.COSMOS_DATABASE]
    await _ensure_indexes(db)
    log.info("motor connected", extra={"database": settings.COSMOS_DATABASE})


async def disconnect() -> None:
    """Close the Motor connection. Safe to call even if not connected."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
        log.info("motor disconnected")


async def get_db() -> AsyncIOMotorDatabase:
    """Return the shared database handle.

    Raises `RuntimeError` if `connect()` has not been called yet.
    """
    if _client is None:
        raise RuntimeError(
            "Motor client is not connected. "
            "Ensure connect() was called in the FastAPI lifespan."
        )
    return _client[settings.COSMOS_DATABASE]


# ── Indexes ────────────────────────────────────────────────────────────────

async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create collection indexes idempotently.

    Cosmos DB for MongoDB (RU tier) raises NamespaceExists (code 48)
    if a collection was implicitly created before the index call. The
    index is still created successfully; we swallow that specific code.
    Duplicate-key (11000) is also safe to ignore on unique indexes that
    already exist.
    """

    async def _try(col_name: str, keys: list[tuple], **kwargs: object) -> None:
        try:
            await db[col_name].create_index(keys, **kwargs)
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "code", None)
            if code not in (48, 11000):
                raise

    await asyncio.gather(
        # users
        _try("users", [("username", 1)], unique=True),
        # models
        _try("models", [("projectId", 1), ("updatedAt", -1)]),
        # model_tables
        _try("model_tables", [("modelId", 1)]),
        _try("model_tables", [("modelId", 1), ("schema", 1)]),
        _try("model_tables", [("modelId", 1), ("domain", 1)]),
        # model_relationships
        _try("model_relationships", [("modelId", 1)]),
        # model_views
        _try("model_views", [("modelId", 1)]),
        _try("model_views", [("modelId", 1), ("sourceTableId", 1)]),
    )
    log.info("motor indexes ensured")
