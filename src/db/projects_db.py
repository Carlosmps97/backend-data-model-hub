"""Async CRUD for the `projects` collection (Motor / Cosmos DB).

Mirrors `web-data-model-hub/src/server/db/mongo.ts` — PROYECTOS section.
Soft-delete: `flgactive: false` + `deletedAt` (never hard-deletes).
Cascade: delete cascades to all models via `models_db.delete_model`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pymongo import ReturnDocument

from src.db.motor_client import get_db
from src.db.db_models import ProjectDoc


# ── Helpers ────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_project(doc: dict) -> ProjectDoc:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return ProjectDoc.model_validate(doc)


# ── CRUD ───────────────────────────────────────────────────────────────────

async def get_projects() -> list[ProjectDoc]:
    db = await get_db()
    docs = await db["projects"].find({"flgactive": {"$ne": False}}).to_list(None)
    # Cosmos DB RU rejects .sort() on unindexed fields — sort in Python.
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
    """Soft-delete the project and cascade to all its models."""
    # Import here to avoid circular dependency at module load time.
    from src.db.models_db import delete_model  # noqa: PLC0415

    db = await get_db()
    now = _now()

    # Cascade: soft-delete every model that belongs to this project.
    model_docs = await db["models"].find(
        {"projectId": project_id}, {"_id": 1}
    ).to_list(None)
    for m in model_docs:
        await delete_model(str(m["_id"]))

    result = await db["projects"].update_one(
        {"_id": project_id},
        {"$set": {"flgactive": False, "deletedAt": now, "updatedAt": now}},
    )
    return result.modified_count > 0
