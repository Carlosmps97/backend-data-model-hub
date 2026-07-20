"""CRUD async de `schemas` (soft-delete por `flgactive`, patrón projects)."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.core.db.client import get_db

from .models import SchemaDoc

SCHEMAS = "schemas"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    for k in ("flgactive", "deletedAt", "updatedAt", "createdAt"):
        doc.pop(k, None)
    return doc


async def list_schemas() -> list[dict]:
    db = await get_db()
    docs = await db[SCHEMAS].find({"flgactive": {"$ne": False}}).to_list(None)
    docs.sort(key=lambda d: (d.get("name") or "").lower())
    return [SchemaDoc.model_validate(_to(d)).model_dump() for d in docs]


async def get_schema(sid: str) -> dict | None:
    db = await get_db()
    doc = await db[SCHEMAS].find_one({"_id": sid, "flgactive": {"$ne": False}})
    return SchemaDoc.model_validate(_to(doc)).model_dump() if doc else None


async def find_by_name(name: str) -> dict | None:
    """Esquema ACTIVO por nombre exacto case-insensitive (chequeo de unicidad:
    Cosmos no permite índices únicos sobre colecciones pobladas — la garantía
    vive en el service, mismo criterio que canonical_tables)."""
    db = await get_db()
    doc = await db[SCHEMAS].find_one({
        "flgactive": {"$ne": False},
        "name": {"$regex": f"^{re.escape(name)}$", "$options": "i"},
    })
    return SchemaDoc.model_validate(_to(doc)).model_dump() if doc else None


async def usage_count(name: str) -> int:
    """Tablas + vistas PUBLICADAS activas que usan el esquema (gate del delete
    directo sin changeset; el flujo de changeset computa el uso EFECTIVO)."""
    db = await get_db()
    flt = {"flgactive": {"$ne": False}, "schema": name}
    tables = await db["canonical_tables"].count_documents(flt)
    views = await db["views"].count_documents(flt)
    return tables + views


async def create_schema(data: dict) -> dict:
    db = await get_db()
    s = SchemaDoc.model_validate({**data, "id": data.get("id") or str(uuid.uuid4())})
    await db[SCHEMAS].insert_one({"_id": s.id, "flgactive": True, "createdAt": _now(),
                                  "updatedAt": _now(),
                                  **{k: v for k, v in s.model_dump().items() if k != "id"}})
    return s.model_dump()


async def update_schema(sid: str, data: dict) -> dict | None:
    db = await get_db()
    data = {k: v for k, v in data.items() if k not in ("id", "_id")}
    res = await db[SCHEMAS].find_one_and_update(
        {"_id": sid, "flgactive": {"$ne": False}},
        {"$set": {**data, "updatedAt": _now()}},
        return_document=ReturnDocument.AFTER)
    return SchemaDoc.model_validate(_to(res)).model_dump() if res else None


async def delete_schema(sid: str) -> bool:
    db = await get_db()
    res = await db[SCHEMAS].update_one(
        {"_id": sid, "flgactive": {"$ne": False}},
        {"$set": {"flgactive": False, "deletedAt": _now()}})
    return res.modified_count > 0
