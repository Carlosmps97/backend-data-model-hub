"""Lecturas de solo lectura para `summary` (único punto que toca el store en
esta feature; guardrail `tests/architecture/test_store_boundary.py`).

Sólo cuenta documentos ACTIVOS (`flgactive != False`), igual que el resto de
repositorios. Doc 75 D6: el alcance de un proyecto es el `projectId` de cada
documento (columna generada `project_id`), no la unión de canvases.
"""
from __future__ import annotations

from app.core.db.client import get_db
from app.core.scope import scoped

ACTIVE = {"flgactive": {"$ne": False}}


async def count_active(collection: str) -> int:
    """Nº de documentos activos de una colección."""
    db = await get_db()
    return await db[collection].count_documents(ACTIVE)


async def count_scoped(project_id: str, collection: str) -> int:
    """Nº de documentos activos DEL PROYECTO en `collection`."""
    db = await get_db()
    return await db[collection].count_documents(scoped(project_id, ACTIVE))
