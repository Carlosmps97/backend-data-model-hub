"""CRUD async de `changesets` + `changeset_changes` + lectura de publicadas.

Los cambios viven en la colección `changeset_changes` (UN doc por cambio, con
`_id` determinista `{csId}::{collection}::{entityId}`) — el viejo dict embebido
en el doc del changeset crecía sin techo con changesets
grandes. El doc de `changesets` queda como cabecera (estado/decisiones)."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from pymongo import DeleteOne, ReplaceOne, ReturnDocument, UpdateOne

from app.core.db.client import get_db
from app.core.logging import get_logger
from app.core.scope import assert_scoped_filter, scoped

from . import asof
from .models import ChangeDoc, ChangesetDoc

log = get_logger("app.changesets.repository")

COLL = "changesets"
CHANGES_COLL = "changeset_changes"
# Doc 105 (revisión R1): lápidas de las eliminaciones de drafts — UNA POR
# INTENTO {_id: "<csId>:<uuid>", csId, at: epoch} (ronda 3: con una sola por
# versión, un intento negado —doble clic, dueño y admin a la vez— o la purga
# retiraban la del intento que SÍ estaba en curso). Guían a
# `purge_orphan_changes`; viven mientras dura su intento (o hasta la purga, si
# se cortó en el medio). La gracia sólo protege a una versión VIVA (un intento
# en el otro proceso que todavía no borró la cabecera).
DELETED_COLL = "deleted_changesets"
TOMBSTONE_GRACE_SECONDS = 10 * 60
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
    procedencia es del alta; un re-create posterior arranca limpio).

    Doc 105 (H4): lo mismo con la imagen PREVIA que estampó un publish que
    falló (`before`/`beforeAt`, también en deletes; `before=None` = «no
    existía»): re-editar tras un publish a medias la perdía y el re-approve la
    re-capturaba de la producción a medio escribir (rollback y `asof:`
    contaminados). Si no hubo escritura, el re-approve la refresca igual
    (`store_before_images(keep_existing=False)`)."""
    if prev and prev.get("beforeAt"):
        doc["beforeAt"] = prev["beforeAt"]
        doc["before"] = prev.get("before")
    if doc.get("op") == "delete":
        return doc
    if origin:
        doc["origin"] = origin
    elif prev and prev.get("origin"):
        doc["origin"] = prev["origin"]
    return doc


