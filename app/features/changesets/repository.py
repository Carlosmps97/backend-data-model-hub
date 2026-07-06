"""CRUD async de `changesets` + `changeset_changes` + lectura de publicadas.

Los cambios viven en la colección `changeset_changes` (UN doc por cambio, con
`_id` determinista `{csId}::{collection}::{entityId}`) — el viejo dict embebido
en el doc del changeset topaba el límite de 2MB/doc de Cosmos RU con changesets
grandes. El doc de `changesets` queda como cabecera (estado/decisiones)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument, UpdateOne

from app.core.db.client import get_db

from .models import ChangeDoc, ChangesetDoc

COLL = "changesets"
CHANGES_COLL = "changeset_changes"
# Colecciones cuyo cambio pasa por el changeset/aprobación del canvas.
# `parent_domains` y `glossary_terms` SALIERON (2026-07-04): los estándares
# (UDP / Parent Domains) se editan y versionan en el módulo Data Standards, con
# escritura global directa fuera del publish (ver plan-implementacion/03 §4.1).
VERSIONED = ("canonical_tables", "canonical_columns", "relationships", "views")


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


async def list_summaries() -> list[dict]:
    """Como `list_all` pero SIN los blobs `changes`/`comments` (proyección):
    las listas de versiones/requests sólo proyectan cabeceras y el `changes`
    de un changeset grande pesa MBs — bajarlo por fila no escala."""
    db = await get_db()
    docs = await db[COLL].find({}, {"changes": 0, "comments": 0}).to_list(None)
    docs.sort(key=lambda d: d.get("updatedAt") or "", reverse=True)
    return [ChangesetDoc.model_validate(_to_doc(d)).model_dump() for d in docs]


def _safe_path_part(s: str) -> bool:
    """Apto como segmento de dot-path de Mongo (no vacío, sin '.' ni '$')."""
    return bool(s) and "." not in s and not s.startswith("$")


def change_key(cs_id: str, collection: str, entity_id: str) -> str:
    """`_id` determinista del cambio: el upsert por `_id` da last-write-wins
    por entidad, sin duplicados posibles (misma semántica del viejo dot-path)."""
    return f"{cs_id}::{collection}::{entity_id}"


async def set_change(cs_id: str, collection: str, entity_id: str, op: str, payload: dict | None) -> dict | None:
    """Graba UN cambio como documento propio en `changeset_changes`, sólo si el
    changeset sigue en `draft`.

    El guard ya no puede vivir en el filtro de UNA escritura (estado y cambio
    están en documentos distintos): protocolo de 3 pasos con compensación —
    1) touch atómico del padre CON filtro `status: draft` (si perdió el draft,
       ni se escribe);
    2) upsert del cambio por `_id` determinista, marcado con un `wtoken` único
       (identidad de ESTA escritura — `at` no sirve: dos escrituras pueden
       compartir timestamp);
    3) re-check del estado: si un submit ganó la carrera entre 1 y 2, se
       COMPENSA la escritura propia: si había un cambio previo (grabado
       legítimamente en draft) se RESTAURA — borrarlo destruiría un cambio ya
       aceptado del request enviado —; si no había, se borra el doc. En ambos
       casos el filtro por `wtoken` garantiza no pisar una re-edición ajena
       posterior. Además, `_apply_and_finalize` filtra por `at <= submittedAt`
       (ver service): una escritura tardía que se cuele entre el claim y la
       lectura del apply queda fuera del publish aunque esta compensación
       todavía no haya corrido."""
    if not (collection and entity_id):
        return None
    db = await get_db()
    parent = await db[COLL].find_one_and_update(
        {"_id": cs_id, "status": "draft"},
        {"$set": {"updatedAt": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    if parent is None:
        return None

    key = change_key(cs_id, collection, entity_id)
    # Timestamp por cambio: baseline preciso para detectar conflictos con
    # producción en el diff, y límite del ciclo de envío en el apply.
    at = _now()
    token = uuid.uuid4().hex
    doc = {"_id": key, "csId": cs_id, "collection": collection, "entityId": entity_id,
           "op": op, "at": at, "wtoken": token}
    if op != "delete":
        doc["payload"] = payload or {}
    prev = await db[CHANGES_COLL].find_one({"_id": key})
    await db[CHANGES_COLL].replace_one({"_id": key}, doc, upsert=True)

    still_draft = await db[COLL].find_one({"_id": cs_id, "status": "draft"}, {"_id": 1})
    if still_draft is None:
        if prev is not None:
            await db[CHANGES_COLL].replace_one({"_id": key, "wtoken": token}, prev)
        else:
            await db[CHANGES_COLL].delete_one({"_id": key, "wtoken": token})
        return None
    return ChangesetDoc.model_validate(_to_doc(parent)).model_dump()


async def changes_map(cs_id: str, collections: list[str] | None = None) -> dict[str, dict[str, dict]]:
    """Cambios del changeset como `{collection: {entityId: {op, payload?, at}}}`
    — la forma que consumen overlay/diff/apply. `collections` acota la query
    (usa el índice compuesto csId+collection); None = todas."""
    db = await get_db()
    flt: dict = {"csId": cs_id}
    if collections is not None:
        flt["collection"] = {"$in": collections}
    docs = await db[CHANGES_COLL].find(flt).to_list(None)
    out: dict[str, dict[str, dict]] = {}
    for d in docs:
        ch = ChangeDoc.model_validate({**d, "id": str(d.get("_id"))}).model_dump()
        entry: dict = {"op": ch["op"], "at": ch["at"]}
        if ch["op"] != "delete":
            entry["payload"] = ch["payload"] or {}
        out.setdefault(ch["collection"], {})[ch["entityId"]] = entry
    return out


async def set_approval(cs_id: str, actor: str, entry: dict) -> dict | None:
    """Registra la decisión de UN revisor con `$set` atómico (`approvals.<actor>`),
    sólo en `submitted`: dos revisores decidiendo a la vez no se pisan."""
    if not _safe_path_part(actor):
        return None
    db = await get_db()
    res = await db[COLL].find_one_and_update(
        {"_id": cs_id, "status": "submitted"},
        {"$set": {f"approvals.{actor}": entry, "updatedAt": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    return ChangesetDoc.model_validate(_to_doc(res)).model_dump() if res else None


async def push_comment(cs_id: str, comment: dict) -> dict | None:
    """Agrega un comentario con `$push` atómico (sin leer-modificar-escribir)."""
    db = await get_db()
    res = await db[COLL].find_one_and_update(
        {"_id": cs_id},
        {"$push": {"comments": comment}, "$set": {"updatedAt": _now()}},
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


async def transition(cs_id: str, from_status: str, fields: dict, expect: dict | None = None) -> dict | None:
    """`set_status` condicionado al estado ACTUAL (guard atómico de la máquina
    de estados): no-op (None) si el changeset ya no está en `from_status` —
    p.ej. retirar un request que un revisor decidió en paralelo.

    `expect` agrega condiciones extra al guard. El caso que motiva esto: el
    status es un string que se REPITE entre ciclos (withdraw → edit → resubmit
    vuelve a 'submitted'), así que un claim tardío por-status puede cerrar un
    ENVÍO distinto al que el revisor decidió (ABA). Los cierres pasan
    `expect={"submittedAt": <el del doc leído>}` — submittedAt cambia en cada
    envío, con lo que el claim solo matchea el ciclo que se estaba revisando."""
    db = await get_db()
    res = await db[COLL].find_one_and_update(
        {"_id": cs_id, "status": from_status, **(expect or {})},
        {"$set": {**fields, "updatedAt": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    return ChangesetDoc.model_validate(_to_doc(res)).model_dump() if res else None


async def published(collection: str, flt: dict | None = None, limit: int | None = None,
                    sort_field: str | None = None) -> list[dict]:
    """Lista publicada de una colección versionada (para overlay/diff).
    `flt` opcional (p.ej. `{"tableId": ...}` o `{"_id": {"$in": [...]}}`): a
    escala (15k tablas / 300k columnas) traer la colección COMPLETA por request
    no es viable — los llamadores calientes SIEMPRE deben pasar un slice.
    `limit` capea el resultado (búsqueda server-side de catálogo).

    `sort_field` ordena en Mongo ANTES del corte — pasarlo SOLO si el campo
    tiene índice en esa colección: Cosmos RU rechaza `.sort()` sobre campos sin
    índice (mismo motivo por el que folders ordena en Python). Hoy el único
    caso es canonical_tables.physicalName (índice en core/db/indexes.py). Sin
    sort_field el corte es en orden natural y el llamador re-ordena en Python."""
    db = await get_db()
    cursor = db[collection].find({"flgactive": {"$ne": False}, **(flt or {})})
    if limit is not None and limit > 0:
        if sort_field:
            cursor = cursor.sort(sort_field, 1)
        cursor = cursor.limit(limit)
    docs = await cursor.to_list(None)
    out = []
    for d in docs:
        d = dict(d); d["id"] = str(d.pop("_id"))
        d.pop("flgactive", None)
        out.append(d)
    return out


async def apply_changes(plan: list[tuple]) -> dict[str, int]:
    """Aplica el plan a las colecciones publicadas con UN `bulk_write` por
    colección (upsert / soft-delete por entidad). El loop viejo de un
    `update_one` awaiteado por entidad tardaba minutos con miles de cambios y
    dejaba una ventana enorme de aplicación parcial. Devuelve counts por
    colección para loguear el publish."""
    db = await get_db()
    now = _now()
    ops_by_coll: dict[str, list[UpdateOne]] = {}
    for collection, entity_id, op, payload in plan:
        ops = ops_by_coll.setdefault(collection, [])
        if op == "delete":
            ops.append(UpdateOne(
                {"_id": entity_id},
                {"$set": {"flgactive": False, "deletedAt": now, "updatedAt": now}},
            ))
        else:  # upsert
            body = {k: v for k, v in (payload or {}).items() if k != "id"}
            ops.append(UpdateOne(
                {"_id": entity_id},
                {"$set": {**body, "flgactive": True, "updatedAt": now},
                 "$setOnInsert": {"createdAt": now}},
                upsert=True,
            ))
    counts: dict[str, int] = {}
    for collection, ops in ops_by_coll.items():
        await db[collection].bulk_write(ops, ordered=False)
        counts[collection] = len(ops)
    return counts


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
