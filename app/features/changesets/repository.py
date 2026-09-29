"""CRUD async de `changesets` + `changeset_changes` + lectura de publicadas.

Los cambios viven en la colección `changeset_changes` (UN doc por cambio, con
`_id` determinista `{csId}::{collection}::{entityId}`) — el viejo dict embebido
en el doc del changeset crecía sin techo con changesets
grandes. El doc de `changesets` queda como cabecera (estado/decisiones)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import DeleteOne, ReplaceOne, ReturnDocument, UpdateOne

from app.core.db.client import get_db
from app.core.scope import assert_scoped_filter, scoped

from . import asof
from .models import ChangeDoc, ChangesetDoc

COLL = "changesets"
CHANGES_COLL = "changeset_changes"
# Colecciones cuyo cambio pasa por el changeset/aprobación del canvas, en
# ORDEN DE DEPENDENCIA para el apply (proyectos → folders → canvases → tablas
# → columnas → relaciones → vistas).
# `parent_domains` y `glossary_terms` SALIERON (2026-07-04): los estándares
# (UDP / Parent Domains) se editan y versionan en el módulo Data Standards, con
# escritura global directa fuera del publish (ver plan-implementacion/03 §4.1).
# `projects`/`folders`/`subject_areas` ENTRARON (2026-07-16, doc 16): la
# ESTRUCTURA creada/editada en una versión draft se escribía directo a las
# colecciones publicadas y aparecía en producción ANTES de aprobar.
# `schemas` ENTRÓ (2026-07-16, doc 18): el esquema de BD como entidad — crear/
# renombrar/eliminar un esquema forma parte de la versión publicable. Va ANTES
# de canonical_tables (las tablas lo referencian por nombre).
# Doc 75 D5: `projects` SIGUE versionado — se crea directo (con su v1), pero
# renombrar/describir/borrar el proyecto son cambios del draft del propio
# proyecto; el apply de un delete de proyecto cascada en `_apply_and_finalize`.
VERSIONED = ("projects", "folders", "subject_areas", "schemas",
             "canonical_tables", "canonical_columns", "relationships", "views")


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


async def list_summaries(project_id: str | None = None) -> list[dict]:
    """Como `list_all` pero SIN los blobs `changes`/`comments` (proyección):
    las listas de versiones/requests sólo proyectan cabeceras y el `changes`
    de un changeset grande pesa MBs — bajarlo por fila no escala. Con
    `project_id` sólo las versiones de ESE proyecto (doc 75 D2)."""
    db = await get_db()
    flt = scoped(project_id) if project_id else {}
    docs = await db[COLL].find(flt, {"changes": 0, "comments": 0}).to_list(None)
    docs.sort(key=lambda d: d.get("updatedAt") or "", reverse=True)
    return [ChangesetDoc.model_validate(_to_doc(d)).model_dump() for d in docs]


def change_key(cs_id: str, collection: str, entity_id: str) -> str:
    """`_id` determinista del cambio: el upsert por `_id` da last-write-wins
    por entidad, sin duplicados posibles (misma semántica del viejo dot-path)."""
    return f"{cs_id}::{collection}::{entity_id}"


def _with_origin(doc: dict, origin: dict | None, prev: dict | None) -> dict:
    """Adjunta la procedencia (doc 51) al doc del cambio. El upsert por `_id`
    REEMPLAZA el doc: sin este carry-forward, editar la entidad en el mismo
    draft borraría el origin del alta (paste/CTAS). Un delete lo corta (la
    procedencia es del alta; un re-create posterior arranca limpio)."""
    if doc.get("op") == "delete":
        return doc
    if origin:
        doc["origin"] = origin
    elif prev and prev.get("origin"):
        doc["origin"] = prev["origin"]
    return doc


async def set_change(cs_id: str, collection: str, entity_id: str, op: str, payload: dict | None,
                     origin: dict | None = None) -> dict | None:
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
    doc = _with_origin(doc, origin, prev)
    await db[CHANGES_COLL].replace_one({"_id": key}, doc, upsert=True)

    still_draft = await db[COLL].find_one({"_id": cs_id, "status": "draft"}, {"_id": 1})
    if still_draft is None:
        if prev is not None:
            await db[CHANGES_COLL].replace_one({"_id": key, "wtoken": token}, prev)
        else:
            await db[CHANGES_COLL].delete_one({"_id": key, "wtoken": token})
        return None
    return ChangesetDoc.model_validate(_to_doc(parent)).model_dump()


# Tamaño de tanda del lote (mismo criterio que la carga Erwin): el fast-path
# del adaptador manda arrays por unnest — tandas acotadas, statements sanos.
_BULK_BATCH = 1000


async def set_changes_bulk(cs_id: str, items: list[dict]) -> dict | None:
    """Graba VARIOS cambios de una vez, con el MISMO protocolo de 3 pasos de
    `set_change` pagado UNA vez por lote: las cascadas (borrar tabla, crear
    tabla desde fuentes) iban cambio-por-cambio y a 4k columnas eran minutos.

    `items` = [{collection, entityId, op, payload?}]. Se dedup-ea por entidad
    (último gana — semántica del loop secuencial); los ReplaceOne homogéneos
    por `_id` activan el fast-path del adaptador (INSERT … ON CONFLICT vía
    unnest, 1-2 round-trips por tanda). Un solo `wtoken` identifica TODO el
    lote; si un submit gana la carrera entre el touch y el re-check, la
    compensación restaura los cambios previos legítimos y borra los nuevos,
    filtrando por ese token para no pisar re-ediciones ajenas posteriores."""
    deduped: dict[str, dict] = {}
    for it in items:
        if it.get("collection") and it.get("entityId"):
            deduped[change_key(cs_id, it["collection"], it["entityId"])] = it
    if not deduped:
        return None
    db = await get_db()
    parent = await db[COLL].find_one_and_update(
        {"_id": cs_id, "status": "draft"},
        {"$set": {"updatedAt": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    if parent is None:
        return None

    at = _now()
    token = uuid.uuid4().hex
    keys = list(deduped)
    prevs = {d["_id"]: d for d in await db[CHANGES_COLL].find({"_id": {"$in": keys}}).to_list(None)}
    ops = []
    for key, it in deduped.items():
        doc = {"_id": key, "csId": cs_id, "collection": it["collection"],
               "entityId": it["entityId"], "op": it["op"], "at": at, "wtoken": token}
        if it["op"] != "delete":
            doc["payload"] = it.get("payload") or {}
        doc = _with_origin(doc, it.get("origin"), prevs.get(key))
        ops.append(ReplaceOne({"_id": key}, doc, upsert=True))
    for i in range(0, len(ops), _BULK_BATCH):
        await db[CHANGES_COLL].bulk_write(ops[i:i + _BULK_BATCH])

    still_draft = await db[COLL].find_one({"_id": cs_id, "status": "draft"}, {"_id": 1})
    if still_draft is None:
        comp = [ReplaceOne({"_id": k, "wtoken": token}, prevs[k]) if k in prevs
                else DeleteOne({"_id": k, "wtoken": token}) for k in keys]
        for i in range(0, len(comp), _BULK_BATCH):
            await db[CHANGES_COLL].bulk_write(comp[i:i + _BULK_BATCH])
        return None
    return ChangesetDoc.model_validate(_to_doc(parent)).model_dump()


async def changes_map(cs_id: str, collections: list[str] | None = None) -> dict[str, dict[str, dict]]:
    """Cambios del changeset como `{collection: {entityId: {op, payload?, at}}}`
    — la forma que consumen overlay/diff/apply. `collections` acota la query
    (usa el índice compuesto csId+collection); None = todas.

    Doc 70: `asof:<versionId>` es un changeset VIRTUAL — el inverso compuesto
    de las versiones publicadas después de esa (`asof_changes_map`); todos los
    lectores changeset-aware heredan el snapshot por esta única puerta."""
    version_id = asof.asof_version_id(cs_id)
    if version_id is not None:
        return await asof_changes_map(version_id, collections)
    return await _ledger_map(cs_id, collections)


async def asof_changes_map(version_id: str, collections: list[str] | None = None) -> dict[str, dict[str, dict]]:
    """Inverso compuesto de las versiones publicadas DESPUÉS de `version_id`
    (doc 70 §3): producción + este mapa = estado exacto del modelo en esa
    versión. La producción actual (sin posteriores) devuelve `{}`. Levanta
    `AsOfUnavailable` si la versión no existe, no está publicada o alguna
    posterior no tiene imágenes previas."""
    target = await get(version_id)
    if not target:
        raise asof.AsOfUnavailable("not-found", version_id)
    if target.get("status") != "approved" or not target.get("appliedAt"):
        raise asof.AsOfUnavailable("not-applied", version_id)
    later = await applied_after(target["projectId"], str(target["appliedAt"]))   # latest → oldest
    ledgers = [await _ledger_map(v["id"], collections) for v in later]
    composed, missing = asof.compose_inverse(ledgers)
    if missing:
        raise asof.AsOfUnavailable("no-before", version_id, missing)
    return composed


def _ledger_entry(d: dict) -> tuple[str, str, dict]:
    """(colección, entityId, cambio) de un doc del ledger, con la forma que
    devuelve `changes_map`."""
    ch = ChangeDoc.model_validate({**d, "id": str(d.get("_id"))}).model_dump()
    entry: dict = {"op": ch["op"], "at": ch["at"]}
    if ch["op"] != "delete":
        entry["payload"] = ch["payload"] or {}
    if ch.get("beforeAt"):
        # Imagen previa capturada en el publish → habilita el rollback.
        entry["beforeAt"] = ch["beforeAt"]
        entry["before"] = ch.get("before")
    return ch["collection"], ch["entityId"], entry


async def _ledger_map(cs_id: str, collections: list[str] | None = None) -> dict[str, dict[str, dict]]:
    """Lector CRUDO del ledger de un changeset real (ver `changes_map`)."""
    db = await get_db()
    flt: dict = {"csId": cs_id}
    if collections is not None:
        flt["collection"] = {"$in": collections}
    docs = await db[CHANGES_COLL].find(flt).to_list(None)
    out: dict[str, dict[str, dict]] = {}
    for d in docs:
        collection, entity_id, entry = _ledger_entry(d)
        out.setdefault(collection, {})[entity_id] = entry
    return out


async def column_changes_for_tables(cs_id: str, column_ids: list[str],
                                    table_ids: list[str]) -> dict[str, dict]:
    """Doc 102: cambios de `canonical_columns` de un changeset REAL (no `asof:`)
    que tocan un LOTE de tablas, sin leer su ledger entero: los de columnas ya
    leídas (`column_ids`, por `_id` — ediciones, bajas y mudanzas fuera del
    lote) y los upserts cuyo `payload.tableId` cae en el lote (altas, revividas,
    mudanzas hacia el lote). Forma de `changes_map(...)["canonical_columns"]`."""
    db = await get_db()
    docs: list[dict] = []
    if column_ids:
        keys = [change_key(cs_id, "canonical_columns", c) for c in column_ids]
        docs += await db[CHANGES_COLL].find({"_id": {"$in": keys}}).to_list(None)
    if table_ids:
        docs += await db[CHANGES_COLL].find({"csId": cs_id, "collection": "canonical_columns",
                                             "payload.tableId": {"$in": table_ids}}).to_list(None)
    return {entity_id: entry for _, entity_id, entry in map(_ledger_entry, docs)}


async def set_approval(cs_id: str, actor: str, entry: dict) -> dict | None:
    """Registra la decisión de UN revisor de forma atómica, sólo en `submitted`
    (dos revisores decidiendo a la vez no se pisan). La key es el username TAL
    CUAL — los usernames SSO son correos (`carlos@dominio.com`), y un dot-path
    `approvals.<actor>` los splitearía por los puntos (el guard viejo devolvía
    None y el router lo disfrazaba de "no longer in review", bug 2026-08-30):
    con `$mergeObjects` la key viaja como DATO jsonb, nunca como path."""
    if not actor:
        return None
    db = await get_db()
    res = await db[COLL].find_one_and_update(
        {"_id": cs_id, "status": "submitted"},
        {"$mergeObjects": {"approvals": {actor: entry}},
         "$set": {"updatedAt": _now()}},
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


async def capture_before_images(plan: list[tuple]) -> dict[tuple[str, str], dict | None]:
    """Imagen PREVIA publicada de cada entidad del plan de apply — {(colección,
    entityId): doc | None}. None = no existe activa (el rollback la borra).
    Se llama ANTES de `apply_changes` (después ya no hay 'antes')."""
    out: dict[tuple[str, str], dict | None] = {}
    by_coll: dict[str, list[str]] = {}
    for collection, entity_id, _op, _payload in plan:
        by_coll.setdefault(collection, []).append(entity_id)
    for collection, ids in by_coll.items():
        docs = {d["id"]: d for d in await published(collection, {"_id": {"$in": ids}})}
        for eid in ids:
            out[(collection, eid)] = docs.get(eid)
    return out


async def store_before_images(cs_id: str, befores: dict[tuple[str, str], dict | None]) -> None:
    """Estampa la imagen previa en cada doc de cambio. `beforeAt $exists:false`
    en el filtro: un RE-APPLY tras un fallo parcial no debe re-capturar (la
    'previa' de ese reintento ya estaría contaminada por el apply a medias)."""
    db = await get_db()
    now = _now()
    for (collection, entity_id), doc in befores.items():
        await db[CHANGES_COLL].update_one(
            {"_id": change_key(cs_id, collection, entity_id), "beforeAt": {"$exists": False}},
            {"$set": {"beforeAt": now, "before": doc}})


async def latest_applied_id(project_id: str) -> str | None:
    """Id del changeset APLICADO más reciente DEL PROYECTO (máximo `appliedAt`)."""
    db = await get_db()
    docs = await db[COLL].find(scoped(project_id, {"status": "approved", "appliedAt": {"$ne": None}}),
                               {"appliedAt": 1}).to_list(None)
    if not docs:
        return None
    return str(max(docs, key=lambda d: d.get("appliedAt") or "")["_id"])


async def applied_after(project_id: str, applied_at: str) -> list[dict]:
    """Changesets DEL PROYECTO publicados DESPUÉS de `applied_at` — los que un
    rollback A esa versión debe deshacer — del MÁS RECIENTE al más viejo (doc
    75 I6: jamás versiones de otro proyecto). Se ordena en Python (son pocas)."""
    db = await get_db()
    docs = await db[COLL].find(
        scoped(project_id, {"status": "approved", "appliedAt": {"$gt": applied_at}}),
        {"appliedAt": 1, "versionLabel": 1, "owner": 1, "title": 1}).to_list(None)
    docs.sort(key=lambda d: d.get("appliedAt") or "", reverse=True)
    # Doc 84 C2: owner/title viajan para la atribución por entidad del compare.
    return [{"id": str(d["_id"]), "appliedAt": d.get("appliedAt"),
             "versionLabel": d.get("versionLabel"), "owner": d.get("owner"),
             "title": d.get("title")} for d in docs]


async def entity_changes(collection: str, entity_id: str) -> list[dict]:
    """Cambios de UNA entidad con imagen previa ESTAMPADA (`beforeAt`) — el
    marcador de que el cambio entró a un apply (doc 51). Cruza TODOS los
    changesets (índice compuesto collection+entityId); el llamador filtra
    además por cabecera aplicada — `beforeAt` solo no alcanza: un apply
    fallido lo deja estampado con el request devuelto a revisión."""
    db = await get_db()
    docs = await db[CHANGES_COLL].find(
        {"collection": collection, "entityId": entity_id,
         "beforeAt": {"$exists": True}}).to_list(None)
    return [ChangeDoc.model_validate({**d, "id": str(d.get("_id"))}).model_dump()
            for d in docs]


async def changesets_by_ids(cs_ids: list[str]) -> dict[str, dict]:
    """Cabeceras proyectadas de changesets puntuales, por id (doc 51 — el
    historial junta cambios de VARIAS versiones; bajar docs completos con
    `comments` por cabecera no hace falta)."""
    if not cs_ids:
        return {}
    db = await get_db()
    docs = await db[COLL].find(
        {"_id": {"$in": sorted(set(cs_ids))}},
        {"versionLabel": 1, "title": 1, "owner": 1, "status": 1, "projectId": 1,
         "appliedAt": 1, "reviewedBy": 1, "approvals": 1,
         "restoredFrom": 1}).to_list(None)
    out: dict[str, dict] = {}
    for d in docs:
        d = dict(d)
        d["id"] = str(d.pop("_id"))
        out[d["id"]] = d
    return out


async def earliest_applied(project_id: str) -> dict | None:
    """Cabecera de la versión APLICADA más VIEJA DEL PROYECTO (mínimo `appliedAt`)
    — el marcador v1 de la carga inicial (doc 51: la entrada sintética
    "Initial load" toma de acá label y fecha de fallback).

    Doc 82: el doc 75 insertó `changesets_deleting_project` EN MEDIO de esta
    función y su cola (`if not docs … return {...}`) quedó como código muerto
    tras el `return` de la otra — esta devolvía siempre None y el historial
    perdía la fila «Initial load». `tests/architecture/test_function_bodies.py`
    acusa ambas formas (función sin `return` / código tras un `return`)."""
    db = await get_db()
    docs = await db[COLL].find(scoped(project_id, {"status": "approved", "appliedAt": {"$ne": None}}),
                               {"appliedAt": 1, "versionLabel": 1, "title": 1}).to_list(None)
    if not docs:
        return None
    d = min(docs, key=lambda d: d.get("appliedAt") or "")
    return {"id": str(d["_id"]), "appliedAt": d.get("appliedAt"),
            "versionLabel": d.get("versionLabel"), "title": d.get("title")}


async def changesets_deleting_project(cs_ids: list[str]) -> set[str]:
    """Ids de los changesets (entre `cs_ids`) que llevan un delete de `projects`
    (doc 75 D5/D20: la bandeja marca la pill «Deletes project» sin pedir el diff).
    Usa el prefijo del índice `(collection, entityId)`."""
    if not cs_ids:
        return set()
    db = await get_db()
    docs = await db[CHANGES_COLL].find(
        {"collection": "projects", "op": "delete", "csId": {"$in": sorted(set(cs_ids))}},
        {"csId": 1}).to_list(None)
    return {str(d.get("csId")) for d in docs}


async def published(collection: str, flt: dict | None = None, limit: int | None = None,
                    sort_field: str | None = None, projection: dict | None = None) -> list[dict]:
    """Lista publicada de una colección versionada (para overlay/diff).
    `flt` opcional (p.ej. `{"tableId": ...}` o `{"_id": {"$in": [...]}}`): a
    escala (15k tablas / 300k columnas) traer la colección COMPLETA por request
    no es viable — los llamadores calientes SIEMPRE deben pasar un slice.
    `limit` capea el resultado (búsqueda server-side de catálogo).

    `sort_field` ordena en la BD ANTES del corte — pasarlo SOLO si el campo
    tiene índice en esa colección: a escala un orden sin índice sería full-scan
    (mismo motivo por el que folders ordena en Python). Hoy el único
    caso es canonical_tables.physicalName (índice en core/db/indexes.py). Sin
    sort_field el corte es en orden natural y el llamador re-ordena en Python.

    `projection` (doc 55): campos a traer cuando el llamador necesita la
    colección entera pero solo algunos campos (la carga masiva resuelve
    identidades de tablas por nombre sobre TODO el pool)."""
    assert_scoped_filter(collection, flt)   # doc 75 D1: sin alcance no se lee
    db = await get_db()
    cursor = db[collection].find({"flgactive": {"$ne": False}, **(flt or {})}, projection)
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


async def deleted_at(collection: str, ids: list[str]) -> dict[str, str | None]:
    """Doc 100 (P3): `{id: deletedAt}` de las entidades de `ids` que están
    BORRADAS (soft-delete) en producción. La revisión las necesita: el diff
    sólo mira lo activo y una entidad que otra versión borró se veía como
    «added», sin aviso de conflicto."""
    if not ids:
        return {}
    flt = {"_id": {"$in": ids}}
    assert_scoped_filter(collection, flt)
    db = await get_db()
    docs = await db[collection].find({**flt, "flgactive": False}, {"deletedAt": 1}).to_list(None)
    return {str(d["_id"]): d.get("deletedAt") for d in docs}


# Marcas que deja un borrado (`deletedIn`: cascada del borrado de proyecto,
# doc 75). Las maneja la base, nunca un payload.
_DELETION_MARKS = ("deletedAt", "deletedIn")


async def apply_changes(plan: list[tuple]) -> dict[str, int]:
    """Aplica el plan a las colecciones publicadas con UN `bulk_write` por
    colección (upsert / soft-delete por entidad). El loop viejo de un
    `update_one` awaiteado por entidad tardaba minutos con miles de cambios y
    dejaba una ventana enorme de aplicación parcial. Devuelve counts por
    colección para loguear el publish.

    Doc 100 (P3): un upsert sobre una entidad BORRADA la reactiva (gana el
    draft que publica: rollback, restore, o un draft que la tocó antes de que
    otro la borrara — la revisión lo avisa) y le quita las marcas de borrado:
    antes quedaba activa con su `deletedAt` viejo. Las reactivadas van en el
    MISMO lote que el resto y las marcas salen después con UNA sentencia por
    colección: un `$unset` dentro de cada op sacaba al lote del camino rápido
    del adaptador (una sentencia por fila) y un rollback puede reactivar miles."""
    db = await get_db()
    now = _now()
    upsert_ids: dict[str, list[str]] = {}
    for collection, entity_id, op, _payload in plan:
        if op != "delete":
            upsert_ids.setdefault(collection, []).append(entity_id)
    revived: dict[str, list[str]] = {}
    for collection, ids in upsert_ids.items():
        # Proyección de un campo REAL: `{"_id": 1}` el adaptador la lee como
        # «sin exclusiones» y trae los documentos completos.
        docs = await db[collection].find({"_id": {"$in": ids}, "flgactive": False},
                                         {"flgactive": 1}).to_list(None)
        if docs:
            revived[collection] = [str(d["_id"]) for d in docs]
    ops_by_coll: dict[str, list[UpdateOne]] = {}
    for collection, entity_id, op, payload in plan:
        ops = ops_by_coll.setdefault(collection, [])
        if op == "delete":
            ops.append(UpdateOne(
                {"_id": entity_id},
                {"$set": {"flgactive": False, "deletedAt": now, "updatedAt": now}},
            ))
        else:  # upsert — las marcas de borrado las maneja la base, nunca un payload
            body = {k: v for k, v in (payload or {}).items() if k != "id" and k not in _DELETION_MARKS}
            ops.append(UpdateOne(
                {"_id": entity_id},
                {"$set": {**body, "flgactive": True, "updatedAt": now},
                 "$setOnInsert": {"createdAt": now}},
                upsert=True,
            ))
    counts: dict[str, int] = {}
    for collection, ops in ops_by_coll.items():      # orden del plan (dependencias)
        await db[collection].bulk_write(ops, ordered=False)
        if revived.get(collection):
            # Sólo las que quedaron ACTIVAS: si otra publicación la volvió a
            # borrar en el medio, conserva las marcas de ese borrado.
            await db[collection].update_many(
                {"_id": {"$in": revived[collection]}, "flgactive": True},
                {"$unset": dict.fromkeys(_DELETION_MARKS, "")})
        counts[collection] = len(ops)
    return counts
