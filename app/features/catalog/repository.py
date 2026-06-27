"""CRUD async de `canonical_tables` + `canonical_columns` (col. separada)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.core.db.client import get_db

from .models import CanonicalColumnDoc, CanonicalTableDoc

TABLES = "canonical_tables"
COLUMNS = "canonical_columns"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_doc(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    for k in ("flgactive", "deletedAt", "updatedAt", "createdAt"):
        doc.pop(k, None)
    return doc


async def list_tables() -> list[dict]:
    db = await get_db()
    docs = await db[TABLES].find({"flgactive": {"$ne": False}}).to_list(None)
    docs.sort(key=lambda d: (d.get("physicalName") or "").lower())
    return [CanonicalTableDoc.model_validate(_to_doc(d)).model_dump(by_alias=True) for d in docs]


async def create_table(data: dict) -> dict:
    db = await get_db()
    t = CanonicalTableDoc.model_validate({**data, "id": data.get("id") or str(uuid.uuid4())})
    payload = t.model_dump(by_alias=True)
    await db[TABLES].insert_one(
        {"_id": t.id, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
         **{k: v for k, v in payload.items() if k != "id"}}
    )
    return payload


async def list_columns(table_id: str) -> list[dict]:
    db = await get_db()
    docs = await db[COLUMNS].find(
        {"tableId": table_id, "flgactive": {"$ne": False}}
    ).to_list(None)
    docs.sort(key=lambda d: d.get("ordinal") or 0)
    return [CanonicalColumnDoc.model_validate(_to_doc(d)).model_dump() for d in docs]


async def create_column(data: dict) -> dict:
    db = await get_db()
    c = CanonicalColumnDoc.model_validate({**data, "id": data.get("id") or str(uuid.uuid4())})
    payload = c.model_dump()
    await db[COLUMNS].insert_one(
        {"_id": c.id, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
         **{k: v for k, v in payload.items() if k != "id"}}
    )
    return payload


async def domain_default(parent_domain_id: str) -> str | None:
    db = await get_db()
    d = await db["parent_domains"].find_one({"_id": parent_domain_id}, {"defaultDataType": 1})
    return d.get("defaultDataType") if d else None
