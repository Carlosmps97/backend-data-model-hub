"""CRUD async de `parent_domains` + cascada de tipo sobre `canonical_columns`."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.core.db.client import get_db

from .models import ParentDomainDoc

COLL = "parent_domains"
COLUMNS = "canonical_columns"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_doc(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    for k in ("flgactive", "deletedAt", "updatedAt", "createdAt"):
        doc.pop(k, None)
    return doc


async def list_domains() -> list[dict]:
    db = await get_db()
    docs = await db[COLL].find({"flgactive": {"$ne": False}}).to_list(None)
    docs.sort(key=lambda d: (d.get("name") or "").lower())
    return [ParentDomainDoc.model_validate(_to_doc(d)).model_dump() for d in docs]


async def create_domain(data: dict) -> dict:
    db = await get_db()
    d = ParentDomainDoc.model_validate({**data, "id": data.get("id") or str(uuid.uuid4())})
    payload = d.model_dump()
    await db[COLL].insert_one(
        {"_id": d.id, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
         **{k: v for k, v in payload.items() if k != "id"}}
    )
    return payload


async def update_domain(domain_id: str, data: dict, cascade_query: dict | None) -> dict | None:
    db = await get_db()
    data = {k: v for k, v in data.items() if k not in ("id", "_id")}
    res = await db[COLL].find_one_and_update(
        {"_id": domain_id, "flgactive": {"$ne": False}},
        {"$set": {**data, "updatedAt": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    if not res:
        return None
    # Cascada (req 4): re-deriva el tipo de las columnas sin override.
    if cascade_query is not None and "defaultDataType" in data:
        await db[COLUMNS].update_many(
            cascade_query, {"$set": {"dataType": data["defaultDataType"], "updatedAt": _now()}}
        )
    return ParentDomainDoc.model_validate(_to_doc(res)).model_dump()


async def delete_domain(domain_id: str) -> bool:
    db = await get_db()
    res = await db[COLL].update_one(
        {"_id": domain_id}, {"$set": {"flgactive": False, "deletedAt": _now()}}
    )
    return res.modified_count > 0


# ── Cascade directa de Parent Domain (p4b, retroactiva, fuera de publish) ──


async def columns_using(domain_id: str) -> list[dict]:
    """Columnas activas con `parentDomainId == domain_id` (proyección mínima
    para el cómputo de impacto). Ordenadas en Python."""
    db = await get_db()
    docs = await db[COLUMNS].find(
        {"parentDomainId": domain_id, "flgactive": {"$ne": False}},
        {"_id": 1, "physicalName": 1, "tableId": 1, "typeOverridden": 1},
    ).to_list(None)
    docs.sort(key=lambda d: (d.get("physicalName") or "").lower())
    return docs


async def get_default_data_type(domain_id: str) -> str | None:
    """`defaultDataType` ACTUAL del dominio (None si no existe/borrado)."""
    db = await get_db()
    d = await db[COLL].find_one(
        {"_id": domain_id, "flgactive": {"$ne": False}}, {"defaultDataType": 1}
    )
    return d.get("defaultDataType") if d else None


async def propagate_type(cascade_query: dict, data_type: str) -> int:
    """Aplica `data_type` a las columnas que matchean `cascade_query` (las que
    usan el dominio SIN override). Update directo. Devuelve el nº actualizado."""
    db = await get_db()
    res = await db[COLUMNS].update_many(
        cascade_query, {"$set": {"dataType": data_type, "updatedAt": _now()}}
    )
    return res.modified_count
