"""Persistencia del versionado de estándares:
- CRUD append-only de `standards_versions`;
- restore de un snapshot sobre las colecciones publicadas de estándares
  (`parent_domains`, `glossary_terms`, `naming_config`).

`data_standards` es el **aggregate root** de los estándares: por eso su
repositorio puede reemplazar esas 3 colecciones al restaurar un snapshot
(operación que no pertenece a ninguna feature individual)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import UpdateOne

from app.core.db.client import get_db

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


# ── standards_versions (historial append-only) ────────────────────────────


async def list_versions() -> list[dict]:
    db = await get_db()
    docs = await db[COLL].find({}).to_list(None)
    docs.sort(key=lambda d: d.get("seq") or 0, reverse=True)
    return [_to_doc(d) for d in docs]


async def get_version(seq: int) -> dict | None:
    db = await get_db()
    doc = await db[COLL].find_one({"seq": seq})
    return _to_doc(doc) if doc else None


async def max_seq() -> int:
    db = await get_db()
    docs = await db[COLL].find({}, {"seq": 1}).to_list(None)
    return max((d.get("seq") or 0 for d in docs), default=0)


async def insert_version(fields: dict) -> dict:
    db = await get_db()
    vid = fields.get("id") or str(uuid.uuid4())
    doc = StandardsVersionDoc.model_validate({**fields, "id": vid})
    payload = doc.model_dump()
    await db[COLL].insert_one({"_id": vid, "flgactive": True,
                               **{k: v for k, v in payload.items() if k != "id"}})
    return payload


async def insert_version_next_seq(fields: dict) -> dict:
    """Asigna `seq = max+1` (+ `label = v{seq}`) y guarda. Ante colisión de seq
    (índice único → dos apply/rollback concurrentes) reintenta recomputando. El
    reintento vive acá porque toca el store (pymongo). `fields` NO trae seq/label."""
    from pymongo.errors import DuplicateKeyError

    for _ in range(8):
        seq = await max_seq() + 1
        try:
            return await insert_version({**fields, "seq": seq, "label": f"v{seq}"})
        except DuplicateKeyError:
            continue
    raise RuntimeError("No se pudo asignar la versión de estándares (concurrencia alta); reintentá.")


# ── Restore de snapshot (rollback) ────────────────────────────────────────


async def restore_domains(domains: list[dict]) -> None:
    """Deja `parent_domains` EXACTAMENTE como el snapshot: soft-delete de los
    activos y upsert de los del snapshot (por `id`)."""
    db = await get_db()
    keep = {d["id"] for d in domains if d.get("id")}
    await db[DOMAINS].update_many(
        {"flgactive": {"$ne": False}, "_id": {"$nin": list(keep)}},
        {"$set": {"flgactive": False, "deletedAt": _now()}},
    )
    ops = [UpdateOne(
        {"_id": d["id"]},
        {"$set": {**{k: v for k, v in d.items() if k != "id"}, "flgactive": True, "updatedAt": _now()},
         "$setOnInsert": {"createdAt": _now()}}, upsert=True)
        for d in domains if d.get("id")]
    if ops:
        await db[DOMAINS].bulk_write(ops, ordered=False)


async def restore_dict(entries: list[dict], preserve_ids: set[str] | None = None) -> None:
    """Deja `glossary_terms` EXACTAMENTE como el snapshot, salvo `preserve_ids`
    (D4): entradas HOY bloqueadas con contenido idéntico al snapshot — se
    conservan TAL CUAL (ni soft-delete ni upsert), así el restore no revierte
    el lock vigente (los snapshots pre-bloqueo traen locked=False)."""
    db = await get_db()
    preserve = preserve_ids or set()
    keep = {e["id"] for e in entries if e.get("id")} | preserve
    await db[DICT].update_many(
        {"flgactive": {"$ne": False}, "_id": {"$nin": list(keep)}},
        {"$set": {"flgactive": False, "deletedAt": _now()}},
    )
    # bulk_write: un solo round-trip en vez de N update_one secuenciales (a 49
    # términos eran ~seg de latencia por-op en Cosmos).
    ops = [UpdateOne(
        {"_id": e["id"]},
        {"$set": {**{k: v for k, v in e.items() if k != "id"}, "flgactive": True, "updatedAt": _now()},
         "$setOnInsert": {"createdAt": _now()}}, upsert=True)
        for e in entries if e.get("id") and e["id"] not in preserve]
    if ops:
        await db[DICT].bulk_write(ops, ordered=False)


async def restore_naming(config: dict) -> None:
    """Upsert de naming_config de ambos scopes desde el snapshot."""
    db = await get_db()
    for scope, rule in (config or {}).items():
        fields = {k: v for k, v in (rule or {}).items() if k in ("separator", "case")}
        if not fields:
            continue
        await db[NAMING].update_one(
            {"_id": scope},
            {"$set": {**fields, "scope": scope, "updatedAt": _now()},
             "$setOnInsert": {"createdAt": _now()}},
            upsert=True,
        )
