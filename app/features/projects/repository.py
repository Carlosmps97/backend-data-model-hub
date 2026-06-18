"""CRUD async de la colección `projects` (Motor / Cosmos DB).

Soft-delete: `flgactive: false` + `deletedAt` (nunca borra físico). El borrado
cascadea al canvas del proyecto vía `canvas.repository.delete_canvas`.

Un proyecto embebe sus arrays `engines` / `layers` (ModelLevels) / `domains`; los
datos hijos pesados (tablas + relaciones) viven en `project_tables` /
`project_relationships` (ver la feature `canvas`).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.core.db.client import get_db

from .models import ProjectDoc


# ── Helpers ────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def generate_project_id(name: str) -> str:
    """Slug legible usado como `_id` del proyecto (p. ej. "programa-ia-credicorp"),
    para que las URLs lean `/projects/<slug>/…` en vez de un UUID. Cae a `project`
    y agrega `-2`, `-3`… ante colisión con un id existente."""
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "project"
    db = await get_db()
    if not await db["projects"].find_one({"_id": base}, {"_id": 1}):
        return base
    n = 2
    while await db["projects"].find_one({"_id": f"{base}-{n}"}, {"_id": 1}):
        n += 1
    return f"{base}-{n}"


def _to_project(doc: dict) -> ProjectDoc:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return ProjectDoc.model_validate(doc)


# ── CRUD ───────────────────────────────────────────────────────────────────

async def get_projects() -> list[ProjectDoc]:
    db = await get_db()
    docs = await db["projects"].find({"flgactive": {"$ne": False}}).to_list(None)
    # Cosmos DB RU rechaza .sort() en campos sin índice — ordenar en Python.
    docs.sort(key=lambda d: d.get("updatedAt") or "", reverse=True)
    return [_to_project(d) for d in docs]


async def get_project(project_id: str) -> ProjectDoc | None:
    db = await get_db()
    doc = await db["projects"].find_one(
        {"_id": project_id, "flgactive": {"$ne": False}}
    )
    return _to_project(doc) if doc else None


async def create_project(project: ProjectDoc) -> ProjectDoc:
    db = await get_db()
    data = project.model_dump(exclude={"id"})
    await db["projects"].insert_one({"_id": project.id, **data})
    return project


async def update_project(
    project_id: str,
    updates: dict,
) -> ProjectDoc | None:
    db = await get_db()
    updates.pop("id", None)
    updates["updatedAt"] = _now()
    result = await db["projects"].find_one_and_update(
        {"_id": project_id},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    return _to_project(result) if result else None


async def delete_project(project_id: str) -> bool:
    """Soft-delete del proyecto + cascada a su canvas (tablas + relaciones)."""
    # Import local para no acoplar a load-time entre features.
    from app.features.canvas.repository import delete_canvas  # noqa: PLC0415

    db = await get_db()
    now = _now()

    # Cascada: soft-delete de toda tabla + relación del proyecto.
    await delete_canvas(project_id)

    result = await db["projects"].update_one(
        {"_id": project_id},
        {"$set": {"flgactive": False, "deletedAt": now, "updatedAt": now}},
    )
    return result.modified_count > 0
