"""CRUD async de `ddl_rules` + singleton `ddl_ruleset_config`. Mutaciones SOLO
vía `data_standards.apply` (versionadas); acá no hay lógica de negocio.

Nota adaptador: los lookups inversos (¿qué reglas referencian este UDP?) se
resuelven en el service filtrando en Python sobre la lista activa (docenas de
reglas) — sin dot-paths sobre arrays de objetos en la query, que el adaptador
Lakebase no necesita soportar.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import UpdateOne

from app.core.db.client import get_db
from app.core.scope import scoped

from .models import RULE_KINDS, RULE_TARGETS, VALIDATION_STATES, DdlRuleDoc, DdlRulesetConfigDoc

COLL = "ddl_rules"
CONFIG_COLL = "ddl_ruleset_config"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_doc(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    for k in ("flgactive", "deletedAt", "updatedAt", "createdAt"):
        doc.pop(k, None)
    return doc


def _clean(data: dict) -> dict:
    """Normaliza una regla: kind/target/estado válidos y campos que no aplican
    al kind en None/[] (un generator no tiene target ni appliesTo; una rule no
    tiene sourceArtifact). Puro-ish (no toca DB)."""
    data = dict(data)
    if data.get("kind") not in RULE_KINDS:
        data["kind"] = "rule"
    if data["kind"] == "rule":
        if data.get("target") not in RULE_TARGETS:
            data["target"] = "column"
        data["sourceArtifact"] = None
    else:  # generator
        data["target"] = None
        data["appliesTo"] = []
    if data.get("validationState") not in VALIDATION_STATES:
        data["validationState"] = "valid"
    try:
        data["priority"] = int(data.get("priority", 100))
    except (TypeError, ValueError):
        data["priority"] = 100
    return data


# ── Reglas ─────────────────────────────────────────────────────────────────


async def list_rules(project_id: str) -> list[dict]:
    """Reglas activas DEL PROYECTO en orden de ejecución: (priority DESC, name ASC)
    — el mismo orden determinista del motor (spec §7.1) y del catálogo 16b."""
    db = await get_db()
    docs = await db[COLL].find(scoped(project_id, {"flgactive": {"$ne": False}})).to_list(None)
    docs.sort(key=lambda d: (-(d.get("priority") or 0), (d.get("name") or "").lower()))
    return [DdlRuleDoc.model_validate(_to_doc(d)).model_dump() for d in docs]


async def get_rule(rule_id: str) -> dict | None:
    db = await get_db()
    doc = await db[COLL].find_one({"_id": rule_id, "flgactive": {"$ne": False}})
    return DdlRuleDoc.model_validate(_to_doc(doc)).model_dump() if doc else None


async def create_rule(project_id: str, data: dict) -> dict:
    db = await get_db()
    d = DdlRuleDoc.model_validate({**_clean(data), "projectId": project_id,
                                   "id": data.get("id") or str(uuid.uuid4())})
    payload = d.model_dump()
    await db[COLL].insert_one(
        {"_id": d.id, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
         **{k: v for k, v in payload.items() if k != "id"}}
    )
    return payload


async def update_rule(rule_id: str, data: dict) -> dict | None:
    from pymongo import ReturnDocument

    db = await get_db()
    data = {k: v for k, v in _clean(data).items() if k not in ("id", "_id")}
    res = await db[COLL].find_one_and_update(
        {"_id": rule_id, "flgactive": {"$ne": False}},
        {"$set": {**data, "updatedAt": _now()}}, return_document=ReturnDocument.AFTER)
    return DdlRuleDoc.model_validate(_to_doc(res)).model_dump() if res else None


async def delete_rule(rule_id: str) -> bool:
    db = await get_db()
    res = await db[COLL].update_one({"_id": rule_id, "flgactive": {"$ne": False}},
                                    {"$set": {"flgactive": False, "deletedAt": _now()}})
    return res.modified_count > 0


async def restore_rules(project_id: str, rules: list[dict]) -> None:
    """Rollback: deja SOLO las reglas del snapshot activas (soft-delete de las
    demás) y upserta las del snapshot. Espeja `udp.restore_udp`. Doc 75 I5:
    ACOTADO al proyecto."""
    db = await get_db()
    keep = {r["id"] for r in rules if r.get("id")}
    await db[COLL].update_many(scoped(project_id, {"_id": {"$nin": list(keep)}, "flgactive": {"$ne": False}}),
                               {"$set": {"flgactive": False, "deletedAt": _now()}})
    ops = []
    for r in rules:
        rid = r.get("id")
        if not rid:
            continue
        payload = DdlRuleDoc.model_validate(_clean({**r, "id": rid, "projectId": project_id})).model_dump()
        ops.append(UpdateOne(
            {"_id": rid},
            {"$set": {"flgactive": True, "updatedAt": _now(),
                      **{k: v for k, v in payload.items() if k != "id"}},
             "$setOnInsert": {"createdAt": _now()}}, upsert=True))
    if ops:
        await db[COLL].bulk_write(ops, ordered=False)


# ── Lecturas livianas del catálogo (para /impact — proyección mínima) ─────


async def tables_light(project_id: str) -> list[dict]:
    db = await get_db()
    docs = await db["canonical_tables"].find(
        scoped(project_id, {"flgactive": {"$ne": False}}),
        {"physicalName": 1, "schema": 1, "udpValues": 1, "description": 1}).to_list(None)
    return [{**d, "id": str(d.pop("_id"))} for d in docs]


async def columns_light(project_id: str) -> list[dict]:
    db = await get_db()
    docs = await db["canonical_columns"].find(
        scoped(project_id, {"flgactive": {"$ne": False}}),
        {"tableId": 1, "physicalName": 1, "dataType": 1, "isNullable": 1,
         "isPrimaryKey": 1, "ordinal": 1, "parentDomainId": 1, "udpValues": 1,
         "description": 1}).to_list(None)
    return [{**d, "id": str(d.pop("_id"))} for d in docs]


# ── Config del ruleset (uno por proyecto: `_id` == projectId, doc 75 D3) ───


async def get_config(project_id: str) -> dict:
    db = await get_db()
    doc = await db[CONFIG_COLL].find_one({"_id": project_id})
    if not doc:
        return DdlRulesetConfigDoc(id=project_id, projectId=project_id).model_dump()
    return DdlRulesetConfigDoc.model_validate({**_to_doc(doc), "projectId": project_id}).model_dump()


async def set_config(project_id: str, lookups: dict | None = None,
                     functions: list | None = None, output: dict | None = None) -> dict:
    """Reemplaza el/los bloque(s) que vengan no-None (el patch del apply manda
    el set COMPLETO de lookups/functions/output, no deltas — payload chico)."""
    db = await get_db()
    sets: dict = {"updatedAt": _now(), "projectId": project_id}
    if lookups is not None:
        sets["lookups"] = lookups
    if functions is not None:
        sets["functions"] = functions
    if output is not None:
        sets["output"] = output
    await db[CONFIG_COLL].update_one(
        {"_id": project_id},
        {"$set": sets, "$setOnInsert": {"createdAt": _now()}}, upsert=True)
    return await get_config(project_id)


async def restore_config(project_id: str, snapshot: dict) -> None:
    """Rollback: deja la config DEL PROYECTO exactamente como el snapshot
    (snapshots pre-feature sin 'ddlConfig' → vacío)."""
    db = await get_db()
    cfg = DdlRulesetConfigDoc.model_validate(
        {**(snapshot or {}), "id": project_id, "projectId": project_id}).model_dump()
    await db[CONFIG_COLL].update_one(
        {"_id": project_id},
        {"$set": {"lookups": cfg["lookups"], "functions": cfg["functions"], "output": cfg["output"],
                  "projectId": project_id, "updatedAt": _now()},
         "$setOnInsert": {"createdAt": _now()}}, upsert=True)
