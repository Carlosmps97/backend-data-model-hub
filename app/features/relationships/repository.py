"""CRUD async de `relationships`."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.core.db.client import get_db

from .models import RelationshipDoc

COLL = "relationships"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to(doc: dict) -> dict:
    doc = dict(doc); doc["id"] = str(doc.pop("_id"))
    for k in ("flgactive", "deletedAt", "updatedAt", "createdAt"):
        doc.pop(k, None)
    return doc


async def list_all() -> list[dict]:
    db = await get_db()
    docs = await db[COLL].find({"flgactive": {"$ne": False}}).to_list(None)
    return [RelationshipDoc.model_validate(_to(d)).model_dump() for d in docs]


async def create(data: dict) -> dict:
    db = await get_db()
    r = RelationshipDoc.model_validate({**data, "id": data.get("id") or str(uuid.uuid4())})
    await db[COLL].insert_one({"_id": r.id, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
                               **{k: v for k, v in r.model_dump().items() if k != "id"}})
    return r.model_dump()


async def update(rid: str, data: dict) -> dict | None:
    db = await get_db()
    data = {k: v for k, v in data.items() if k not in ("id", "_id")}
    res = await db[COLL].find_one_and_update(
        {"_id": rid, "flgactive": {"$ne": False}}, {"$set": {**data, "updatedAt": _now()}},
        return_document=ReturnDocument.AFTER)
    return RelationshipDoc.model_validate(_to(res)).model_dump() if res else None


async def delete(rid: str) -> bool:
    db = await get_db()
    res = await db[COLL].update_one({"_id": rid}, {"$set": {"flgactive": False, "deletedAt": _now()}})
    return res.modified_count > 0
