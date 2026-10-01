"""Persistencia del versionado de estándares POR PROYECTO (doc 75 D3):
- CRUD append-only de `standards_versions` (`seq` único por (projectId, seq));
- restore de un snapshot sobre las colecciones publicadas de estándares
  (`parent_domains`, `glossary_terms`, `naming_config`) — SIEMPRE acotado al
  proyecto (I5: sin el filtro, el soft-delete borraría los demás proyectos).

`data_standards` es el **aggregate root** de los estándares: por eso su
repositorio puede reemplazar esas 3 colecciones al restaurar un snapshot
(operación que no pertenece a ninguna feature individual)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import UpdateOne

from app.core.db.client import get_db
from app.core.scope import naming_id, scoped
from app.features.settings.repository import _usable as _naming_usable

from .models import StandardsVersionDoc

COLL = "standards_versions"
DOMAINS = "parent_domains"
DICT = "glossary_terms"
NAMING = "naming_config"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_doc(doc: dict) -> dict:
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    d.pop("flgactive", None)
    return StandardsVersionDoc.model_validate(d).model_dump()


# ── standards_versions (historial append-only, por proyecto) ──────────────


async def list_versions(project_id: str) -> list[dict]:
    db = await get_db()
    docs = await db[COLL].find(scoped(project_id)).to_list(None)
    docs.sort(key=lambda d: d.get("seq") or 0, reverse=True)
    return [_to_doc(d) for d in docs]


async def get_version(project_id: str, seq: int) -> dict | None:
    db = await get_db()
    doc = await db[COLL].find_one(scoped(project_id, {"seq": seq}))
    return _to_doc(doc) if doc else None


async def owners(collection: str, ids: list[str]) -> dict[str, dict]:
    """`{id: {projectId, flgactive}}` de los docs de `collection` con esos ids,
    de CUALQUIER proyecto y también los borrados (doc 105: un upsert del apply
    no puede dar de alta un id que ya existe)."""
    if not ids:
        return {}
    db = await get_db()
    docs = await db[collection].find({"_id": {"$in": list(ids)}}, {"projectId": 1, "flgactive": 1}).to_list(None)
    return {str(d["_id"]): {"projectId": d.get("projectId"), "flgactive": d.get("flgactive", True) is not False}
            for d in docs}


async def max_seq(project_id: str) -> int:
    db = await get_db()
    docs = await db[COLL].find(scoped(project_id), {"seq": 1}).to_list(None)
    return max((d.get("seq") or 0 for d in docs), default=0)


async def insert_version(fields: dict) -> dict:
    db = await get_db()
    vid = fields.get("id") or str(uuid.uuid4())
    doc = StandardsVersionDoc.model_validate({**fields, "id": vid})
    payload = doc.model_dump()
    await db[COLL].insert_one({"_id": vid, "flgactive": True,
                               **{k: v for k, v in payload.items() if k != "id"}})
    return payload


async def insert_version_next_seq(project_id: str, fields: dict) -> dict:
    """Asigna `seq = max(seq del proyecto) + 1` (+ `label = v{seq}`) y guarda. Ante
    colisión de seq (índice único (project_id, seq) → dos apply/rollback
    concurrentes del MISMO proyecto) reintenta recomputando. `fields` NO trae
    seq/label ni projectId."""
    from pymongo.errors import DuplicateKeyError

    for _ in range(8):
        seq = await max_seq(project_id) + 1
        try:
            return await insert_version({**fields, "projectId": project_id, "seq": seq, "label": f"v{seq}"})
        except DuplicateKeyError:
            continue
    raise RuntimeError("Couldn't allocate the standards version (high concurrency); retry.")


# ── Restore de snapshot (rollback) — acotado al proyecto (I5) ─────────────


async def restore_domains(project_id: str, domains: list[dict]) -> None:
    """Deja `parent_domains` DEL PROYECTO exactamente como el snapshot: soft-delete
    de los activos que no están y upsert de los del snapshot (por `id`)."""
    db = await get_db()
    keep = {d["id"] for d in domains if d.get("id")}
    await db[DOMAINS].update_many(
        scoped(project_id, {"flgactive": {"$ne": False}, "_id": {"$nin": list(keep)}}),
        {"$set": {"flgactive": False, "deletedAt": _now()}},
    )
    ops = [UpdateOne(
        {"_id": d["id"]},
        {"$set": {**{k: v for k, v in d.items() if k != "id"}, "projectId": project_id,
                  "flgactive": True, "updatedAt": _now()},
         "$setOnInsert": {"createdAt": _now()}}, upsert=True)
        for d in domains if d.get("id")]
    if ops:
        await db[DOMAINS].bulk_write(ops, ordered=False)


# Claves de un término que el restore escribe (las del snapshot vigente).
_TERM_RESTORE_KEYS = ("term", "abbrev", "scope", "locked", "lockedBy", "lockedAt")


async def restore_dict(project_id: str, entries: list[dict], preserve_ids: set[str] | None = None) -> None:
    """Deja `glossary_terms` DEL PROYECTO exactamente como el snapshot, salvo
    `preserve_ids` (D4): entradas HOY bloqueadas con contenido idéntico al
    snapshot — se conservan TAL CUAL (ni soft-delete ni upsert), así el restore
    no revierte el lock vigente (los snapshots pre-bloqueo traen locked=False)."""
    db = await get_db()
    preserve = preserve_ids or set()
    keep = {e["id"] for e in entries if e.get("id")} | preserve
    await db[DICT].update_many(
        scoped(project_id, {"flgactive": {"$ne": False}, "_id": {"$nin": list(keep)}}),
        {"$set": {"flgactive": False, "deletedAt": _now()}},
    )
    # bulk_write: un solo round-trip en vez de N update_one secuenciales. Solo
    # se restauran las claves VIGENTES (doc 94 D11): un snapshot viejo no
    # reinyecta campos retirados como `wordType`.
    ops = [UpdateOne(
        {"_id": e["id"]},
        {"$set": {**{k: v for k, v in e.items() if k in _TERM_RESTORE_KEYS}, "projectId": project_id,
                  "flgactive": True, "updatedAt": _now()},
         "$setOnInsert": {"createdAt": _now()}}, upsert=True)
        for e in entries if e.get("id") and e["id"] not in preserve]
    if ops:
        await db[DICT].bulk_write(ops, ordered=False)


async def restore_naming(project_id: str, config: dict) -> None:
    """Upsert de naming_config de ambos scopes DEL PROYECTO desde el snapshot.
    Doc 105 (R5): no regraba valores que el motor no puede usar — misma regla
    que la lectura de la app (`settings.repository._usable`): un snapshot
    anterior al doc 105 con, p. ej., `case: 'Upper'` volvía a la BD y el kit
    Erwin, que lee `naming_config` directo, abortaba. Se graban como None (=
    el default del scope al leer)."""
    db = await get_db()
    for scope, rule in (config or {}).items():
        # Lo inválido o ausente (None) del snapshot se restaura como None: la
        # lectura lo toma como el default del scope — lo que la app veía de ese
        # snapshot (ronda 3: omitirlo dejaba el valor de AHORA, p. ej. un
        # `maxLength: None` legado no volvía a 150).
        fields = {k: (v if v is not None and _naming_usable(k, v) else None)
                  for k, v in (rule or {}).items() if k in ("separator", "case", "maxLength")}
        if not fields:
            continue
        await db[NAMING].update_one(
            {"_id": naming_id(project_id, scope)},
            {"$set": {**fields, "scope": scope, "projectId": project_id, "updatedAt": _now()},
             "$setOnInsert": {"createdAt": _now()}},
            upsert=True,
        )
