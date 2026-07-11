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


async def update_domain(domain_id: str, data: dict, cascade: bool = True) -> dict | None:
    db = await get_db()
    data = {k: v for k, v in data.items() if k not in ("id", "_id")}
    # Estado PREVIO: el tipo viejo del dominio define qué columnas "mantienen" la
    # conexión (las que aún tienen ese tipo). Se lee antes de actualizar.
    old = await db[COLL].find_one({"_id": domain_id, "flgactive": {"$ne": False}})
    if not old:
        return None
    old_type = old.get("defaultDataType")
    res = await db[COLL].find_one_and_update(
        {"_id": domain_id, "flgactive": {"$ne": False}},
        {"$set": {**data, "updatedAt": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    if not res:
        return None
    new_type = data.get("defaultDataType")
    # Cascada RESPETANDO EL OVERRIDE MANUAL: solo re-tipa columnas que MANTIENEN
    # el tipo viejo del dominio (`dataType == old_type`) y no fueron editadas a
    # mano (`typeOverridden != True`). Una columna cuyo tipo se cambió
    # manualmente (rompió la conexión) queda excluida — aunque su flag venga mal.
    if cascade and new_type is not None and new_type != old_type:
        await db[COLUMNS].update_many(
            {"parentDomainId": domain_id, "typeOverridden": {"$ne": True},
             "dataType": old_type, "flgactive": {"$ne": False}},
            {"$set": {"dataType": new_type, "updatedAt": _now()}},
        )
    return ParentDomainDoc.model_validate(_to_doc(res)).model_dump()


async def delete_domain(domain_id: str) -> bool:
    db = await get_db()
    res = await db[COLL].update_one(
        {"_id": domain_id}, {"$set": {"flgactive": False, "deletedAt": _now()}}
    )
    return res.modified_count > 0


# ── Cascade directa de Parent Domain (p4b, retroactiva, fuera de publish) ──


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


# ── Impacto agrupado por tabla (F2 #2) ─────────────────────────────────────

TABLES = "canonical_tables"
SUBJECT_AREAS = "subject_areas"


def impact_pipeline(domain_id: str) -> list[dict]:
    """Pipeline del impacto: columnas ACTIVAS del dominio agrupadas por tabla
    (nº afectadas + nº con override manual). Puro (testeable sin DB)."""
    return [
        {"$match": {"parentDomainId": domain_id, "flgactive": {"$ne": False}}},
        {"$group": {
            "_id": "$tableId",
            "columns": {"$sum": 1},
            "overridden": {"$sum": {"$cond": [{"$eq": ["$typeOverridden", True]}, 1, 0]}},
        }},
    ]


async def impact_groups(domain_id: str) -> list[dict]:
    """`$match` + `$group` por tableId sobre canonical_columns (patrón probado
    a 400k columnas en el motor de reporting; NO $lookup: Cosmos)."""
    db = await get_db()
    return await db[COLUMNS].aggregate(impact_pipeline(domain_id)).to_list(None)


async def tables_by_ids(table_ids: list[str]) -> dict[str, dict]:
    """Join batch: tableId → {schema, physicalName, logicalName} desde
    canonical_tables (proyección mínima, un solo $in)."""
    if not table_ids:
        return {}
    db = await get_db()
    docs = await db[TABLES].find(
        {"_id": {"$in": table_ids}},
        {"schema": 1, "physicalName": 1, "logicalName": 1},
    ).to_list(None)
    return {str(d["_id"]): {"schema": d.get("schema"),
                            "physicalName": d.get("physicalName"),
                            "logicalName": d.get("logicalName")} for d in docs}


async def models_affected(table_ids: list[str]) -> int:
    """Nº de canvases (`subject_areas`) cuyos `tableIds` intersectan las tablas
    afectadas = "modelos de datos" impactados (spec #2)."""
    if not table_ids:
        return 0
    db = await get_db()
    return await db[SUBJECT_AREAS].count_documents(
        {"tableIds": {"$in": table_ids}, "flgactive": {"$ne": False}})
