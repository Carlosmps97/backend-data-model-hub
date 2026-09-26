"""CRUD async de `parent_domains` + cascada de tipo sobre `canonical_columns`."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.core.db.client import get_db
from app.core.scope import scoped

from .cascade import FACETS, retype_filter
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


async def list_domains(project_id: str) -> list[dict]:
    """Dominios activos DEL PROYECTO (doc 75 D3)."""
    db = await get_db()
    docs = await db[COLL].find(scoped(project_id, {"flgactive": {"$ne": False}})).to_list(None)
    docs.sort(key=lambda d: (d.get("name") or "").lower())
    return [ParentDomainDoc.model_validate(_to_doc(d)).model_dump() for d in docs]


async def create_domain(project_id: str, data: dict) -> dict:
    db = await get_db()
    d = ParentDomainDoc.model_validate({**data, "projectId": project_id,
                                        "id": data.get("id") or str(uuid.uuid4())})
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
    res = await db[COLL].find_one_and_update(
        {"_id": domain_id, "flgactive": {"$ne": False}},
        {"$set": {**data, "updatedAt": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    if not res:
        return None
    # Cascada RESPETANDO EL OVERRIDE MANUAL, por faceta (doc 69) y con LA regla
    # de `cascade` (doc 95): solo re-tipa las columnas que SIGUEN al dominio —
    # sin override y con el tipo viejo. Una columna editada a mano (o que ya
    # tenía otro tipo) rompió la conexión y queda excluida.
    if cascade:
        await retype(domain_id, "physical", old.get("defaultDataType"), data.get("defaultDataType"))
        await retype(domain_id, "logical", old.get("logicalDataType"), data.get("logicalDataType"))
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


async def get_domain_types(domain_id: str) -> tuple[str | None, str | None]:
    """(`defaultDataType` físico, `logicalDataType`) ACTUALES del dominio;
    (None, None) si no existe (doc 69)."""
    db = await get_db()
    d = await db[COLL].find_one({"_id": domain_id, "flgactive": {"$ne": False}},
                                {"defaultDataType": 1, "logicalDataType": 1})
    return (d.get("defaultDataType"), d.get("logicalDataType")) if d else (None, None)


async def propagate_field(cascade_query: dict, field: str, value: str) -> int:
    """Aplica `value` al campo `field` de las columnas que matchean (faceta
    lógica: doc 69). Update directo. Devuelve el nº actualizado."""
    db = await get_db()
    res = await db[COLUMNS].update_many(cascade_query, {"$set": {field: value, "updatedAt": _now()}})
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
    a 400k columnas en el motor de reporting; NO $lookup, joins en Python a
    propósito)."""
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


# ── Doc 95: re-tipo con LA regla + columnas del dominio para la vista previa ──


async def retype(domain_id: str, facet: str, from_type: str | None, to_type: str | None) -> int:
    """Re-tipa en `facet` las columnas que SIGUEN al dominio (tipo `from_type`,
    sin override) a `to_type`. Sin tipo nuevo o sin cambio no toca nada (nunca
    se cascadea a vacío). Update directo; devuelve cuántas cambió."""
    if to_type is None or to_type == from_type:
        return 0
    db = await get_db()
    res = await db[COLUMNS].update_many(retype_filter(domain_id, facet, from_type),
                                        {"$set": {FACETS[facet][0]: to_type, "updatedAt": _now()}})
    return res.modified_count


async def domain_columns(domain_id: str) -> list[dict]:
    """Columnas ACTIVAS del dominio con lo justo para clasificarlas (doc 95 D7)."""
    db = await get_db()
    return await db[COLUMNS].find(
        {"parentDomainId": domain_id, "flgactive": {"$ne": False}},
        {"_id": 1, "tableId": 1, "physicalName": 1, "logicalName": 1, "dataType": 1,
         "logicalDataType": 1, "typeOverridden": 1, "logicalTypeOverridden": 1},
    ).to_list(None)


async def types_by_ids(domain_ids: list[str], include_deleted: bool = False
                       ) -> dict[str, tuple[str | None, str | None]]:
    """Doc 95: (físico, lógico) ACTUALES de los dominios pedidos, en una lectura.
    `include_deleted`: el rollback que revive un dominio borrado re-tipa desde
    su último tipo (el doc borrado lo conserva)."""
    if not domain_ids:
        return {}
    db = await get_db()
    flt: dict = {"_id": {"$in": list(domain_ids)}}
    if not include_deleted:
        flt["flgactive"] = {"$ne": False}
    docs = await db[COLL].find(flt, {"defaultDataType": 1, "logicalDataType": 1}).to_list(None)
    return {str(d["_id"]): (d.get("defaultDataType"), d.get("logicalDataType")) for d in docs}