async def set_change(cs_id: str, collection: str, entity_id: str, op: str, payload: dict | None,
                     origin: dict | None = None, owner: str | None = None) -> dict | None:
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
       todavía no haya corrido.

    Doc 104: con `owner`, el paso 1 exige además que el draft siga siendo de
    ese usuario — la cabecera puede cambiar de dueño (transferencia) entre la
    lectura del service y esta escritura; y si la versión se ELIMINÓ en el
    medio, la compensación no restaura el cambio previo (quedaría huérfano):
    sólo quita la escritura propia."""
    if not (collection and entity_id):
        return None
    db = await get_db()
    parent = await db[COLL].find_one_and_update(
        {"_id": cs_id, "status": "draft", **({"owner": owner} if owner else {})},
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

    # Doc 104: el re-check también mira al dueño — una transferencia que cae
    # entre el touch y la escritura compensa la escritura del dueño anterior.
    still_draft = await db[COLL].find_one(
        {"_id": cs_id, "status": "draft", **({"owner": owner} if owner else {})}, {"_id": 1})
    if still_draft is None:
        if prev is not None and await _exists(db, cs_id):
            await db[CHANGES_COLL].replace_one({"_id": key, "wtoken": token}, prev)
        else:
            await db[CHANGES_COLL].delete_one({"_id": key, "wtoken": token})
        return None
    return ChangesetDoc.model_validate(_to_doc(parent)).model_dump()


async def _exists(db, cs_id: str) -> bool:
    """¿La cabecera sigue existiendo? (doc 104: un draft eliminado no recibe
    compensaciones que lo re-pueblen). Proyección chica (un campo)."""
    return await db[COLL].find_one({"_id": cs_id}, {"status": 1}) is not None


# Tamaño de tanda del lote (mismo criterio que la carga Erwin): el fast-path
# del adaptador manda arrays por unnest — tandas acotadas, statements sanos.
_BULK_BATCH = 1000


async def set_changes_bulk(cs_id: str, items: list[dict], owner: str | None = None) -> dict | None:
    """Graba VARIOS cambios de una vez, con el MISMO protocolo de 3 pasos de
    `set_change` pagado UNA vez por lote: las cascadas (borrar tabla, crear
    tabla desde fuentes) iban cambio-por-cambio y a 4k columnas eran minutos.

    `items` = [{collection, entityId, op, payload?}]. Se dedup-ea por entidad
    (último gana — semántica del loop secuencial); los ReplaceOne homogéneos
    por `_id` activan el fast-path del adaptador (INSERT … ON CONFLICT vía
    unnest, 1-2 round-trips por tanda). Un solo `wtoken` identifica TODO el
    lote; si un submit gana la carrera entre el touch y el re-check, la
    compensación restaura los cambios previos legítimos y borra los nuevos,
    filtrando por ese token para no pisar re-ediciones ajenas posteriores.
    `owner` y la compensación ante una versión eliminada: como `set_change`
    (doc 104)."""
    deduped: dict[str, dict] = {}
    for it in items:
        if it.get("collection") and it.get("entityId"):
            deduped[change_key(cs_id, it["collection"], it["entityId"])] = it
    if not deduped:
        return None
    db = await get_db()
    parent = await db[COLL].find_one_and_update(
        {"_id": cs_id, "status": "draft", **({"owner": owner} if owner else {})},
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

    async def _compensate(alive: bool) -> None:
        # Sólo lo que grabó ESTA llamada (filtro por `wtoken`): una re-edición
        # ajena posterior no se pisa; las claves que no alcanzó a grabar, no
        # matchean y quedan como estaban.
        comp = [ReplaceOne({"_id": k, "wtoken": token}, prevs[k]) if alive and k in prevs
                else DeleteOne({"_id": k, "wtoken": token}) for k in keys]
        for i in range(0, len(comp), _BULK_BATCH):
            await db[CHANGES_COLL].bulk_write(comp[i:i + _BULK_BATCH])

    try:
        for i in range(0, len(ops), _BULK_BATCH):
            await db[CHANGES_COLL].bulk_write(ops[i:i + _BULK_BATCH])
    except BaseException:
        # Doc 105: una tanda que falla (timeout) no deja las anteriores a
        # medias — un rename de esquema o un restore de más de 1000 entidades
        # es todo o nada, salvo que la base tampoco deje compensar. También
        # ante una cancelación (`CancelledError`: el proceso se apaga).
        try:
            await _compensate(await _exists(db, cs_id))   # doc 104: un draft eliminado no se re-puebla
        except Exception:  # noqa: BLE001
            log.warning("bulk change write failed and its compensation too", extra={"cs_id": cs_id},
                        exc_info=True)
        raise

    still_draft = await db[COLL].find_one(
        {"_id": cs_id, "status": "draft", **({"owner": owner} if owner else {})}, {"_id": 1})
    if still_draft is None:
        await _compensate(await _exists(db, cs_id))
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


# Público para otros módulos que leen el ledger por clave (doc 105: los lotes
# del Reporting) — la misma forma que `changes_map`.
ledger_entry = _ledger_entry


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
    # Doc 105 (R2-A3): una edición EN SITIO llega por las dos lecturas: se valida una vez.
    unique = {d["_id"]: d for d in docs}.values()
    return {entity_id: entry for _, entity_id, entry in map(_ledger_entry, unique)}


async def column_change_tables(cs_id: str) -> dict[str, dict]:
    """Doc 105 (A7): cambios de `canonical_columns` de un changeset con lo JUSTO
    para ajustar conteos por tabla (`adjust_column_counts`): `op` y
    `payload.tableId`. Con un draft de carga Excel el ledger de columnas es la
    lectura dominante del reporte de tablas: se trae proyectado, sin payloads
    completos ni validación por documento. Forma de `changes_map(...)["canonical_columns"]`."""
    db = await get_db()
    docs = await db[CHANGES_COLL].find({"csId": cs_id, "collection": "canonical_columns"},
                                       {"entityId": 1, "op": 1, "payload.tableId": 1}).to_list(None)
    return {str(d.get("entityId")): ({"op": "delete"} if d.get("op") == "delete"
                                     else {"op": d.get("op"), "payload": {"tableId": (d.get("payload") or {}).get("tableId")}})
            for d in docs if d.get("entityId")}


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


async def transfer_owner(cs_id: str, expected_owner: str, new_owner: str, entry: dict) -> dict | None:
    """Doc 104: pasa el draft a `new_owner` y ANEXA `entry` a `transfers`, en
    UNA escritura condicionada al estado Y al dueño que el llamador leyó: si en
    el medio lo enviaron a revisión, lo transfirieron o lo borraron, no toca
    nada (None). `$push` atómico: dos transferencias jamás pisan su historia.
    Doc 105: tampoco con una carga Excel escribiendo (lock de la cabecera,
    que puede haber tomado el otro proceso de uvicorn)."""
    db = await get_db()
    res = await db[COLL].find_one_and_update(
        {"_id": cs_id, "status": "draft", "owner": expected_owner, **upload_lock_free()},
        {"$set": {"owner": new_owner, "updatedAt": _now()}, "$push": {"transfers": entry}},
        return_document=ReturnDocument.AFTER,
    )
    return ChangesetDoc.model_validate(_to_doc(res)).model_dump() if res else None


async def delete_changeset(cs_id: str, expected_owner: str, statuses: tuple[str, ...]) -> int | None:
    """Doc 104: borra la cabecera (sólo si sigue en `statuses` y con el dueño
    que el llamador leyó) y después TODOS sus cambios. Devuelve cuántos cambios
    se borraron, o None si la condición ya no se cumplía (nada se borra).

    `delete_many` y no `delete_one` a propósito: en el adaptador de Lakebase
    `delete_many` es un DELETE con la condición en su propio WHERE, que Postgres
    RE-EVALÚA si otra transacción cambió la fila en paralelo (un submit que
    gana la carrera deja el request intacto); `delete_one` resuelve la fila en
    una subconsulta previa que no se re-evalúa. La cabecera va PRIMERO: desde
    ahí `set_change` (paso 1, touch condicionado a draft) ya no escribe.
    Doc 105: tampoco con una carga Excel escribiendo (lock de la cabecera)."""
    db = await get_db()
    pending = await db[CHANGES_COLL].count_documents({"csId": cs_id})
    # Lápida ANTES de la cabecera (revisión R1): si el proceso muere entre la
    # cabecera y sus cambios, la purga del arranque sabe qué versión limpiar
    # sin recorrer todo el ledger. Propia de ESTE intento (ronda 3).
    stone = f"{cs_id}:{uuid.uuid4().hex}"
    await db[DELETED_COLL].insert_one({"_id": stone, "csId": cs_id, "at": _clock()})
    res = await db[COLL].delete_many(
        {"_id": cs_id, "status": {"$in": list(statuses)}, "owner": expected_owner,
         "partialApplyAt": None,              # nulo o ausente: sin publish a medias
         **upload_lock_free()})
    if not res.deleted_count:
        await _drop_tombstone(db, stone)      # sólo la propia: otro intento en curso conserva la suya
        return None
    # Doc 105 (H2): la versión YA no existe. Si borrar sus cambios falla (o el
    # proceso muere acá), quedan huérfanos que ningún lector ve: la lápida los
    # deja para la purga del arranque (`purge_orphan_changes`) y la eliminación
    # responde bien.
    try:
        gone = await db[CHANGES_COLL].delete_many({"csId": cs_id})
    except Exception:  # noqa: BLE001
        log.warning("changes of a deleted version left for the startup purge", extra={"cs_id": cs_id},
                    exc_info=True)
        return pending
    await _drop_tombstone(db, stone)
    return gone.deleted_count


async def _drop_tombstone(db, stone: str) -> None:
    try:
        await db[DELETED_COLL].delete_many({"_id": stone})
    except Exception:  # noqa: BLE001 — la purga la retira después
        log.warning("tombstone of a version delete left for the startup purge", extra={"stone": stone},
                    exc_info=True)


async def purge_orphan_changes() -> int:
    """Doc 105 (H2): borra los cambios cuya versión ya no existe (una eliminación
    que se cortó entre la cabecera y sus cambios). Idempotente; corre al
    arrancar la app. Revisión R1: sigue las LÁPIDAS de `delete_changeset` (antes
    un `distinct` sobre todo el ledger — en Lakebase, un escaneo completo en
    cada arranque de cada worker). Una lápida reciente puede ser una
    eliminación en curso en el otro proceso: se deja. Si su versión sigue viva
    (el proceso murió antes de borrar la cabecera, o la eliminación se negó),
    sólo se retira la lápida. Seguro con escrituras en curso: sin cabecera
    `set_change` no escribe."""
    db = await get_db()
    old = _clock() - TOMBSTONE_GRACE_SECONDS
    by_cs: dict[str, list[dict]] = {}
    for stone in await db[DELETED_COLL].find({}, {"csId": 1, "at": 1}).to_list(None):
        by_cs.setdefault(str(stone.get("csId") or stone["_id"]), []).append(stone)
    purged = 0
    for cs_id, stones in by_cs.items():
        if await db[COLL].find_one({"_id": cs_id}, {"_id": 1}) is None:
            # Sin cabecera, nada en curso que proteger: sus cambios son huérfanos
            # (un intento que siga vivo borraría lo mismo — idempotente).
            purged += (await db[CHANGES_COLL].delete_many({"csId": cs_id})).deleted_count
            done = [s["_id"] for s in stones]
        else:
            # Viva: intento negado, o en curso que aún no borró la cabecera — sólo
            # se retiran las lápidas viejas LEÍDAS (una posterior sigue).
            done = [s["_id"] for s in stones if float(s.get("at") or 0) < old]
        if done:
            await db[DELETED_COLL].delete_many({"_id": {"$in": done}})
    return purged


# ── Doc 105: lock de carga Excel en la cabecera ─────────────────────────
# `uvicorn --workers 2`: el lock de «un apply por versión» no puede vivir en la
# memoria de un proceso. Va en la cabecera, así transferir, eliminar y enviar a
# revisión lo respetan en la MISMA sentencia que los condiciona al dueño.
UPLOAD_LOCK_STALE_SECONDS = 10 * 60
_clock = time.time


def upload_lock_free(now: float | None = None) -> dict:
    """Condición de filtro: ninguna carga escribiendo (o la que había murió:
    su latido tiene más de UPLOAD_LOCK_STALE_SECONDS)."""
    now = _clock() if now is None else now
    return {"$or": [{"uploadLock": None},
                    {"uploadLock.heartbeat": {"$lt": now - UPLOAD_LOCK_STALE_SECONDS}}]}


def upload_lock_active(cs: dict | None, now: float | None = None) -> bool:
    lock = (cs or {}).get("uploadLock")
    if not lock:
        return False
    now = _clock() if now is None else now
    return float(lock.get("heartbeat") or 0) >= now - UPLOAD_LOCK_STALE_SECONDS


async def claim_upload_lock(cs_id: str, owner: str, job_id: str) -> dict | None:
    """Toma el lock para el job, sólo si la versión sigue en draft, con ese
    dueño y sin otra carga viva escribiendo. None = no se pudo (el llamador
    re-lee la cabecera para decir por qué)."""
    now = _clock()
    db = await get_db()
    res = await db[COLL].find_one_and_update(
        {"_id": cs_id, "status": "draft", "owner": owner, **upload_lock_free(now)},
        {"$set": {"uploadLock": {"jobId": job_id, "owner": owner, "at": now, "heartbeat": now}}},
        return_document=ReturnDocument.AFTER,
    )
    return ChangesetDoc.model_validate(_to_doc(res)).model_dump() if res else None


async def heartbeat_upload_lock(cs_id: str, job_id: str) -> bool:
    """Latido del lock. False si ya no es de este job (venció y otra carga lo
    tomó, o la versión ya no existe): el apply tiene que cortarse (doc 105, R1)."""
    db = await get_db()
    res = await db[COLL].update_one({"_id": cs_id, "uploadLock.jobId": job_id},
                                    {"$set": {"uploadLock.heartbeat": _clock()}})
    return bool(res.matched_count)


async def release_upload_lock(cs_id: str, job_id: str) -> None:
    """Suelta el lock SÓLO si sigue siendo de ese job (uno muerto que otro
    reemplazó no suelta el ajeno)."""
    db = await get_db()
    await db[COLL].update_one({"_id": cs_id, "uploadLock.jobId": job_id}, {"$set": {"uploadLock": None}})


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


async def store_before_images(cs_id: str, befores: dict[tuple[str, str], dict | None],
                              keep_existing: bool = True) -> None:
    """Estampa la imagen previa en cada doc de cambio, en UNA escritura por lote
    (doc 105: antes era una sentencia por entidad — miles en una versión
    grande, antes de escribir producción).

    `keep_existing` (el publish lo pasa = la cabecera tiene `partialApplyAt`):
    un RE-APPLY tras un apply que YA escribió producción no debe re-capturar
    (la 'previa' de ese reintento estaría contaminada por el apply a medias):
    se estampa sólo lo que no tiene marca. Sin escritura previa (el approve
    falló antes de escribir, o nunca se intentó) se re-captura TODO fresco —
    otra versión pudo publicar esas entidades en el medio (doc 105, H4b)."""
    if not befores:
        return
    db = await get_db()
    now = _now()
    keys = {change_key(cs_id, collection, entity_id): doc for (collection, entity_id), doc in befores.items()}
    if keep_existing:
        # Revisión R1/R2: las marcas que ya están se leen ANTES (por `_id`) y se
        # escribe sólo lo que falta con filtro `_id` puro — el camino rápido del
        # adaptador (un `beforeAt: {$exists}` en el filtro lo sacaba de ahí: una
        # sentencia por entidad). Seguro: con la versión reclamada por este
        # publish nadie más escribe sus cambios.
        ids = list(keys)
        for i in range(0, len(ids), _BULK_BATCH):
            marked = await db[CHANGES_COLL].find(
                {"_id": {"$in": ids[i:i + _BULK_BATCH]}, "beforeAt": {"$exists": True}}, {"_id": 1}).to_list(None)
            for d in marked:
                keys.pop(str(d["_id"]), None)
    ops = [UpdateOne({"_id": key}, {"$set": {"beforeAt": now, "before": doc}}) for key, doc in keys.items()]
    for i in range(0, len(ops), _BULK_BATCH):
        await db[CHANGES_COLL].bulk_write(ops[i:i + _BULK_BATCH], ordered=False)


async def project_of(collection: str, ids: list[str]) -> dict[str, str | None]:
    """Doc 105 (H7): `projectId` del documento que HOY existe con cada id — vivo
    o borrado (un upsert por `_id` lo revive) —, para el gate del publish que
    impide escribir sobre entidades de otro proyecto. Ids sin documento no
    aparecen."""
    if not ids:
        return {}
    db = await get_db()
    docs = await db[collection].find({"_id": {"$in": list(ids)}}, {"projectId": 1}).to_list(None)
    return {str(d["_id"]): d.get("projectId") for d in docs}


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
         "restoredFrom": 1, "transfers": 1}).to_list(None)
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


async def apply_changes(plan: list[tuple], progress: dict | None = None) -> dict[str, int]:
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
        # Proyección chica: sólo hace falta saber cuáles están borradas.
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
    if progress is not None:
        # Doc 104: desde acá se ESCRIBE en producción (antes sólo se leyó): si
        # algo falla de aquí en adelante, parte pudo llegar.
        progress["writing"] = True
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
