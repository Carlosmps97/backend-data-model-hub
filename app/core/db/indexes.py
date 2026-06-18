"""Índices de colecciones (idempotentes).

Cosmos DB for MongoDB (tier RU) levanta NamespaceExists (code 48) si una
colección fue creada implícitamente antes de la llamada al índice. El índice
igual se crea correctamente; tragamos ese código puntual. Duplicate-key
(11000) también es seguro de ignorar en índices únicos ya existentes.
"""

from __future__ import annotations

import asyncio
import logging

from motor.motor_asyncio import AsyncIOMotorDatabase

log = logging.getLogger(__name__)


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Crea los índices de las colecciones del backend de plataforma."""

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
        # projects
        _try("projects", [("updatedAt", -1)]),
        # project_tables (shard key projectId)
        _try("project_tables", [("projectId", 1)]),
        _try("project_tables", [("projectId", 1), ("layer", 1)]),
        _try("project_tables", [("projectId", 1), ("domain", 1)]),
        # project_relationships (shard key projectId)
        _try("project_relationships", [("projectId", 1)]),
        # UDP / Semantic Type catalog (transversal, global)
        _try("semantic_types", [("flgactive", 1)]),
        _try("udps", [("flgactive", 1)]),
    )
    log.info("motor indexes ensured")
