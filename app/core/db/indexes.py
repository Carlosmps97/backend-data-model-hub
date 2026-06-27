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
        # ── Modelo canónico (M1) ────────────────────────────────
        _try("parent_domains", [("flgactive", 1)]),
        _try("abbreviation_dict", [("flgactive", 1)]),
        _try("canonical_tables", [("flgactive", 1)]),
        _try("canonical_columns", [("tableId", 1)]),
        _try("canonical_columns", [("parentDomainId", 1)]),
        # ── Changesets (M2a) ────────────────────────────────────
        _try("changesets", [("updatedAt", -1)]),
        # ── M3a: Projects + Subject Areas + Relationships + Views ──
        _try("projects", [("flgactive", 1)]),
        _try("subject_areas", [("projectId", 1)]),
        _try("relationships", [("flgactive", 1)]),
        _try("views", [("flgactive", 1)]),
        # ── R1a: Folders (jerarquía del Model Explorer) ──────────
        _try("folders", [("projectId", 1)]),
        # ── R1c: naming_config (1 doc por scope; _id = scope) ────
        _try("naming_config", [("scope", 1)]),
    )
    log.info("motor indexes ensured")
