"""Persistencia de `naming_config` (1 doc por scope; `scope` es el `_id`).

Único módulo de la feature que toca el store. La lectura siembra defaults en
memoria si el doc no existe (no escribe): así `GET` siempre responde ambos
scopes aunque la colección esté vacía.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.core.db.client import get_db

from .models import DEFAULTS, SCOPES, NamingConfigDoc

COLL = "naming_config"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_doc(scope: str, doc: dict | None) -> dict:
    """Normaliza un doc Cosmos (o None) a la forma pública, sembrando defaults."""
    base = {"scope": scope, **DEFAULTS.get(scope, {})}
    if doc:
        for k in ("separator", "case"):
            if doc.get(k) is not None:
                base[k] = doc[k]
    return NamingConfigDoc.model_validate(base).model_dump()


async def get_all() -> dict[str, dict]:
    """Devuelve ambos scopes (con defaults sembrados si faltan)."""
    db = await get_db()
    docs = await db[COLL].find({}).to_list(None)
    by_scope = {d.get("_id"): d for d in docs}
    return {scope: _to_doc(scope, by_scope.get(scope)) for scope in SCOPES}


async def get_one(scope: str) -> dict:
    """Config de un scope (default sembrado si el doc no existe)."""
    db = await get_db()
    doc = await db[COLL].find_one({"_id": scope})
    return _to_doc(scope, doc)


async def upsert(scope: str, data: dict) -> dict:
    """Upsert de {separator, case} para `scope`. Crea el doc si no existía."""
    db = await get_db()
    fields = {k: v for k, v in data.items() if k in ("separator", "case")}
    res = await db[COLL].find_one_and_update(
        {"_id": scope},
        {
            "$set": {**fields, "updatedAt": _now()},
            "$setOnInsert": {"createdAt": _now()},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return _to_doc(scope, res)
