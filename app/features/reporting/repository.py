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


# El reporte sólo usa estos campos de cada colección. Proyectarlos evita traer
# los campos PESADOS de `subject_areas` (`layout`/`drawings` de un canvas de 100
# tablas son varios KB — 150 canvases eran ~1.7s sólo por el tamaño del doc).
_SA_LITE = {"name": 1, "projectId": 1, "tableIds": 1}
_REL_LITE = {"sourceTableId": 1, "targetTableId": 1}


async def _subject_areas(ids: list[str] | None = None) -> list[dict]:
    """Canvases (proyectados a name/projectId/tableIds). Si `ids`, sólo los que
    referencian alguna de esas tablas (`tableIds $in`)."""
    db = await get_db()
    query = dict(ACTIVE)
    if ids is not None:
        query["tableIds"] = {"$in": ids}
    docs = await db["subject_areas"].find(query, _SA_LITE).to_list(None)
    return [_strip(d) for d in docs]


async def _relationships(ids: list[str] | None = None) -> list[dict]:
    """Relaciones (proyectadas a source/target). Si `ids`, sólo las que tocan
    alguna de esas tablas."""
    db = await get_db()
    query = dict(ACTIVE)
    if ids is not None:
        query["$or"] = [{"sourceTableId": {"$in": ids}}, {"targetTableId": {"$in": ids}}]
    docs = await db["relationships"].find(query, _REL_LITE).to_list(None)
    return [_strip(d) for d in docs]


async def column_counts() -> dict[str, int]:
    """Conteo de columnas activas POR tabla vía agregación server-side (`$group`).
    La fila del reporte sólo necesita `columnCount`, no los documentos de columna:
    materializar las columnas costaba ~24s a 400k docs; el `$group` (sobre el
    índice `tableId`) devuelve ~10k pares en ~2s."""
    db = await get_db()
    pipeline = [{"$match": ACTIVE}, {"$group": {"_id": "$tableId", "n": {"$sum": 1}}}]
    rows = await db["canonical_columns"].aggregate(pipeline).to_list(None)
    return {r["_id"]: r["n"] for r in rows if r.get("_id") is not None}


async def report_inputs() -> dict:
    """Todo lo que la agregación por tabla necesita, en una lectura por colección
    (las uniones se hacen en Python en `service`). Las columnas se traen YA
    CONTADAS por tabla (`columnCounts`), no como documentos — así el reporte
    escala a cientos de miles de columnas sin materializarlas."""
    return {
        "tables": await _find_active("canonical_tables"),
        "columnCounts": await column_counts(),
        "relationships": await _relationships(),
        "subjectAreas": await _subject_areas(),
        "projects": await _find_active("projects"),
    }


async def report_inputs_page(limit: int) -> dict:
    """Como `report_inputs` pero SOLO las primeras `limit` tablas (orden físico,
    índice `physicalName`) y sus conteos de columna (agregación acotada por
    `tableId $in`). Es el camino de la carga inicial del front (`limit=50`): evita
    tocar las 10k tablas / 400k columnas → sub-segundo en vez de varios segundos.
    Sólo válido SIN filtros (con schema/projectId hace falta el barrido completo)."""
    db = await get_db()
    docs = await db["canonical_tables"].find(ACTIVE).sort("physicalName", 1).limit(limit).to_list(limit)
    tables = [_strip(d) for d in docs]
    ids = [t["id"] for t in tables]
    pipeline = [{"$match": {**ACTIVE, "tableId": {"$in": ids}}},
                {"$group": {"_id": "$tableId", "n": {"$sum": 1}}}]
    rows = await db["canonical_columns"].aggregate(pipeline).to_list(None)
    return {
        "tables": tables,
        "columnCounts": {r["_id"]: r["n"] for r in rows if r.get("_id") is not None},
        "relationships": await _relationships(ids),
        "subjectAreas": await _subject_areas(ids),
        "projects": await _find_active("projects"),
    }


async def columns(table_id: str | None = None, table_ids: list[str] | None = None,
                  limit: int | None = None) -> list[dict]:
    """Columnas activas; todas, las de una tabla (`tableId`) o de un lote
    (`tableIds`, `$in` sobre el índice tableId — para el export acotado).
    `limit` acota el resultado (tope de seguridad del export sin filtro: sin él,
    a 400k columnas la respuesta eran ~91MB)."""
    query = dict(ACTIVE)
    if table_ids:
        query["tableId"] = {"$in": table_ids}
    elif table_id is not None:
        query["tableId"] = table_id
    db = await get_db()
    cur = db["canonical_columns"].find(query)
    if limit:
        cur = cur.limit(limit)
    docs = await cur.to_list(limit or None)
    return [_strip(d) for d in docs]


async def parent_domains() -> list[dict]:
    """Parent domains activos (para resolver nombre del dominio en el export)."""
    return await _find_active("parent_domains")
