"""CRUD async de `views`."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.core.db.client import get_db

from .models import ViewDoc

COLL = "views"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to(doc: dict) -> dict:
    doc = dict(doc); doc["id"] = str(doc.pop("_id"))
    for k in ("flgactive", "deletedAt", "updatedAt", "createdAt"):
        doc.pop(k, None)
    return doc


def _dump(v: ViewDoc) -> dict:
    # by_alias: `schema` viaja como `schema` (no `sql_schema`), igual que catalog.
    return v.model_dump(by_alias=True)


def build_query(table_id: str | None = None, table_ids: list[str] | None = None) -> dict:
    """Query de `list_all`. Pura (testeable sin DB).

    F3: el match canónico es contra `sourceTableIds` (igualdad sobre array =
    contains; `$in` = intersección). El OR con `tableId` cubre docs legacy
    aún no migrados por `scripts/migrate_view_source_tables.py`."""
    query: dict = {"flgactive": {"$ne": False}}
    if table_id is not None:
        query["$or"] = [{"sourceTableIds": table_id}, {"tableId": table_id}]
    elif table_ids:
        query["$or"] = [{"sourceTableIds": {"$in": table_ids}},
                        {"tableId": {"$in": table_ids}}]
    return query


async def list_all(table_id: str | None = None, table_ids: list[str] | None = None) -> list[dict]:
    db = await get_db()
    docs = await db[COLL].find(build_query(table_id, table_ids)).to_list(None)
    return [_dump(ViewDoc.model_validate(_to(d))) for d in docs]


async def create(data: dict) -> dict:
    db = await get_db()
    v = ViewDoc.model_validate({**data, "id": data.get("id") or str(uuid.uuid4())})
    payload = _dump(v)
    await db[COLL].insert_one({"_id": v.id, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
                               **{k: val for k, val in payload.items() if k != "id"}})
    return payload


async def update(vid: str, data: dict) -> dict | None:
    db = await get_db()
    data = {k: v for k, v in data.items() if k not in ("id", "_id")}
    res = await db[COLL].find_one_and_update(
        {"_id": vid, "flgactive": {"$ne": False}}, {"$set": {**data, "updatedAt": _now()}},
        return_document=ReturnDocument.AFTER)
    return _dump(ViewDoc.model_validate(_to(res))) if res else None


async def delete(vid: str) -> bool:
    db = await get_db()
    res = await db[COLL].update_one({"_id": vid, "flgactive": {"$ne": False}},
                                    {"$set": {"flgactive": False, "deletedAt": _now()}})
    return res.modified_count > 0
