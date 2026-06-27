"""CRUD async de `changesets` + lectura de colecciones publicadas para overlay."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.core.db.client import get_db

from .models import ChangesetDoc

COLL = "changesets"
VERSIONED = ("parent_domains", "abbreviation_dict", "canonical_tables", "canonical_columns", "relationships", "views")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_doc(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    doc.pop("flgactive", None)
    return doc


async def create(title: str, owner: str, extra: dict | None = None) -> dict:
    """Crea un changeset (draft). `extra` siembra campos aditivos del snapshot
    (versionLabel, description, projectIds…) validados por el modelo."""
    db = await get_db()
    cs = ChangesetDoc.model_validate(
        {"id": str(uuid.uuid4()), "title": title, "owner": owner,
         "createdAt": _now(), "updatedAt": _now(), **(extra or {})}
    )
    await db[COLL].insert_one({"_id": cs.id, **cs.model_dump(exclude={"id"})})
    return cs.model_dump()


async def get(cs_id: str) -> dict | None:
    db = await get_db()
    doc = await db[COLL].find_one({"_id": cs_id})
    return ChangesetDoc.model_validate(_to_doc(doc)).model_dump() if doc else None


async def list_all() -> list[dict]:
    db = await get_db()
    docs = await db[COLL].find({}).to_list(None)
    docs.sort(key=lambda d: d.get("updatedAt") or "", reverse=True)
    return [ChangesetDoc.model_validate(_to_doc(d)).model_dump() for d in docs]


async def set_changes(cs_id: str, changes: dict) -> dict | None:
    db = await get_db()
    res = await db[COLL].find_one_and_update(
        {"_id": cs_id}, {"$set": {"changes": changes, "updatedAt": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    return ChangesetDoc.model_validate(_to_doc(res)).model_dump() if res else None


async def set_status(cs_id: str, fields: dict) -> dict | None:
    db = await get_db()
    res = await db[COLL].find_one_and_update(
        {"_id": cs_id}, {"$set": {**fields, "updatedAt": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    return ChangesetDoc.model_validate(_to_doc(res)).model_dump() if res else None


async def published(collection: str) -> list[dict]:
    """Lista publicada de una colección versionada (para overlay/diff)."""
    db = await get_db()
    docs = await db[collection].find({"flgactive": {"$ne": False}}).to_list(None)
    out = []
    for d in docs:
        d = dict(d); d["id"] = str(d.pop("_id"))
        d.pop("flgactive", None)
        out.append(d)
    return out


async def apply_changes(plan: list[tuple]) -> None:
    """Aplica el plan a las colecciones publicadas: upsert o soft-delete por entidad."""
    db = await get_db()
    now = _now()
    for collection, entity_id, op, payload in plan:
        if op == "delete":
            await db[collection].update_one(
                {"_id": entity_id}, {"$set": {"flgactive": False, "deletedAt": now, "updatedAt": now}}
            )
        else:  # upsert
            body = {k: v for k, v in (payload or {}).items() if k != "id"}
            await db[collection].update_one(
                {"_id": entity_id},
                {"$set": {**body, "flgactive": True, "updatedAt": now},
                 "$setOnInsert": {"createdAt": now}},
                upsert=True,
            )


async def cascade_domain_types(domain_changes: dict) -> None:
    """Tras aplicar dominios, re-deriva el tipo de las columnas sin override
    (misma cascada que M1a) para cada dominio con `defaultDataType` nuevo."""
    db = await get_db()
    now = _now()
    for did, ch in domain_changes.items():
        if ch.get("op") == "upsert" and "defaultDataType" in (ch.get("payload") or {}):
            await db["canonical_columns"].update_many(
                {"parentDomainId": did, "typeOverridden": {"$ne": True}},
                {"$set": {"dataType": ch["payload"]["defaultDataType"], "updatedAt": now}},
            )
