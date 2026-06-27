"""Lecturas de solo lectura para `reporting` (único punto que toca el store en
esta feature; guardrail `tests/architecture/test_store_boundary.py`).

Sólo lee documentos ACTIVOS (`flgactive != False`). Devuelve dicts crudos
normalizados (`_id` -> `id`, sin campos internos de Mongo) con EXACTAMENTE los
campos que la agregación pura de `service` consume — incluido `schema`, que en
Cosmos se guarda con esa clave (el modelo de `catalog` lo aliasa a `sql_schema`,
pero aquí lo dejamos como `schema` para la salida del reporte).
"""
from __future__ import annotations

from app.core.db.client import get_db

ACTIVE = {"flgactive": {"$ne": False}}


def _strip(doc: dict) -> dict:
    """`_id` -> `id`, descarta campos internos de Mongo."""
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    for k in ("flgactive", "deletedAt", "updatedAt", "createdAt"):
        doc.pop(k, None)
    return doc


async def _find_active(collection: str, projection: dict | None = None) -> list[dict]:
    db = await get_db()
    docs = await db[collection].find(ACTIVE, projection).to_list(None)
    return [_strip(d) for d in docs]


async def report_inputs() -> dict:
    """Todo lo que la agregación por tabla necesita, en una sola lectura por
    colección (las uniones se hacen en Python en `service`)."""
    return {
        "tables": await _find_active("canonical_tables"),
        "columns": await _find_active("canonical_columns"),
        "relationships": await _find_active("relationships"),
        "subjectAreas": await _find_active("subject_areas"),
        "projects": await _find_active("projects"),
    }


async def columns(table_id: str | None = None) -> list[dict]:
    """Columnas activas; todas, o las de una tabla (`tableId`)."""
    query = dict(ACTIVE)
    if table_id is not None:
        query["tableId"] = table_id
    db = await get_db()
    docs = await db["canonical_columns"].find(query).to_list(None)
    return [_strip(d) for d in docs]


async def parent_domains() -> list[dict]:
    """Parent domains activos (para resolver nombre del dominio en el export)."""
    return await _find_active("parent_domains")
