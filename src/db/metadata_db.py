"""Async CRUD for the transversal UDP / Semantic Type catalog.

Two global collections — `semantic_types` and `udps` — hold organization-wide
governance metadata assignable to any project's tables/columns (Feature 3).
Unlike the project canvas these are NOT sharded by project: they are small and
read by every project's table editor. Soft-delete (`flgactive: false`) like the
rest of the platform.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument

from src.db.motor_client import get_db
from src.db.db_models import SemanticTypeDoc, UdpDoc

SEMANTIC_TYPES = "semantic_types"
UDPS = "udps"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_doc(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    doc.pop("flgactive", None)
    doc.pop("deletedAt", None)
    doc.pop("updatedAt", None)
    doc.pop("createdAt", None)
    return doc


# ── Semantic Types ───────────────────────────────────────────────────────────

async def list_semantic_types() -> list[dict]:
    db = await get_db()
    docs = await db[SEMANTIC_TYPES].find({"flgactive": {"$ne": False}}).to_list(None)
    docs.sort(key=lambda d: (d.get("name") or "").lower())
    return [SemanticTypeDoc.model_validate(_to_doc(d)).model_dump() for d in docs]


async def create_semantic_type(data: dict) -> dict:
    db = await get_db()
    st = SemanticTypeDoc.model_validate({**data, "id": data.get("id") or str(uuid.uuid4())})
    payload = st.model_dump()
    await db[SEMANTIC_TYPES].insert_one(
        {"_id": st.id, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
         **{k: v for k, v in payload.items() if k != "id"}}
    )
    return payload


async def update_semantic_type(st_id: str, data: dict) -> dict | None:
    db = await get_db()
    data = {k: v for k, v in data.items() if k not in ("id", "_id")}
    res = await db[SEMANTIC_TYPES].find_one_and_update(
        {"_id": st_id, "flgactive": {"$ne": False}},
        {"$set": {**data, "updatedAt": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    return SemanticTypeDoc.model_validate(_to_doc(res)).model_dump() if res else None


async def delete_semantic_type(st_id: str) -> bool:
    db = await get_db()
    res = await db[SEMANTIC_TYPES].update_one(
        {"_id": st_id}, {"$set": {"flgactive": False, "deletedAt": _now()}}
    )
    return res.modified_count > 0


# ── UDPs ─────────────────────────────────────────────────────────────────────

async def list_udps() -> list[dict]:
    db = await get_db()
    docs = await db[UDPS].find({"flgactive": {"$ne": False}}).to_list(None)
    docs.sort(key=lambda d: (d.get("name") or "").lower())
    return [UdpDoc.model_validate(_to_doc(d)).model_dump() for d in docs]


async def create_udp(data: dict) -> dict:
    db = await get_db()
    u = UdpDoc.model_validate({**data, "id": data.get("id") or str(uuid.uuid4())})
    payload = u.model_dump()
    await db[UDPS].insert_one(
        {"_id": u.id, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
         **{k: v for k, v in payload.items() if k != "id"}}
    )
    return payload


async def update_udp(udp_id: str, data: dict) -> dict | None:
    db = await get_db()
    data = {k: v for k, v in data.items() if k not in ("id", "_id")}
    res = await db[UDPS].find_one_and_update(
        {"_id": udp_id, "flgactive": {"$ne": False}},
        {"$set": {**data, "updatedAt": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    return UdpDoc.model_validate(_to_doc(res)).model_dump() if res else None


async def delete_udp(udp_id: str) -> bool:
    db = await get_db()
    res = await db[UDPS].update_one(
        {"_id": udp_id}, {"$set": {"flgactive": False, "deletedAt": _now()}}
    )
    return res.modified_count > 0
