"""CRUD async de `views`."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.core.db.client import get_db
from app.core.scope import MissingProjectError

from .membership import filter_canvas_views
from .models import ViewDoc

COLL = "views"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to(doc: dict) -> dict:
    doc = dict(doc); doc["id"] = str(doc.pop("_id"))
    for k in ("flgactive", "deletedAt", "updatedAt", "createdAt"):
        doc.pop(k, None)
    return doc


def _dump(v: ViewDoc) -> dict:
    # by_alias: `schema` viaja como `schema` (no `sql_schema`), igual que catalog.
    return v.model_dump(by_alias=True)


def build_query(table_id: str | None = None, table_ids: list[str] | None = None,
                project_id: str | None = None) -> dict:
    """Query de `list_all`. Pura (testeable sin DB).

    F3: el match canónico es contra `sourceTableIds` (igualdad sobre array =
    contains; `$in` = intersección). El OR con `tableId` cubre docs legacy
    (anteriores a la materialización de `sourceTableIds`). Doc 75: con
    `project_id` acota al proyecto (única forma de listar SIN tabla)."""
    query: dict = {"flgactive": {"$ne": False}}
    if project_id:
        query["projectId"] = project_id
    if table_id is not None:
        query["$or"] = [{"sourceTableIds": table_id}, {"tableId": table_id}]
    elif table_ids:
        query["$or"] = [{"sourceTableIds": {"$in": table_ids}},
                        {"tableId": {"$in": table_ids}}]
    return query


async def list_all(table_id: str | None = None, table_ids: list[str] | None = None,
                   project_id: str | None = None) -> list[dict]:
    if table_id is None and not table_ids and not project_id:
        # Doc 75 D6: nunca se lista la colección completa cruzando proyectos.
        raise MissingProjectError("views.list_all requires a table or a project scope")
    db = await get_db()
    docs = await db[COLL].find(build_query(table_id, table_ids, project_id)).to_list(None)
    return [_dump(ViewDoc.model_validate(_to(d))) for d in docs]


async def rows_of_project(project_id: str) -> list[dict]:
    """Filas SLIM de las vistas del proyecto para el ÁRBOL del Explorer: sólo lo
    que se pinta (nombre, esquema, fuentes) + el CONTEO de columnas de salida.

    Perf (medido 2026-09-08): el doc completo de vista es enorme y el árbol casi
    no lo usa — en «Modelo DDV» las 1 932 vistas pesan 35.8 MB, de los cuales
    `sql` (22.2 MB, el preview congelado que el árbol NUNCA pinta) y `sources`
    (12.6 MB, sólo visibles al EXPANDIR la vista) son el 99 %; lo imprescindible
    son 0.37 MB. El `$size` lo resuelve Postgres, así que esos arrays ni salen
    de la BD. Las columnas se piden aparte al expandir (`catalog.view_columns`),
    igual que las de una tabla.
    """
    db = await get_db()
    pipeline = [
        {"$match": {"projectId": project_id, "flgactive": {"$ne": False}}},
        {"$project": {"name": 1, "schema": 1, "sourceTableIds": 1, "tableId": 1, "customSql": 1,
                      "nSources": {"$size": {"$ifNull": ["$sources", []]}},
                      "nCustom": {"$size": {"$ifNull": ["$customColumns", []]}}}},
    ]
    docs = await db[COLL].aggregate(pipeline).to_list(None)
    rows = []
    for d in docs:
        custom = bool((d.get("customSql") or "").strip())
        tid = d.get("tableId")
        rows.append({
            "id": str(d.get("_id") or d.get("id")),
            "name": d.get("name") or "",
            "schema": d.get("schema"),
            "sourceTableIds": list(d.get("sourceTableIds") or ([tid] if tid else [])),
            "columnCount": (d.get("nCustom") or 0) if custom else (d.get("nSources") or 0),
            "custom": custom,
        })
    return rows


async def get(vid: str) -> dict | None:
    """Vista ACTIVA por id (None si no existe / soft-deleted)."""
    db = await get_db()
    doc = await db[COLL].find_one({"_id": vid, "flgactive": {"$ne": False}})
    return _dump(ViewDoc.model_validate(_to(doc))) if doc else None


async def create(data: dict) -> dict:
    db = await get_db()
    v = ViewDoc.model_validate({**data, "id": data.get("id") or str(uuid.uuid4())})
    payload = _dump(v)
    await db[COLL].insert_one({"_id": v.id, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
                               **{k: val for k, val in payload.items() if k != "id"}})
    return payload


async def update(vid: str, data: dict) -> dict | None:
    db = await get_db()
    data = {k: v for k, v in data.items() if k not in ("id", "_id")}
    res = await db[COLL].find_one_and_update(
        {"_id": vid, "flgactive": {"$ne": False}}, {"$set": {**data, "updatedAt": _now()}},
        return_document=ReturnDocument.AFTER)
    return _dump(ViewDoc.model_validate(_to(res))) if res else None


async def delete(vid: str) -> bool:
    db = await get_db()
    res = await db[COLL].update_one({"_id": vid, "flgactive": {"$ne": False}},
                                    {"$set": {"flgactive": False, "deletedAt": _now()}})
    return res.modified_count > 0


def build_canvas_query(table_ids: list[str]) -> dict:
    """Query de vistas visibles en un canvas (F3): showOnCanvas=True y ≥1
    fuente presente. Pura. Matchea SOLO `sourceTableIds` (indexado): el flag
    nace en F3a, todo doc con True pasó por el write path nuevo que normaliza
    las fuentes — no hace falta fallback legacy aquí."""
    return {"flgactive": {"$ne": False}, "showOnCanvas": True,
            "sourceTableIds": {"$in": table_ids}}


async def list_for_canvas(table_ids: list[str]) -> list[dict]:
    """Vistas a dibujar en un canvas LEGACY (doc 10 D3: flag global, aparece
    en todo canvas que contenga ≥1 tabla fuente). Desde el doc 70 sólo se usa
    como fallback para canvases sin `viewIds` (ver `resolve_canvas_views`)."""
    if not table_ids:
        return []
    db = await get_db()
    docs = await db[COLL].find(build_canvas_query(table_ids)).to_list(None)
    return [_dump(ViewDoc.model_validate(_to(d))) for d in docs]


async def list_by_ids(ids: list[str]) -> list[dict]:
    """Vistas activas por id (`_id $in`), en el orden de `ids`."""
    if not ids:
        return []
    db = await get_db()
    docs = await db[COLL].find({"_id": {"$in": list(ids)}, "flgactive": {"$ne": False}}).to_list(None)
    by_id = {str(d["_id"]): _dump(ViewDoc.model_validate(_to(d))) for d in docs}
    return [by_id[i] for i in ids if i in by_id]


def normalize(doc: dict) -> dict:
    """Payload crudo (overlay de changeset) → forma canónica de la API
    (`ViewDoc` validado, `schema` por alias)."""
    return _dump(ViewDoc.model_validate(doc))


async def canvases_for_views(view_ids: list[str], table_id: str) -> list[dict]:
    """Canvases activos CANDIDATOS a contener alguna de las vistas de la
    tabla (doc 70): los que las listan explícitamente (`viewIds`) o los que
    contienen la tabla (regla legacy). Proyección liviana — sin layout/drawings.
    Devuelve [{id, name, tableIds, viewIds}]; `viewIds` None = canvas legacy."""
    if not table_id:
        return []
    db = await get_db()
    ors: list[dict] = [{"tableIds": table_id}]
    if view_ids:
        ors.append({"viewIds": {"$in": list(view_ids)}})
    docs = await db["subject_areas"].find(
        {"flgactive": {"$ne": False}, "$or": ors},
        {"name": 1, "tableIds": 1, "viewIds": 1},
    ).to_list(None)
    return [{"id": str(d["_id"]), "name": d.get("name") or "",
             "tableIds": d.get("tableIds") or [], "viewIds": d.get("viewIds")}
            for d in docs]


async def resolve_canvas_views(sa: dict) -> list[dict]:
    """Doc 70 §2.2 — ÚNICO punto de resolución de las vistas de un canvas:
    `viewIds` None (legacy) → regla vieja (`list_for_canvas`); lista → esas
    vistas, filtradas a las que conservan ≥1 fuente presente."""
    table_ids = list(sa.get("tableIds") or [])
    view_ids = sa.get("viewIds")
    if view_ids is None:
        return await list_for_canvas(table_ids)
    return filter_canvas_views(await list_by_ids(list(view_ids)), list(view_ids), table_ids)
