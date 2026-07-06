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
        _try("glossary_terms", [("flgactive", 1)]),
        _try("udp_definitions", [("flgactive", 1)]),
        _try("canonical_tables", [("flgactive", 1)]),
        # REQUERIDO por la búsqueda server-side del catálogo (?q=&limit=): el
        # top-N se ordena en Mongo por physicalName, y Cosmos RU rechaza
        # `.sort()` sobre campos sin índice (500 en todas las búsquedas).
        _try("canonical_tables", [("physicalName", 1)]),
        _try("canonical_columns", [("tableId", 1)]),
        _try("canonical_columns", [("parentDomainId", 1)]),
        # ── Changesets (M2a) ────────────────────────────────────
        _try("changesets", [("updatedAt", -1)]),
        _try("changesets", [("status", 1)]),
        # Un doc POR CAMBIO (sin límite de 2MB/doc): overlay/diff/apply leen
        # por csId (+collection) — el prefijo del compuesto cubre ambos.
        _try("changeset_changes", [("csId", 1), ("collection", 1)]),
        # ── M3a: Projects + Subject Areas + Relationships + Views ──
        _try("projects", [("flgactive", 1)]),
        _try("subject_areas", [("projectId", 1)]),
        _try("relationships", [("flgactive", 1)]),
        # El canvas resuelve relaciones por extremos ($in por tabla).
        _try("relationships", [("sourceTableId", 1)]),
        _try("relationships", [("targetTableId", 1)]),
        _try("views", [("flgactive", 1)]),
        # Las vistas se listan por tabla (PropertiesPanel · tab Views).
        _try("views", [("tableId", 1)]),
        # ── R1a: Folders (jerarquía del Model Explorer) ──────────
        _try("folders", [("projectId", 1)]),
        # ── R1c: naming_config (1 doc por scope; _id = scope) ────
        _try("naming_config", [("scope", 1)]),
        # ── Auth propia + RBAC + auditoría (2026-07-04) ──────────
        _try("users", [("email", 1)]),
        _try("audit_log", [("at", -1)]),
        _try("audit_log", [("actor", 1)]),
        # Data Standards: seq ÚNICO — dos apply/rollback concurrentes no pueden
        # crear dos versiones con el mismo seq/label (el service reintenta ante
        # la colisión). Sirve para ambos sentidos de orden (el app ordena en Python).
        _try("standards_versions", [("seq", 1)], unique=True),
    )
    log.info("motor indexes ensured")
