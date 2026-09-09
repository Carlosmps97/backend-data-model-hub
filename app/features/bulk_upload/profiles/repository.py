"""CRUD async de `upload_profiles` (doc 78 §3.1). Alcance por proyecto en TODA
lectura/escritura (`scoped`); soft-delete; `set_default` desmarca al resto."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.core.db.client import get_db
from app.core.scope import scoped

from .models import UploadProfileDoc

COLL = "upload_profiles"
_AUDIT = ("flgactive", "deletedAt", "createdAt", "updatedAt")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_doc(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    meta = {k: doc.pop(k, None) for k in _AUDIT}
    out = UploadProfileDoc.model_validate(doc).model_dump()
    out["createdAt"], out["updatedAt"] = meta["createdAt"], meta["updatedAt"]
    return out


async def list_profiles(project_id: str) -> list[dict]:
    """Perfiles activos DEL PROYECTO: el default primero, luego por nombre."""
    db = await get_db()
    docs = await db[COLL].find(scoped(project_id, {"flgactive": {"$ne": False}})).to_list(None)
    out = [_to_doc(d) for d in docs]
    out.sort(key=lambda p: (not p["isDefault"], (p["name"] or "").lower()))
    return out


async def get_profile(project_id: str, profile_id: str) -> dict | None:
    db = await get_db()
    doc = await db[COLL].find_one(scoped(project_id, {"_id": profile_id, "flgactive": {"$ne": False}}))
    return _to_doc(doc) if doc else None


async def create_profile(project_id: str, data: dict) -> dict:
    db = await get_db()
    d = UploadProfileDoc.model_validate({**data, "projectId": project_id, "id": data.get("id") or str(uuid.uuid4())})
    payload = d.model_dump()
    now = _now()
    await db[COLL].insert_one({"_id": d.id, "flgactive": True, "createdAt": now, "updatedAt": now,
                               **{k: v for k, v in payload.items() if k != "id"}})
    return {**payload, "createdAt": now, "updatedAt": now}


async def update_profile(project_id: str, profile_id: str, data: dict) -> dict | None:
    db = await get_db()
    d = UploadProfileDoc.model_validate({**data, "projectId": project_id, "id": profile_id}).model_dump()
    sets = {k: v for k, v in d.items() if k not in ("id", "createdBy")}
    res = await db[COLL].find_one_and_update(
        scoped(project_id, {"_id": profile_id, "flgactive": {"$ne": False}}),
        {"$set": {**sets, "updatedAt": _now()}}, return_document=ReturnDocument.AFTER)
    return _to_doc(res) if res else None


async def delete_profile(project_id: str, profile_id: str) -> bool:
    db = await get_db()
    res = await db[COLL].update_one(scoped(project_id, {"_id": profile_id, "flgactive": {"$ne": False}}),
                                    {"$set": {"flgactive": False, "isDefault": False, "deletedAt": _now()}})
    return res.modified_count > 0


async def set_default(project_id: str, profile_id: str) -> dict | None:
    """Marca `profile_id` como default del proyecto y desmarca a los demás."""
    db = await get_db()
    await db[COLL].update_many(scoped(project_id, {"_id": {"$ne": profile_id}, "isDefault": True}),
                               {"$set": {"isDefault": False, "updatedAt": _now()}})
    res = await db[COLL].find_one_and_update(
        scoped(project_id, {"_id": profile_id, "flgactive": {"$ne": False}}),
        {"$set": {"isDefault": True, "updatedAt": _now()}}, return_document=ReturnDocument.AFTER)
    return _to_doc(res) if res else None
