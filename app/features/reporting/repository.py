"""Lecturas de solo lectura para `reporting` (único punto que toca el store en
esta feature; guardrail `tests/architecture/test_store_boundary.py`).

Sólo lee documentos ACTIVOS (`flgactive != False`) de UN proyecto (doc 75:
toda lectura de una colección de alcance va con `scoped(project_id, …)`).
Devuelve dicts crudos normalizados (`_id` -> `id`, sin campos internos del
store) con EXACTAMENTE los campos que la agregación pura de `service` consume — incluido `schema`, que en
la BD se guarda con esa clave (el modelo de `catalog` lo aliasa a `sql_schema`,
pero aquí lo dejamos como `schema` para la salida del reporte).

Doc 105: también lee TAJADAS del ledger (`changeset_changes`) de una versión
propia — por `_id` determinista o por el extremo de una relación —, para que un
lote del export no relea el ledger entero (ver `relationship_changes_touching`).
"""
from __future__ import annotations

from app.core.db.client import get_db
from app.core.scope import scoped
from app.features.changesets import repository as cs_repo

from .draft import REL_ENDS

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
_SA_LITE = {"name": 1, "projectId": 1, "tableIds": 1, "viewIds": 1, "folderId": 1}   # viewIds: doc 70 · folderId: doc 88
_FOLDER_LITE = {"name": 1, "parentFolderId": 1}
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


async def _folders(project_id: str) -> list[dict]:
    """Carpetas del proyecto (id/name/parentFolderId): doc 88 §7, el subject
    area de una tabla es la CARPETA que contiene el canvas, no el canvas."""
    return await _find_active("folders", project_id, _FOLDER_LITE)


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
    # Doc 102: con el id, la versión propia puede superponerse (overlay).
    return [{"id": str(d["_id"]),
             "parentTableId": d.get("parentTableId") or d.get("targetTableId"),
             "childTableId": d.get("childTableId") or d.get("sourceTableId")}
            for d in docs]


async def filter_options(project_id: str) -> dict:
    """Doc 70 §4.1: universo de los filtros del reporte tabular SIN paginar —
    esquemas distintos de las tablas activas del proyecto (agregación `$group`,
    no se materializan las 10k tablas) y, doc 88 §7, nombres de las CARPETAS
    que contienen sus canvases activos (un canvas en la raíz no aporta).
    Devuelve listas CRUDAS (con None/vacíos); `service.filter_option_lists`
    las limpia y ordena."""
    db = await get_db()
    pipeline = [{"$match": scoped(project_id, ACTIVE)},
                {"$group": {"_id": "$schema", "n": {"$sum": 1}}}]
    schemas = await db["canonical_tables"].aggregate(pipeline).to_list(None)
    canvases = await db["subject_areas"].find(scoped(project_id, ACTIVE), {"folderId": 1}).to_list(None)
    folder_name = {f["id"]: f.get("name") for f in await _folders(project_id)}
    return {"schemas": [s.get("_id") for s in schemas],
            "subjectAreas": [folder_name.get(str(c.get("folderId"))) for c in canvases if c.get("folderId")]}


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
        "folders": await _folders(project_id),
    }


async def report_inputs_page(project_id: str, limit: int, offset: int = 0) -> dict:
    """Como `report_inputs` pero SOLO una PÁGINA de tablas (orden físico,
    índice `physicalName`; `offset` = doc 92 D3, scroll infinito del front) y
    sus conteos de columna (agregación acotada por `tableId $in`). Es el camino
    de la carga inicial del front (`limit=50`): evita tocar las 10k tablas /
    400k columnas → sub-segundo en vez de varios segundos. Sólo válido SIN
    filtros (con `schema` hace falta el barrido completo)."""
    db = await get_db()
    cur = db["canonical_tables"].find(scoped(project_id, ACTIVE)).sort("physicalName", 1)
    if offset:
        cur = cur.skip(offset)
    docs = await cur.limit(limit).to_list(limit)
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
        "folders": await _folders(project_id),
    }


async def count_tables(project_id: str) -> int:
    """Doc 92 D4: total de tablas ACTIVAS del proyecto (conteo real para la
    barra del Reporting y el Select all, aunque la grilla tenga cargada sólo
    una página)."""
    db = await get_db()
    return int(await db["canonical_tables"].count_documents(scoped(project_id, ACTIVE)))


async def table_ids(project_id: str) -> list[str]:
    """Doc 102: ids de las tablas ACTIVAS del proyecto (conteo de una versión
    propia)."""
    return [d["id"] for d in await _find_active("canonical_tables", project_id, {"_id": 1})]


async def table_schemas(project_id: str) -> list[dict]:
    """Doc 102: {id, schema} de las tablas activas (filtros de una versión propia)."""
    return await _find_active("canonical_tables", project_id, {"schema": 1})


async def canvas_folders(project_id: str) -> list[dict]:
    """Doc 102: {id, folderId} de los canvases activos (filtros de una versión propia)."""
    return await _find_active("subject_areas", project_id, {"folderId": 1})


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


# ── Tajadas del ledger de una versión PROPIA (changeset real, no `asof:`) ────
# Doc 105 (revisión, hallazgo 1): un lote del export lee SÓLO los cambios que
# lo tocan, con la misma forma que `changes_map(...)[colección]` (mismo
# `_ledger_entry`). El `_id` de cada cambio es determinista (`change_key`).

def _ledger_entries(docs: list[dict]) -> dict[str, dict]:
    """{entityId: cambio} de docs del ledger; una edición que llega por dos
    lecturas se valida una vez."""
    unique = {d["_id"]: d for d in docs}.values()
    return {entity_id: entry for _, entity_id, entry in map(cs_repo.ledger_entry, unique)}


async def _ledger_by_keys(cs_id: str, collection: str, ids) -> list[dict]:
    if not ids:
        return []
    db = await get_db()
    keys = [cs_repo.change_key(cs_id, collection, i) for i in ids]
    return await db[cs_repo.CHANGES_COLL].find({"_id": {"$in": keys}}).to_list(None)


async def version_changes_by_ids(cs_id: str, collection: str, ids) -> dict[str, dict]:
    """Cambios de `collection` de la versión para ESOS ids (por su `_id`), sin
    leer el resto del ledger: los nombres de tablas de los pares de un lote."""
    return _ledger_entries(await _ledger_by_keys(cs_id, collection, ids))


async def relationship_changes_touching(cs_id: str, relationship_ids: list[str],
                                        table_ids: list[str]) -> dict[str, dict]:
    """Cambios de `relationships` de la versión que tocan un LOTE de tablas:
    los de las relaciones ya leídas (`relationship_ids`, por `_id` — ediciones,
    bajas y mudanzas fuera del lote) y los upserts con un extremo, v2 o legacy,
    en el lote (`payload.<extremo>`: altas, revividas y mudanzas hacia el lote).
    El overlay (`draft.overlay_relationships`) decide cuáles entran."""
    docs = await _ledger_by_keys(cs_id, "relationships", relationship_ids)
    if table_ids:
        db = await get_db()
        docs += await db[cs_repo.CHANGES_COLL].find({
            "csId": cs_id, "collection": "relationships",
            "$or": [{f"payload.{end}": {"$in": table_ids}} for end in REL_ENDS]}).to_list(None)
    return _ledger_entries(docs)
