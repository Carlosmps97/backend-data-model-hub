"""CRUD async de `udp_definitions` (las keys UDP). Los VALORES asignados viven en
`canonical_tables`/`canonical_columns` (`udpValues`), no acá."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import UpdateOne

from app.core.db.client import get_db

from .models import UDP_LEVELS, UDP_TYPES, UdpDefinitionDoc

COLL = "udp_definitions"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_doc(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    for k in ("flgactive", "deletedAt", "updatedAt", "createdAt"):
        doc.pop(k, None)
    return doc


def _clean(data: dict) -> dict:
    """Normaliza una definición: tipo/niveles válidos, allowedValues solo si es
    'list'. Puro-ish (no toca DB)."""
    data = dict(data)
    if data.get("dataType") not in UDP_TYPES:
        data["dataType"] = "string"
    data["appliesTo"] = [lv for lv in (data.get("appliesTo") or []) if lv in UDP_LEVELS] or ["table", "column"]
    if data.get("dataType") != "list":
        data["allowedValues"] = []
    return data


async def list_udp() -> list[dict]:
    db = await get_db()
    docs = await db[COLL].find({"flgactive": {"$ne": False}}).to_list(None)
    docs.sort(key=lambda d: (d.get("name") or "").lower())
    return [UdpDefinitionDoc.model_validate(_to_doc(d)).model_dump() for d in docs]


async def create_udp(data: dict) -> dict:
    db = await get_db()
    d = UdpDefinitionDoc.model_validate({**_clean(data), "id": data.get("id") or str(uuid.uuid4())})
    payload = d.model_dump()
    await db[COLL].insert_one(
        {"_id": d.id, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
         **{k: v for k, v in payload.items() if k != "id"}}
    )
    return payload


async def update_udp(udp_id: str, data: dict) -> dict | None:
    from pymongo import ReturnDocument

    db = await get_db()
    data = {k: v for k, v in _clean(data).items() if k not in ("id", "_id")}
    res = await db[COLL].find_one_and_update(
        {"_id": udp_id, "flgactive": {"$ne": False}},
        {"$set": {**data, "updatedAt": _now()}}, return_document=ReturnDocument.AFTER)
    return UdpDefinitionDoc.model_validate(_to_doc(res)).model_dump() if res else None


async def delete_udp(udp_id: str) -> bool:
    db = await get_db()
    res = await db[COLL].update_one({"_id": udp_id, "flgactive": {"$ne": False}},
                                    {"$set": {"flgactive": False, "deletedAt": _now()}})
    return res.modified_count > 0


async def restore_udp(defs: list[dict]) -> None:
    """Rollback: deja SOLO las definiciones del snapshot activas (soft-delete de
    las que no están) y upserta las del snapshot. Espeja `restore_domains`."""
    db = await get_db()
    keep = {d["id"] for d in defs if d.get("id")}
    await db[COLL].update_many({"_id": {"$nin": list(keep)}, "flgactive": {"$ne": False}},
                               {"$set": {"flgactive": False, "deletedAt": _now()}})
    ops = []
    for d in defs:
        did = d.get("id")
        if not did:
            continue
        payload = UdpDefinitionDoc.model_validate(_clean({**d, "id": did})).model_dump()
        ops.append(UpdateOne(
            {"_id": did},
            {"$set": {"flgactive": True, "updatedAt": _now(),
                      **{k: v for k, v in payload.items() if k != "id"}},
             "$setOnInsert": {"createdAt": _now()}}, upsert=True))
    if ops:
        await db[COLL].bulk_write(ops, ordered=False)
