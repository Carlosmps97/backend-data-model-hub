"""Lecturas de solo lectura para `reporting` (único punto que toca el store en
esta feature; guardrail `tests/architecture/test_store_boundary.py`).

Sólo lee documentos ACTIVOS (`flgactive != False`) de UN proyecto (doc 75:
toda lectura de una colección de alcance va con `scoped(project_id, …)`).
Devuelve dicts crudos normalizados (`_id` -> `id`, sin campos internos del
store) con EXACTAMENTE los campos que la agregación pura de `service` consume — incluido `schema`, que en
la BD se guarda con esa clave (el modelo de `catalog` lo aliasa a `sql_schema`,
pero aquí lo dejamos como `schema` para la salida del reporte).
"""
from __future__ import annotations

from app.core.db.client import get_db
from app.core.scope import scoped

ACTIVE = {"flgactive": {"$ne": False}}


def _strip(doc: dict) -> dict:
    """`_id` -> `id`, descarta campos internos de Mongo."""
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    for k in ("flgactive", "deletedAt", "updatedAt", "createdAt"):
        doc.pop(k, None)
    return doc


async def _find_active(collection: str, project_id: str,
                       projection: dict | None = None) -> list[dict]:
    """Docs activos del proyecto."""
    db = await get_db()
    docs = await db[collection].find(scoped(project_id, ACTIVE), projection).to_list(None)
    return [_strip(d) for d in docs]


# El reporte sólo usa estos campos de cada colección. Proyectarlos evita traer
# los campos PESADOS de `subject_areas` (`layout`/`drawings` de un canvas de 100
# tablas son varios KB — 150 canvases eran ~1.7s sólo por el tamaño del doc).
_SA_LITE = {"name": 1, "projectId": 1, "tableIds": 1, "viewIds": 1}   # viewIds: doc 70
_REL_LITE = {"parentTableId": 1, "childTableId": 1,
             "sourceTableId": 1, "targetTableId": 1}  # legacy: docs pre-backfill


async def _subject_areas(project_id: str, ids: list[str] | None = None) -> list[dict]:
    """Canvases del proyecto (proyectados a name/projectId/tableIds/viewIds). Si
    `ids`, sólo los que referencian alguna de esas tablas (`tableIds $in`).
    `viewIds` ausente/None = canvas legacy (doc 70) — `_strip` no lo inventa."""
    db = await get_db()
    query = scoped(project_id, ACTIVE)
    if ids is not None:
        query["tableIds"] = {"$in": ids}
    docs = await db["subject_areas"].find(query, _SA_LITE).to_list(None)
    return [_strip(d) for d in docs]


async def _relationships(project_id: str, ids: list[str] | None = None) -> list[dict]:
    """Relaciones del proyecto (proyectadas a los dos extremos parent/child,
    con fallback a los campos legacy source/target). Si `ids`, sólo las que
    tocan alguna de esas tablas."""
    db = await get_db()
    query = scoped(project_id, ACTIVE)
    if ids is not None:
        query["$or"] = [{"parentTableId": {"$in": ids}}, {"childTableId": {"$in": ids}},
                        {"sourceTableId": {"$in": ids}}, {"targetTableId": {"$in": ids}}]
    docs = await db["relationships"].find(query, _REL_LITE).to_list(None)
    return [{"parentTableId": d.get("parentTableId") or d.get("targetTableId"),
             "childTableId": d.get("childTableId") or d.get("sourceTableId")}
            for d in docs]


async def filter_options(project_id: str) -> dict:
    """Doc 70 §4.1: universo de los filtros del reporte tabular SIN paginar —
    esquemas distintos de las tablas activas del proyecto (agregación `$group`,
    no se materializan las 10k tablas) y nombres de sus canvases activos.
    Devuelve listas CRUDAS (con None/vacíos); `service.filter_option_lists`
    las limpia y ordena."""
    db = await get_db()
    pipeline = [{"$match": scoped(project_id, ACTIVE)},
                {"$group": {"_id": "$schema", "n": {"$sum": 1}}}]
    schemas = await db["canonical_tables"].aggregate(pipeline).to_list(None)
    canvases = await db["subject_areas"].find(scoped(project_id, ACTIVE), {"name": 1}).to_list(None)
    return {"schemas": [s.get("_id") for s in schemas],
            "subjectAreas": [c.get("name") for c in canvases]}


