"""Lecturas de solo lectura para `summary` (único punto que toca el store en
esta feature; guardrail `tests/architecture/test_store_boundary.py`).

Sólo cuenta/lee documentos ACTIVOS (`flgactive != False`), igual que el resto
de repositorios. Para el alcance por proyecto se leen proyecciones mínimas
(sólo los campos necesarios para los contadores) para no traer documentos
completos.
"""
from __future__ import annotations

from app.core.db.client import get_db

ACTIVE = {"flgactive": {"$ne": False}}


async def count_active(collection: str) -> int:
    """Nº de documentos activos de una colección."""
    db = await get_db()
    return await db[collection].count_documents(ACTIVE)


async def subject_areas_for_project(project_id: str) -> list[dict]:
    """Canvases activos del proyecto (sólo `tableIds`, que es lo que se agrega)."""
    db = await get_db()
    docs = await db["subject_areas"].find(
        {"projectId": project_id, **ACTIVE}, {"tableIds": 1}
    ).to_list(None)
    return [{"tableIds": d.get("tableIds") or []} for d in docs]


async def views_with_table() -> list[dict]:
    """Vistas activas con su `tableId` (puede faltar: vista no atada a tabla)."""
    db = await get_db()
    docs = await db["views"].find(ACTIVE, {"tableId": 1}).to_list(None)
    return [{"tableId": d.get("tableId")} for d in docs]


async def relationships_min() -> list[dict]:
    """Relaciones activas con sólo las dos puntas (parent/child tableId; los
    campos legacy source/target cubren docs pre-backfill)."""
    db = await get_db()
    docs = await db["relationships"].find(
        ACTIVE, {"parentTableId": 1, "childTableId": 1,
                 "sourceTableId": 1, "targetTableId": 1}
    ).to_list(None)
    return [
        {"parentTableId": d.get("parentTableId") or d.get("targetTableId"),
         "childTableId": d.get("childTableId") or d.get("sourceTableId")}
        for d in docs
    ]
