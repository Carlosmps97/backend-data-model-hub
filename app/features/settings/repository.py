"""Persistencia de `naming_config` (1 doc por (proyecto, scope); `_id` =
`<projectId>:<scope>`, doc 75 D3).

Único módulo de la feature que toca el store. La lectura siembra defaults en
memoria si el doc no existe (no escribe): así `GET` siempre responde ambos
scopes aunque el proyecto no tenga configuración todavía.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.core.db.client import get_db
from app.core.scope import naming_id, scoped

from .models import DEFAULTS, SCOPES, NamingConfigDoc

COLL = "naming_config"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_doc(project_id: str, scope: str, doc: dict | None) -> dict:
    """Normaliza un doc persistido (o None) a la forma pública, sembrando defaults."""
    base = {"scope": scope, "projectId": project_id, **DEFAULTS.get(scope, {})}
    if doc:
        for k in ("separator", "case", "maxLength"):
            if doc.get(k) is not None:
                base[k] = doc[k]
    return NamingConfigDoc.model_validate(base).model_dump()


async def get_all(project_id: str) -> dict[str, dict]:
    """Devuelve ambos scopes del proyecto (con defaults sembrados si faltan)."""
    db = await get_db()
    docs = await db[COLL].find(scoped(project_id)).to_list(None)
    by_scope = {d.get("scope"): d for d in docs}
    return {scope: _to_doc(project_id, scope, by_scope.get(scope)) for scope in SCOPES}


async def get_one(project_id: str, scope: str) -> dict:
    """Config de un scope del proyecto (default sembrado si el doc no existe)."""
    db = await get_db()
    doc = await db[COLL].find_one({"_id": naming_id(project_id, scope)})
    return _to_doc(project_id, scope, doc)


async def upsert(project_id: str, scope: str, data: dict) -> dict:
    """Upsert de {separator, case, maxLength} para (proyecto, scope)."""
    db = await get_db()
    fields = {k: v for k, v in data.items() if k in ("separator", "case", "maxLength")}
    res = await db[COLL].find_one_and_update(
        {"_id": naming_id(project_id, scope)},
        {
            "$set": {**fields, "scope": scope, "projectId": project_id, "updatedAt": _now()},
            "$setOnInsert": {"createdAt": _now()},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return _to_doc(project_id, scope, res)