async def column_counts(project_id: str) -> dict[str, int]:
    """Conteo de columnas activas POR tabla vía agregación server-side (`$group`).
    La fila del reporte sólo necesita `columnCount`, no los documentos de columna:
    materializar las columnas costaba ~24s a 400k docs; el `$group` (sobre el
    índice `tableId`) devuelve ~10k pares en ~2s."""
    db = await get_db()
    pipeline = [{"$match": scoped(project_id, ACTIVE)},
                {"$group": {"_id": "$tableId", "n": {"$sum": 1}}}]
    rows = await db["canonical_columns"].aggregate(pipeline).to_list(None)
    return {r["_id"]: r["n"] for r in rows if r.get("_id") is not None}


async def report_inputs(project_id: str) -> dict:
    """Todo lo que la agregación por tabla necesita, en una lectura por colección
    (las uniones se hacen en Python en `service`). Las columnas se traen YA
    CONTADAS por tabla (`columnCounts`), no como documentos — así el reporte
    escala a cientos de miles de columnas sin materializarlas."""
    return {
        "tables": await _find_active("canonical_tables", project_id),
        "columnCounts": await column_counts(project_id),
        "relationships": await _relationships(project_id),
        "subjectAreas": await _subject_areas(project_id),
    }


async def report_inputs_page(project_id: str, limit: int) -> dict:
    """Como `report_inputs` pero SOLO las primeras `limit` tablas (orden físico,
    índice `physicalName`) y sus conteos de columna (agregación acotada por
    `tableId $in`). Es el camino de la carga inicial del front (`limit=50`): evita
    tocar las 10k tablas / 400k columnas → sub-segundo en vez de varios segundos.
    Sólo válido SIN filtros (con `schema` hace falta el barrido completo)."""
    db = await get_db()
    docs = await db["canonical_tables"].find(scoped(project_id, ACTIVE)).sort("physicalName", 1).limit(limit).to_list(limit)
    tables = [_strip(d) for d in docs]
    ids = [t["id"] for t in tables]
    pipeline = [{"$match": scoped(project_id, {**ACTIVE, "tableId": {"$in": ids}})},
                {"$group": {"_id": "$tableId", "n": {"$sum": 1}}}]
    rows = await db["canonical_columns"].aggregate(pipeline).to_list(None)
    return {
        "tables": tables,
        "columnCounts": {r["_id"]: r["n"] for r in rows if r.get("_id") is not None},
        "relationships": await _relationships(project_id, ids),
        "subjectAreas": await _subject_areas(project_id, ids),
    }


async def columns(project_id: str, table_id: str | None = None,
                  table_ids: list[str] | None = None,
                  limit: int | None = None) -> list[dict]:
    """Columnas activas del proyecto; todas, las de una tabla (`tableId`) o de
    un lote (`tableIds`, `$in` sobre el índice tableId — para el export acotado).
    `limit` acota el resultado (tope de seguridad del export sin filtro: sin él,
    a 400k columnas la respuesta eran ~91MB)."""
    query = scoped(project_id, ACTIVE)
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


async def parent_domains(project_id: str) -> list[dict]:
    """Parent domains activos del proyecto (para resolver nombre del dominio en
    el export)."""
    return await _find_active("parent_domains", project_id)


async def udp_definitions(project_id: str) -> list[dict]:
    """Defs UDP activas del proyecto (para traducir `udpValues` {defId: valor}
    a nombres legibles en las filas del reporte/export)."""
    return await _find_active("udp_definitions", project_id)


async def views_for_tables(project_id: str, table_ids: list[str] | None = None,
                           limit: int | None = None,
                           schema: str | None = None) -> list[dict]:
    """Vistas activas del proyecto; con `table_ids`, solo las que derivan de
    alguna de esas tablas (`sourceTableIds` canónico, `tableId` legacy); con
    `schema`, solo las de ese esquema (Database Explorer, doc 18). Docs
    completos: el export necesita `sources` (alias/cast/expresión), `filter`,
    `sql`…"""
    db = await get_db()
    query = scoped(project_id, ACTIVE)
    if table_ids:
        query["$or"] = [{"sourceTableIds": {"$in": table_ids}},
                        {"tableId": {"$in": table_ids}}]
    if schema:
        query["schema"] = schema
    cur = db["views"].find(query)
    if limit:
        cur = cur.limit(limit)
    docs = await cur.to_list(limit or None)
    return [_strip(d) for d in docs]


async def table_names(ids: list[str]) -> dict[str, dict]:
    """{tableId: {physicalName, schema}} para resolver fuentes de vistas."""
    if not ids:
        return {}
    db = await get_db()
    docs = await db["canonical_tables"].find(
        {"_id": {"$in": ids}}, {"physicalName": 1, "schema": 1}).to_list(None)
    return {str(d["_id"]): d for d in docs}
