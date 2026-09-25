"""CRUD async de `glossary_terms`."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.core.db.client import get_db
from app.core.scope import scoped

from .models import AbbreviationDoc

COLL = "glossary_terms"
# Colecciones físicas re-derivables por scope (re-physicalize retroactivo, R5).
ENTITY_COLL = {"table": "canonical_tables", "column": "canonical_columns"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_doc(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    for k in ("flgactive", "deletedAt", "updatedAt", "createdAt"):
        doc.pop(k, None)
    return doc


async def list_entries(project_id: str, scope: str | None = None) -> list[dict]:
    """Términos activos DEL PROYECTO (doc 75 D3). `scope=None` = todos; con
    scope, sólo ese.

    Nota de compat: docs previos a R1c no tienen `scope` persistido; al validar
    con el modelo se les asigna el default `'column'`. Por eso el filtro por
    `scope='column'` se hace EN PYTHON (post-validación) y no en la query, así
    los docs legacy sin el campo también caen en `'column'`."""
    db = await get_db()
    docs = await db[COLL].find(scoped(project_id, {"flgactive": {"$ne": False}})).to_list(None)
    docs.sort(key=lambda d: (d.get("term") or "").lower())
    entries = [AbbreviationDoc.model_validate(_to_doc(d)).model_dump() for d in docs]
    if scope is not None:
        entries = [e for e in entries if e.get("scope") == scope]
    return entries


async def create_entry(project_id: str, data: dict) -> dict:
    db = await get_db()
    e = AbbreviationDoc.model_validate({**data, "projectId": project_id,
                                        "id": data.get("id") or str(uuid.uuid4())})
    payload = e.model_dump()
    await db[COLL].insert_one(
        {"_id": e.id, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
         **{k: v for k, v in payload.items() if k != "id"}}
    )
    return payload


async def update_entry(entry_id: str, data: dict) -> dict | None:
    db = await get_db()
    data = {k: v for k, v in data.items() if k not in ("id", "_id")}
    res = await db[COLL].find_one_and_update(
        {"_id": entry_id, "flgactive": {"$ne": False}},
        {"$set": {**data, "updatedAt": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    return AbbreviationDoc.model_validate(_to_doc(res)).model_dump() if res else None


async def delete_entry(entry_id: str) -> bool:
    db = await get_db()
    res = await db[COLL].update_one(
        {"_id": entry_id}, {"$set": {"flgactive": False, "deletedAt": _now()}}
    )
    return res.modified_count > 0


# ── Validación de términos (F2 #1): corpus de nombres lógicos publicados ──

CORPUS_SAMPLE_CAP = 50
TABLES_COLL = "canonical_tables"
COLUMNS_COLL = "canonical_columns"


async def get_entry(entry_id: str) -> dict | None:
    """Término activo por id (None si no existe o está soft-deleted)."""
    db = await get_db()
    doc = await db[COLL].find_one({"_id": entry_id, "flgactive": {"$ne": False}})
    return AbbreviationDoc.model_validate(_to_doc(doc)).model_dump() if doc else None


async def corpus_conflicts(project_id: str, pattern: str, scope: str = "column") -> tuple[list[dict], int]:
    """Apariciones del patrón (frase completa) en `logicalName` de las entidades
    ACTIVAS DEL PROYECTO del `scope` del término (doc 94 D9): un término de
    columna solo deriva nombres de columnas, así que solo lo bloquean nombres
    lógicos de columnas; uno de tabla, solo de tablas. Devuelve (muestra cap 50,
    conteo total). El regex corre case-insensitive server-side; es un scan
    aceptable como acción on-demand (botón Validar / save), no per-keystroke
    (riesgo medido en el doc 10)."""
    db = await get_db()
    flt = scoped(project_id, {"logicalName": {"$regex": pattern, "$options": "i"},
                              "flgactive": {"$ne": False}})
    if scope == "table":
        total = await db[TABLES_COLL].count_documents(flt)
        t_docs = await db[TABLES_COLL].find(
            flt, {"_id": 1, "physicalName": 1, "logicalName": 1},
        ).limit(CORPUS_SAMPLE_CAP).to_list(None)
        return [{"entity": "table", "tableName": t.get("physicalName") or "",
                 "columnName": None, "logicalName": t.get("logicalName") or ""} for t in t_docs], total

    total = await db[COLUMNS_COLL].count_documents(flt)
    sample: list[dict] = []
    if total > 0:
        c_docs = await db[COLUMNS_COLL].find(
            flt, {"physicalName": 1, "logicalName": 1, "tableId": 1},
        ).limit(CORPUS_SAMPLE_CAP).to_list(None)
        # Nombres de tabla en batch (NO $lookup, joins en Python a propósito).
        names = await table_physical_names([c.get("tableId") for c in c_docs if c.get("tableId")])
        for c in c_docs:
            sample.append({"entity": "column",
                           "tableName": names.get(c.get("tableId"), ""),
                           "columnName": c.get("physicalName"),
                           "logicalName": c.get("logicalName") or ""})
    return sample, total


async def table_physical_names(table_ids: list[str]) -> dict[str, str]:
    """`{tableId: physicalName}` de las tablas pedidas (una sola lectura)."""
    ids = list({t for t in table_ids if t})
    if not ids:
        return {}
    db = await get_db()
    return {t["_id"]: t.get("physicalName") or ""
            for t in await db[TABLES_COLL].find({"_id": {"$in": ids}}, {"physicalName": 1}).to_list(None)}


async def set_lock(entry_id: str, locked: bool, actor: str) -> dict | None:
    """Bloquea/desbloquea la entrada (D4). Devuelve el doc actualizado o None
    si no existe/está soft-deleted."""
    db = await get_db()
    fields = {"locked": locked,
              "lockedBy": actor if locked else None,
              "lockedAt": _now() if locked else None,
              "updatedAt": _now()}
    res = await db[COLL].find_one_and_update(
        {"_id": entry_id, "flgactive": {"$ne": False}},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return AbbreviationDoc.model_validate(_to_doc(res)).model_dump() if res else None


# ── Re-physicalize retroactivo (R5): re-deriva physicalName desde logicalName ──


async def entities_for_rephysicalize(project_id: str, scope: str) -> list[dict]:
    """Entidades activas del scope DEL PROYECTO con su `logicalName`/`physicalName` actuales
    (proyección mínima). `scope ∈ {'table','column'}`. Los físicos con override
    manual quedan FUERA del barrido (doc 68) — mismo contrato `$ne: True` que
    `typeOverridden` en la cascada de dominios."""
    db = await get_db()
    coll = ENTITY_COLL[scope]
    return await db[coll].find(
        scoped(project_id, {"flgactive": {"$ne": False}, "physicalNameOverridden": {"$ne": True}}),
        {"_id": 1, "logicalName": 1, "physicalName": 1, "physicalNameOverridden": 1,
         "tableId": 1},                 # doc 94 D7: el dry-run cuenta tablas afectadas
    ).to_list(None)


async def update_physical_names(scope: str, updates: list[tuple[str, str]]) -> int:
    """Aplica `(id, new_physical)` a la colección del scope. Update directo, uno
    por entidad (el store no garantiza bulk transaccional). Sólo escribe las que
    cambian (el caller ya filtró). Devuelve el nº de docs actualizados."""
    if not updates:
        return 0
    db = await get_db()
    coll = ENTITY_COLL[scope]
    now = _now()
    updated = 0
    for entity_id, physical in updates:
        res = await db[coll].update_one(
            {"_id": entity_id}, {"$set": {"physicalName": physical, "updatedAt": now}}
        )
        updated += res.modified_count
    return updated
