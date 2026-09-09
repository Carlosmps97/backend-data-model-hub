"""CRUD async de `canonical_tables` + `canonical_columns` (col. separada)."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from app.core.db.client import get_db
from app.core.scope import scoped

from .models import CanonicalColumnDoc, CanonicalTableDoc

TABLES = "canonical_tables"
COLUMNS = "canonical_columns"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_doc(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    for k in ("flgactive", "deletedAt", "updatedAt", "createdAt"):
        doc.pop(k, None)
    return doc


async def list_tables(project_id: str, q: str | None = None, limit: int | None = None,
                      schema: str | None = None, ids: list[str] | None = None,
                      skip: int = 0) -> list[dict]:
    """Tablas DEL PROYECTO (doc 75 D6: el catálogo ya no es un pool universal).
    `q` busca por nombre físico/lógico (contains,
    case-insensitive, server-side) y `limit` capea el resultado: los modales de
    catálogo a 15k tablas NO deben bajar la colección completa para filtrar en
    el cliente. `schema` acota a UN esquema (server-side, junto a `q`): el filtro
    por esquema del modal Import existing NO puede resolverse en el cliente sobre
    la página de `limit` — se perdían las tablas del esquema fuera de esa página.
    Sin parámetros, todas las tablas del proyecto."""
    db = await get_db()
    flt: dict = scoped(project_id, {"flgactive": {"$ne": False}})
    if schema:
        flt["schema"] = schema
    if ids is not None:
        # doc 70 §11: acota al alcance (tablas de los canvases del filtro).
        flt["_id"] = {"$in": list(ids)}
    if q:
        rx = {"$regex": re.escape(q), "$options": "i"}
        flt["$or"] = [{"physicalName": rx}, {"logicalName": rx}]
    cursor = db[TABLES].find(flt)
    if limit is not None and limit > 0:
        # el sort por physicalName requiere el índice canonical_tables.physicalName
        # (core/db/indexes.py): a escala, un orden sin índice sería full-scan.
        # `skip` (doc 72 r2): página N del buscador (OFFSET en la BD).
        cursor = cursor.sort("physicalName", 1)
        if skip:
            cursor = cursor.skip(skip)
        cursor = cursor.limit(limit)
    docs = await cursor.to_list(None)
    docs.sort(key=lambda d: (d.get("physicalName") or "").lower())
    return [CanonicalTableDoc.model_validate(_to_doc(d)).model_dump(by_alias=True) for d in docs]


async def search_columns(project_id: str, q: str, limit: int) -> list[dict]:
    """Búsqueda por nombre de COLUMNA DEL PROYECTO (contains, case-insensitive) para
    el Database Explorer: proyección liviana ordenada por physicalName (índice
    canonical_columns.physicalName) y capada a `limit` — a 400k columnas la
    colección NO se baja para filtrar en el cliente."""
    db = await get_db()
    rx = {"$regex": re.escape(q), "$options": "i"}
    cursor = db[COLUMNS].find(
        scoped(project_id, {"flgactive": {"$ne": False}, "$or": [{"physicalName": rx}, {"logicalName": rx}]})
    ).sort("physicalName", 1).limit(limit)
    docs = await cursor.to_list(None)
    return [{
        "id": str(d["_id"]), "tableId": d.get("tableId"),
        "physicalName": d.get("physicalName"), "logicalName": d.get("logicalName"),
        "dataType": d.get("dataType"),
    } for d in docs]


async def search_columns_by(project_id: str, q: str, limit: int, fields: tuple[str, ...],
                            extra: dict | None = None, skip: int = 0) -> list[dict]:
    """Doc 70 §11 — búsqueda de columnas DEL PROYECTO por `fields` (contains,
    case-insensitive, regex escapado: sin ReDoS/inyección) para el buscador del
    Model: nombres (`physicalName`/`logicalName`) o DEFINICIÓN (`description`).
    Proyección liviana + `description`, ordenada por `physicalName` (índice
    canonical_columns.physicalName) y paginada con `skip`/`limit` (doc 72 r2:
    el buscador carga por bloques al hacer scroll). Sin término (`q` vacío =
    navegación por filtros) la búsqueda por DEFINICIÓN devuelve solo columnas
    que tienen una."""
    db = await get_db()
    flt: dict = scoped(project_id, {"flgactive": {"$ne": False}, **(extra or {})})
    if q:
        rx = {"$regex": re.escape(q), "$options": "i"}
        flt["$or"] = [{f: rx} for f in fields]
    elif fields == ("description",):
        flt["description"] = {"$regex": ".", "$options": "i"}
    cursor = db[COLUMNS].find(flt).sort("physicalName", 1)
    if skip:
        cursor = cursor.skip(skip)
    cursor = cursor.limit(limit)
    docs = await cursor.to_list(None)
    return [{
        "id": str(d["_id"]), "tableId": d.get("tableId"),
        "physicalName": d.get("physicalName"), "logicalName": d.get("logicalName"),
        "dataType": d.get("dataType"), "description": d.get("description"),
        "parentDomainId": d.get("parentDomainId"),
        "ordinal": d.get("ordinal", 0),
    } for d in docs]


async def list_folders_lite(project_id: str) -> list[dict]:
    """Carpetas activas DEL PROYECTO con su padre — para resolver el SUBÁRBOL de
    un sub-proyecto/dominio en el alcance del buscador."""
    db = await get_db()
    flt: dict = scoped(project_id, {"flgactive": {"$ne": False}})
    docs = await db["folders"].find(flt, {"projectId": 1, "parentFolderId": 1, "name": 1}).to_list(None)
    return [{"id": str(d["_id"]), "projectId": d.get("projectId"),
             "parentFolderId": d.get("parentFolderId"), "name": d.get("name") or ""} for d in docs]


async def canvases_in_scope(project_id: str, folder_ids: list[str] | None,
                            canvas_id: str | None) -> list[dict]:
    """Canvases activos DEL PROYECTO en el alcance del buscador (doc 70 §11):
    carpetas (`folderId $in` — el SUBÁRBOL del sub-proyecto/dominio elegido, ya
    resuelto por el service) y/o modelo (canvas). Proyección liviana."""
    db = await get_db()
    flt: dict = scoped(project_id, {"flgactive": {"$ne": False}})
    if canvas_id:
        flt["_id"] = canvas_id
    if folder_ids is not None:
        flt["folderId"] = {"$in": list(folder_ids)}
    docs = await db["subject_areas"].find(flt, {"name": 1, "projectId": 1, "folderId": 1, "tableIds": 1}).to_list(None)
    return [{"id": str(d["_id"]), "name": d.get("name") or "", "projectId": d.get("projectId"),
             "folderId": d.get("folderId"), "tableIds": d.get("tableIds") or []} for d in docs]


async def canvases_with_tables(table_ids: list[str]) -> list[dict]:
    """Canvases activos que referencian ALGUNA de las tablas (`tableIds $in`),
    proyección liviana — el buscador del Model resuelve «dónde está» cada hit."""
    if not table_ids:
        return []
    db = await get_db()
    docs = await db["subject_areas"].find(
        {"flgactive": {"$ne": False}, "tableIds": {"$in": list(table_ids)}},
        _SA_LITE,
    ).to_list(None)
    return [_sa_lite(d) for d in docs]


# Proyección liviana de un canvas (sin layout/drawings): lo que necesitan el
# buscador, el inventario del Explorer y el inspector (doc 72). `viewIds`
# ausente/None = canvas legacy (doc 70) — se conserva tal cual.
_SA_LITE = {"name": 1, "projectId": 1, "folderId": 1, "tableIds": 1, "viewIds": 1}


def _sa_lite(d: dict) -> dict:
    return {"id": str(d["_id"]), "name": d.get("name") or "", "projectId": d.get("projectId"),
            "folderId": d.get("folderId"), "tableIds": d.get("tableIds") or [],
            "viewIds": d.get("viewIds")}


async def canvases_of_project(project_id: str) -> list[dict]:
    """Canvases activos de UN proyecto, proyección liviana (inventario del
    Explorer, doc 72 §1)."""
    db = await get_db()
    docs = await db["subject_areas"].find(
        scoped(project_id, {"flgactive": {"$ne": False}}), _SA_LITE,
    ).to_list(None)
    return [_sa_lite(d) for d in docs]


async def table_ids_of_project(project_id: str) -> list[str]:
    """Todas las tablas ACTIVAS del proyecto (doc 75 §6.2: el alcance es el
    `projectId`, no la unión de canvases — una tabla sin canvas también es del
    proyecto). Orden por nombre físico (índice (project_id, physicalName))."""
    db = await get_db()
    docs = await db[TABLES].find(scoped(project_id, {"flgactive": {"$ne": False}}),
                                 {"_id": 1}).sort("physicalName", 1).to_list(None)
    return [str(d["_id"]) for d in docs]


_TABLE_LITE = {"physicalName": 1, "logicalName": 1, "schema": 1, "description": 1}


async def table_rows_of_project(project_id: str) -> list[dict]:
    """Filas LITE de las tablas del proyecto para el ÁRBOL del Explorer: sólo
    los campos que el inventario pinta. Doc 75 §6.2: el alcance es el
    `projectId` — una tabla sin canvas también es del proyecto.

    Sustituye al par `table_ids_of_project` + `list_tables_by_ids`, que eran DOS
    consultas a la MISMA colección para lo mismo (0.80 s + 1.44 s en «Modelo
    DDV») por UNA con proyección (0.43 s, medido 2026-09-08): el doc completo de
    tabla trae metadata que el árbol no usa y validar 2 129 docs con Pydantic
    tampoco es gratis. `list_tables_by_ids` se conserva intacta para el resto de
    llamadores (canvas, proyectos, DDL), que sí necesitan el doc completo."""
    db = await get_db()
    docs = await db[TABLES].find(scoped(project_id, {"flgactive": {"$ne": False}}),
                                 _TABLE_LITE).to_list(None)
    return [{"id": str(d["_id"]), "physicalName": d.get("physicalName") or "",
             "logicalName": d.get("logicalName") or "", "schema": d.get("schema"),
             "description": d.get("description")} for d in docs]


async def project_of_table(table_id: str) -> str | None:
    """Proyecto dueño de una tabla activa, o None si no existe."""
    db = await get_db()
    doc = await db[TABLES].find_one({"_id": table_id, "flgactive": {"$ne": False}}, {"projectId": 1})
    return doc.get("projectId") if doc else None


async def count_active(project_id: str, collection: str) -> int:
    """Conteo de documentos ACTIVOS del proyecto en `collection`."""
    db = await get_db()
    return await db[collection].count_documents(scoped(project_id, {"flgactive": {"$ne": False}}))


async def column_counts_for(project_id: str) -> dict[str, int]:
    """Conteo de columnas activas por tabla DEL PROYECTO (`$match` + `$group`
    server-side): el Explorer muestra el badge sin materializar columnas.

    Perf (medido 2026-09-08 en UDV INT FISICO, 2 288 tablas / 46 196 columnas):
    acotar por `projectId` (índice) tarda **0.9 s** contra **27 s** del
    `tableId: {"$in": [...2 288 ids]}` que se usaba antes — el `$in` masivo era
    el 85 % del tiempo del inventario y lo que disparaba «Couldn't load the
    project catalog». Ambos devuelven EXACTAMENTE los mismos grupos y conteos
    (verificado); por proyecto puede devolver tablas fuera del alcance del
    draft, pero es inofensivo: `project_inventory` sólo lee las del alcance."""
    db = await get_db()
    pipeline = [{"$match": {"flgactive": {"$ne": False}, "projectId": project_id}},
                {"$group": {"_id": "$tableId", "n": {"$sum": 1}}}]
    rows = await db[COLUMNS].aggregate(pipeline).to_list(None)
    return {r["_id"]: r["n"] for r in rows if r.get("_id") is not None}


async def column_tables_by_ids(column_ids: list[str]) -> dict[str, str]:
    """{columnId: tableId} de las columnas PUBLICADAS activas entre `column_ids`
    — para ajustar los conteos con los cambios de columnas del draft (doc 72)."""
    if not column_ids:
        return {}
    db = await get_db()
    docs = await db[COLUMNS].find(
        {"_id": {"$in": list(column_ids)}, "flgactive": {"$ne": False}}, {"tableId": 1},
    ).to_list(None)
    return {str(d["_id"]): d.get("tableId") for d in docs if d.get("tableId")}


async def list_tables_by_ids(table_ids: list[str]) -> list[dict]:
    """Slice del pool por ids (`$in`): el armado de un canvas de 100 tablas no
    debe cargar las 15k del catálogo para filtrar en Python."""
    if not table_ids:
        return []
    db = await get_db()
    docs = await db[TABLES].find(
        {"_id": {"$in": table_ids}, "flgactive": {"$ne": False}}
    ).to_list(None)
    docs.sort(key=lambda d: (d.get("physicalName") or "").lower())
    return [CanonicalTableDoc.model_validate(_to_doc(d)).model_dump(by_alias=True) for d in docs]


async def list_columns_for_tables(table_ids: list[str]) -> list[dict]:
    """Columnas de VARIAS tablas en UNA query (`tableId $in`, usa el índice
    tableId) — reemplaza el N+1 de una query por tabla del canvas."""
    if not table_ids:
        return []
    db = await get_db()
    docs = await db[COLUMNS].find(
        {"tableId": {"$in": table_ids}, "flgactive": {"$ne": False}}
    ).to_list(None)
    docs.sort(key=lambda d: (d.get("tableId") or "", d.get("ordinal") or 0))
    return [CanonicalColumnDoc.model_validate(_to_doc(d)).model_dump() for d in docs]


async def canvases_with_table(table_id: str) -> list[dict]:
    """Canvases activos que referencian la tabla (V3, doc 19 §12b) — proyección
    liviana con `tableIds` (lo necesita el overlay del changeset)."""
    db = await get_db()
    docs = await db["subject_areas"].find(
        {"flgactive": {"$ne": False}, "tableIds": table_id},
        {"name": 1, "folderId": 1, "projectId": 1, "tableIds": 1},
    ).to_list(None)
    return [{"id": str(d["_id"]), "name": d.get("name"), "folderId": d.get("folderId"),
             "projectId": d.get("projectId"), "tableIds": d.get("tableIds") or []}
            for d in docs]


async def names_by_ids(collection: str, ids: list[str]) -> dict[str, str]:
    """{id: name} de folders/projects activos (para armar las filas de uso)."""
    if not ids:
        return {}
    db = await get_db()
    docs = await db[collection].find(
        {"_id": {"$in": ids}, "flgactive": {"$ne": False}}, {"name": 1}
    ).to_list(None)
    return {str(d["_id"]): d.get("name") or str(d["_id"]) for d in docs}


async def create_table(data: dict) -> dict:
    db = await get_db()
    t = CanonicalTableDoc.model_validate({**data, "id": data.get("id") or str(uuid.uuid4())})
    payload = t.model_dump(by_alias=True)
    await db[TABLES].insert_one(
        {"_id": t.id, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
         **{k: v for k, v in payload.items() if k != "id"}}
    )
    return payload


async def list_columns(table_id: str) -> list[dict]:
    db = await get_db()
    docs = await db[COLUMNS].find(
        {"tableId": table_id, "flgactive": {"$ne": False}}
    ).to_list(None)
    docs.sort(key=lambda d: d.get("ordinal") or 0)
    return [CanonicalColumnDoc.model_validate(_to_doc(d)).model_dump() for d in docs]


async def create_column(data: dict) -> dict:
    db = await get_db()
    c = CanonicalColumnDoc.model_validate({**data, "id": data.get("id") or str(uuid.uuid4())})
    payload = c.model_dump()
    await db[COLUMNS].insert_one(
        {"_id": c.id, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
         **{k: v for k, v in payload.items() if k != "id"}}
    )
    return payload


async def domain_default(parent_domain_id: str) -> str | None:
    db = await get_db()
    d = await db["parent_domains"].find_one({"_id": parent_domain_id}, {"defaultDataType": 1})
    return d.get("defaultDataType") if d else None
