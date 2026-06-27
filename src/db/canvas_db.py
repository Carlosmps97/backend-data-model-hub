"""Async CRUD for a project's canvas: `project_tables` + `project_relationships`.

Project-centric architecture (Aurora refactor) — replaces the old `models_db`.
Each project owns a single scoped canvas; tables and relationships are sharded
by `projectId` so all of a project's entities live on the same physical shard.

Key design decisions carried over from the previous `models_db`:
- `_replace_entities` uses soft-delete + bulkWrite upsert to avoid E11000
  duplicate-key errors on previously soft-deleted documents.
- Views are NOT a separate collection anymore — they are embedded in each
  table's `views[]` (role-specific SQL projections).
- `position` is patched via the dedicated `bulk_update_positions` (only tables
  are canvas nodes now; views render inside the Inspector, not on the canvas).
- MongoDB `.sort()` is avoided — Cosmos DB RU tier rejects `.sort()` on fields
  without an explicit index.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from pymongo.operations import ReplaceOne

from src.db.motor_client import get_db
from src.db.db_models import RelationshipDoc, TableDoc

TABLES = "project_tables"
RELATIONSHIPS = "project_relationships"


# ── Helpers ────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_child(doc: dict) -> dict:
    """Strip Mongo internals + `projectId` from a child doc."""
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    doc.pop("projectId", None)
    doc.pop("flgactive", None)
    doc.pop("deletedAt", None)
    doc.pop("updatedAt", None)
    return doc


def _ensure_column_ids(tables: list[dict]) -> list[dict]:
    """Backfill a UUID on any column that reaches the persistence layer
    without one. Idempotent — keeps relationship edges (which reference
    `TableColumn.id`) valid even if a client forgot to mint ids."""
    for table in tables or []:
        for col in table.get("columns") or []:
            cid = col.get("id")
            if not isinstance(cid, str) or not cid:
                col["id"] = str(uuid.uuid4())
    return tables


# ── replaceEntities (bulk upsert) ──────────────────────────────────────────

async def _replace_entities(
    col_name: str,
    project_id: str,
    entities: list[dict],
) -> None:
    """Replace the full set of child entities for a project.

      1. Soft-delete docs whose `_id` is NOT in the incoming payload.
      2. `bulkWrite` with `replaceOne(upsert=True)` for every incoming entity.
    """
    db = await get_db()
    col = db[col_name]
    now = _now()
    incoming_ids = [e["id"] for e in entities]

    await col.update_many(
        {"projectId": project_id, "_id": {"$nin": incoming_ids}},
        {"$set": {"flgactive": False, "deletedAt": now}},
    )

    if not entities:
        return

    ops = []
    for entity in entities:
        eid = entity["id"]
        rest = {k: v for k, v in entity.items() if k not in ("id", "projectId")}
        ops.append(
            ReplaceOne(
                {"_id": eid},
                {
                    "_id": eid,
                    "projectId": project_id,
                    "flgactive": True,
                    "updatedAt": now,
                    **rest,
                },
                upsert=True,
            )
        )
    await col.bulk_write(ops)


# ── CRUD ───────────────────────────────────────────────────────────────────

async def get_canvas(project_id: str) -> dict[str, list[dict]]:
    """Fetch a project's hydrated canvas: `{tables, relationships}`.

    Both child collections live on the same shard (`projectId`), so the two
    queries hit the same physical node.
    """
    db = await get_db()
    table_docs, rel_docs = await asyncio.gather(
        db[TABLES]
        .find({"projectId": project_id, "flgactive": {"$ne": False}})
        .to_list(None),
        db[RELATIONSHIPS]
        .find({"projectId": project_id, "flgactive": {"$ne": False}})
        .to_list(None),
    )
    tables = [
        TableDoc.model_validate(_to_child(d)).model_dump(by_alias=True)
        for d in table_docs
    ]
    relationships = [
        RelationshipDoc.model_validate(_to_child(d)).model_dump(by_alias=True)
        for d in rel_docs
    ]
    return {"tables": tables, "relationships": relationships}


async def replace_canvas(
    project_id: str,
    tables: list[dict[str, Any]] | None = None,
    relationships: list[dict[str, Any]] | None = None,
) -> dict[str, list[dict]] | None:
    """Replace the project's tables and/or relationships in full.

    Returns the freshly hydrated canvas, or None if the project doesn't exist.
    Child arrays absent from the call are left untouched.
    """
    db = await get_db()
    exists = await db["projects"].find_one({"_id": project_id}, {"_id": 1})
    if not exists:
        return None

    ops = []
    if tables is not None:
        _ensure_column_ids(tables)
        ops.append(_replace_entities(TABLES, project_id, tables))
    if relationships is not None:
        ops.append(_replace_entities(RELATIONSHIPS, project_id, relationships))
    if ops:
        await asyncio.gather(*ops)

    await db["projects"].update_one(
        {"_id": project_id}, {"$set": {"updatedAt": _now()}}
    )
    return await get_canvas(project_id)


async def bulk_update_positions(
    project_id: str,
    table_positions: dict[str, dict[str, float]] | None = None,
) -> dict[str, int]:
    """Patch only the `position` field on the given tables.

    Used by `PATCH /api/projects/{id}/positions` for fast persistence of the
    ELK-computed layout and user drags, without rewriting the full canvas.
    """
    db = await get_db()
    now = _now()
    tables_matched = 0

    if table_positions:
        tasks = []
        for tid, pos in table_positions.items():
            if not isinstance(pos, dict):
                continue
            x = pos.get("x")
            y = pos.get("y")
            if not (isinstance(x, (int, float)) and isinstance(y, (int, float))):
                continue
            tasks.append(
                db[TABLES].update_one(
                    {"_id": tid, "projectId": project_id, "flgactive": {"$ne": False}},
                    {"$set": {"position": {"x": float(x), "y": float(y)}, "updatedAt": now}},
                )
            )
        if tasks:
            results = await asyncio.gather(*tasks)
            tables_matched = sum(r.matched_count for r in results)

    if tables_matched:
        await db["projects"].update_one({"_id": project_id}, {"$set": {"updatedAt": now}})

    return {"tables": tables_matched}


async def delete_canvas(project_id: str) -> None:
    """Soft-delete every table and relationship of a project (used by the
    project cascade delete)."""
    db = await get_db()
    now = _now()
    await asyncio.gather(
        db[TABLES].update_many(
            {"projectId": project_id},
            {"$set": {"flgactive": False, "deletedAt": now}},
        ),
        db[RELATIONSHIPS].update_many(
            {"projectId": project_id},
            {"$set": {"flgactive": False, "deletedAt": now}},
        ),
    )
