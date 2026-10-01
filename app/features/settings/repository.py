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
from app.core.logging import get_logger
from app.core.naming.engine import _VALID_CASES
from app.core.scope import naming_id, scoped

from .models import DEFAULTS, SCOPES, NamingConfigDoc

COLL = "naming_config"
log = get_logger("app.settings.repository")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _usable(key: str, value) -> bool:
    """¿El motor de naming puede usar este valor guardado? Doc 105 (R2): el
    apply ya valida al escribir, pero una config inválida guardada antes (por
    API) o que vuelva por un rollback/copia de estándares hacía que el motor
    levantara ValueError — 500 en cada alta/edición de columna y en physicalize."""
    if key == "case":
        return value in _VALID_CASES
    if key == "maxLength":   # 0 = límite desactivado; negativo bloquearía todo nombre
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return isinstance(value, str)


def _to_doc(project_id: str, scope: str, doc: dict | None) -> dict:
    """Normaliza un doc persistido (o None) a la forma pública, sembrando
    defaults; un valor que el motor no puede usar cae al default del scope."""
    base = {"scope": scope, "projectId": project_id, **DEFAULTS.get(scope, {})}
    if doc:
        for k in ("separator", "case", "maxLength"):
            v = doc.get(k)
            if v is None:
                continue
            if _usable(k, v):
                base[k] = v
            else:
                log.warning("invalid naming config value ignored", extra={"project": project_id, "scope": scope,
                                                                        "field": k, "value": repr(v)})
    return NamingConfigDoc.model_validate(base).model_dump()


async def get_stored(project_id: str) -> dict[str, dict]:
    """Lo GUARDADO por scope, sin el filtro de valores inválidos de `_to_doc`
    (un campo ausente sí toma su default). Doc 105: para decidir si un apply
    cambia algo — regrabar el valor visible sobre uno inválido guardado tiene
    que escribirse (si no, el inválido quedaba para siempre)."""
    db = await get_db()
    docs = {d.get("scope"): d for d in await db[COLL].find(scoped(project_id)).to_list(None)}
    out: dict[str, dict] = {}
    for scope in SCOPES:
        doc = docs.get(scope) or {}
        out[scope] = {k: doc[k] if doc.get(k) is not None else DEFAULTS.get(scope, {}).get(k)
                      for k in ("separator", "case", "maxLength")}
    return out


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
