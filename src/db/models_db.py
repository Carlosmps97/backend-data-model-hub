"""Async CRUD for models, model_tables, model_relationships, model_views.

Mirrors `web-data-model-hub/src/server/db/mongo.ts` — MODELOS section.

Key design decisions carried over from TypeScript:
- `replaceEntities` uses soft-delete + bulkWrite upsert to avoid E11000
  duplicate-key errors on previously soft-deleted documents.
- `get_models(full=False)` returns lightweight stubs (only the count) so
  the dashboard and project views don't pay for full hydration.
- `get_models(full=True)` fetches all child collections in parallel (used
  by the Global Canvas view).
- MongoDB `.sort()` is replaced by Python sort — Cosmos DB RU tier rejects
  `.sort()` on fields without an explicit index.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from pymongo import ReturnDocument
from pymongo.operations import ReplaceOne

from src.db.motor_client import get_db
from src.db.db_models import (
    DataModelDoc,
    RelationshipDoc,
    TableModelDoc,
    ViewModelDoc,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_model_meta(doc: dict) -> dict:
    """Strip Mongo internals, rename `_id` → `id`."""
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    doc.pop("tableCount", None)
    doc.pop("flgactive", None)
    doc.pop("deletedAt", None)
    return doc


def _to_child(doc: dict) -> dict:
    """Strip Mongo internals + `modelId` from a child doc."""
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    doc.pop("modelId", None)
    doc.pop("flgactive", None)
    doc.pop("deletedAt", None)
    doc.pop("updatedAt", None)
    return doc


def _ensure_column_ids(tables: list[dict]) -> list[dict]:
    """Backfill a UUID on any column that reaches the persistence layer
    without one.

    Safety net for the column-id migration: the frontend always stamps
    ids on freshly-created columns, but a request coming from an
    outdated client, a script, or a future import path that forgot to
    mint ids would silently break relationship edges (which now reference
    `TableColumn.id`). This helper is idempotent — columns that already
    have a non-empty string id are left untouched.

    Mutates in place (and returns the same list) because the input is
    constructed per-request from `request.json()`; no outside caller is
    holding a reference to it.
    """
    for table in tables or []:
        for col in table.get("columns") or []:
            cid = col.get("id")
            if not isinstance(cid, str) or not cid:
                col["id"] = str(uuid.uuid4())
    return tables


# ── replaceEntities (bulk upsert) ──────────────────────────────────────────

async def _replace_entities(
    col_name: str,
    model_id: str,
    entities: list[dict],
) -> None:
    """Replace the full set of child entities for a model.

    Algorithm (matches TypeScript `replaceEntities`):
      1. Soft-delete docs whose `_id` is NOT in the incoming payload.
      2. `bulkWrite` with `replaceOne(upsert=True)` for every incoming
         entity — avoids E11000 on previously soft-deleted rows.
    """
    db = await get_db()
    col = db[col_name]
    now = _now()
    incoming_ids = [e["id"] for e in entities]

    await col.update_many(
        {"modelId": model_id, "_id": {"$nin": incoming_ids}},
        {"$set": {"flgactive": False, "deletedAt": now}},
    )

    if not entities:
        return

    ops = []
    for entity in entities:
        eid = entity["id"]
        # Strip id + modelId; modelId is injected explicitly below.
        rest = {k: v for k, v in entity.items() if k not in ("id", "modelId")}
        ops.append(
            ReplaceOne(
                {"_id": eid},
                {
                    "_id": eid,
                    "modelId": model_id,
                    "flgactive": True,
                    "updatedAt": now,
                    **rest,
                },
                upsert=True,
            )
        )
    await col.bulk_write(ops)


# ── CRUD ───────────────────────────────────────────────────────────────────

async def get_models(
    project_id: str | None = None,
    full: bool = False,
) -> list[DataModelDoc]:
    """List models, optionally filtered by project.

    Args:
        project_id: Filter to a single project. `None` returns all projects.
        full:       When True, hydrates tables/relationships/views for each
                    model (3 extra parallel queries). When False (default),
                    returns lightweight stubs with only the table count.
    """
    db = await get_db()
    flt: dict[str, Any] = {"flgactive": {"$ne": False}}
    if project_id:
        flt["projectId"] = project_id

    docs = await db["models"].find(flt).to_list(None)
    docs.sort(key=lambda d: d.get("updatedAt") or "", reverse=True)

    if not docs:
        return []

    model_ids = [str(d["_id"]) for d in docs]

    if full:
        table_docs, rel_docs, view_docs = await asyncio.gather(
            db["model_tables"]
            .find({"modelId": {"$in": model_ids}, "flgactive": {"$ne": False}})
            .to_list(None),
            db["model_relationships"]
            .find({"modelId": {"$in": model_ids}, "flgactive": {"$ne": False}})
            .to_list(None),
            db["model_views"]
            .find({"modelId": {"$in": model_ids}, "flgactive": {"$ne": False}})
            .to_list(None),
        )

        tables_by: dict[str, list] = {}
        rels_by: dict[str, list] = {}
        views_by: dict[str, list] = {}
        for d in table_docs:
            tables_by.setdefault(str(d["modelId"]), []).append(_to_child(d))
        for d in rel_docs:
            rels_by.setdefault(str(d["modelId"]), []).append(_to_child(d))
        for d in view_docs:
            views_by.setdefault(str(d["modelId"]), []).append(_to_child(d))

        result = []
        for doc in docs:
            mid = str(doc["_id"])
            meta = _to_model_meta(doc)
            result.append(
                DataModelDoc.model_validate(
                    {
                        **meta,
                        "tables": tables_by.get(mid, []),
                        "relationships": rels_by.get(mid, []),
                        "views": views_by.get(mid, []),
                    }
                )
            )
        return result

    # Stub mode — only the count, not the real table data.
    pipeline = [
        {"$match": {"modelId": {"$in": model_ids}, "flgactive": {"$ne": False}}},
        {"$group": {"_id": "$modelId", "count": {"$sum": 1}}},
    ]
    count_by_model: dict[str, int] = {
        str(c["_id"]): c["count"]
        async for c in db["model_tables"].aggregate(pipeline)
    }

    result = []
    for doc in docs:
        mid = str(doc["_id"])
        count = count_by_model.get(mid, 0)
        meta = _to_model_meta(doc)
        stubs = [
            {"id": f"stub-{i}", "name": "", "columns": [], "schema": None}
            for i in range(count)
        ]
        result.append(
            DataModelDoc.model_validate({**meta, "tables": stubs, "relationships": [], "views": []})
        )
    return result


async def get_model(model_id: str) -> DataModelDoc | None:
    """Fetch a single model with all child entities hydrated."""
    db = await get_db()
    model_doc, table_docs, rel_docs, view_docs = await asyncio.gather(
        db["models"].find_one({"_id": model_id, "flgactive": {"$ne": False}}),
        db["model_tables"]
        .find({"modelId": model_id, "flgactive": {"$ne": False}})
        .to_list(None),
        db["model_relationships"]
        .find({"modelId": model_id, "flgactive": {"$ne": False}})
        .to_list(None),
        db["model_views"]
        .find({"modelId": model_id, "flgactive": {"$ne": False}})
        .to_list(None),
    )
    if not model_doc:
        return None
    meta = _to_model_meta(model_doc)
    return DataModelDoc.model_validate(
        {
            **meta,
            "tables": [_to_child(d) for d in table_docs],
            "relationships": [_to_child(d) for d in rel_docs],
            "views": [_to_child(d) for d in view_docs],
        }
    )


async def create_model(model: DataModelDoc) -> DataModelDoc:
    db = await get_db()
    # by_alias=True ensures "schema" (not "sql_schema") is written to MongoDB.
    tables = [t.model_dump(by_alias=True) for t in model.tables]
    rels = [r.model_dump(by_alias=True) for r in model.relationships]
    views = [v.model_dump(by_alias=True) for v in model.views]
    domain_catalog = [d.model_dump(by_alias=True) for d in model.domainCatalog]

    # Safety net: pydantic default_factory already stamped ids during
    # `.model_validate`, but we re-check the serialised dict in case a
    # future code path constructs `model` through a different shape
    # (e.g. direct dict injection).
    _ensure_column_ids(tables)

    await db["models"].insert_one(
        {
            "_id": model.id,
            "projectId": model.projectId,
            "name": model.name,
            "description": model.description,
            "engine": model.engine,
            "domainCatalog": domain_catalog,
            "tableCount": len(tables),
            "createdAt": model.createdAt,
            "updatedAt": model.updatedAt or _now(),
            "flgactive": True,
        }
    )
    await asyncio.gather(
        _replace_entities("model_tables", model.id, tables),
        _replace_entities("model_relationships", model.id, rels),
        _replace_entities("model_views", model.id, views),
    )
    return model


async def update_model(
    model_id: str,
    updates: dict[str, Any],
) -> DataModelDoc | None:
    """Partial update. Only the keys present in `updates` are changed.

    Child arrays (tables/relationships/views) are replaced in full if
    present in `updates`; otherwise left untouched.
    """
    db = await get_db()
    exists = await db["models"].find_one({"_id": model_id}, {"_id": 1})
    if not exists:
        return None

    tables = updates.pop("tables", None)
    rels = updates.pop("relationships", None)
    views = updates.pop("views", None)
    domain_catalog = updates.pop("domainCatalog", None)
    updates.pop("id", None)

    # Safety net — stamp uuids on any column missing `id` so edges keep
    # pointing at valid handles after the write. Idempotent.
    if tables is not None:
        _ensure_column_ids(tables)

    meta_patch: dict[str, Any] = {**updates, "updatedAt": _now()}
    if domain_catalog is not None:
        meta_patch["domainCatalog"] = domain_catalog
    if tables is not None:
        meta_patch["tableCount"] = len(tables)

    await db["models"].update_one({"_id": model_id}, {"$set": meta_patch})

    child_ops = []
    if tables is not None:
        child_ops.append(_replace_entities("model_tables", model_id, tables))
    if rels is not None:
        child_ops.append(_replace_entities("model_relationships", model_id, rels))
    if views is not None:
        child_ops.append(_replace_entities("model_views", model_id, views))
    if child_ops:
        await asyncio.gather(*child_ops)

    return await get_model(model_id)


async def delete_model(model_id: str) -> bool:
    """Soft-delete model and all its child entities."""
    db = await get_db()
    now = _now()
    await asyncio.gather(
        db["model_tables"].update_many(
            {"modelId": model_id},
            {"$set": {"flgactive": False, "deletedAt": now}},
        ),
        db["model_relationships"].update_many(
            {"modelId": model_id},
            {"$set": {"flgactive": False, "deletedAt": now}},
        ),
        db["model_views"].update_many(
            {"modelId": model_id},
            {"$set": {"flgactive": False, "deletedAt": now}},
        ),
    )
    result = await db["models"].update_one(
        {"_id": model_id},
        {"$set": {"flgactive": False, "deletedAt": now, "updatedAt": now}},
    )
    return result.modified_count > 0
