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


async def list_for_tables(table_ids: list[str]) -> list[dict]:
    """Relaciones que tocan alguna de las tablas dadas (`$in` en ambos extremos):
    el canvas no debe cargar TODAS las relaciones del sistema para filtrar."""
    if not table_ids:
        return []
    db = await get_db()
    # Campos legacy (source/target) en el $or: cubre docs pre-backfill; el
    # model_validate de abajo los devuelve ya normalizados a v2.
    docs = await db[COLL].find({
        "flgactive": {"$ne": False},
        "$or": [{"parentTableId": {"$in": table_ids}}, {"childTableId": {"$in": table_ids}},
                {"sourceTableId": {"$in": table_ids}}, {"targetTableId": {"$in": table_ids}}],
    }).to_list(None)
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
    res = await db[COLL].update_one({"_id": rid, "flgactive": {"$ne": False}},
                                    {"$set": {"flgactive": False, "deletedAt": _now()}})
    return res.modified_count > 0


async def list_for_column(column_id: str) -> list[dict]:
    """Relaciones ACTIVAS donde la columna participa en algún PAR (padre o
    hijo) — spec 10 §8. Usa los índices pairs.parentColumnId/pairs.childColumnId
    (multikey); los campos legacy cubren docs pre-backfill."""
    if not column_id:
        return []
    db = await get_db()
    docs = await db[COLL].find({
        "flgactive": {"$ne": False},
        "$or": [{"pairs.parentColumnId": column_id}, {"pairs.childColumnId": column_id},
                {"sourceColumnId": column_id}, {"targetColumnId": column_id}],
    }).to_list(None)
    return [RelationshipDoc.model_validate(_to(d)).model_dump() for d in docs]


async def canvases_containing(table_id: str) -> list[dict]:
    """Subject areas activas cuyos `tableIds` contienen la tabla — para saber
    en qué canvases es visible una relación. Devuelve `[{id, name, tableIds}]`
    (proyección: no baja layouts/drawings)."""
    if not table_id:
        return []
    db = await get_db()
    docs = await db["subject_areas"].find(
        {"flgactive": {"$ne": False}, "tableIds": table_id},
        {"name": 1, "tableIds": 1},
    ).to_list(None)
    return [{"id": str(d["_id"]), "name": d.get("name") or "", "tableIds": d.get("tableIds") or []}
            for d in docs]
