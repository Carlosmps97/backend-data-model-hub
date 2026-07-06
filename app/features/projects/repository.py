"""CRUD async de `projects` + `subject_areas`."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.core.db.client import get_db

from .models import ProjectDoc, SubjectAreaDoc

PROJECTS = "projects"
SUBJECT_AREAS = "subject_areas"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    for k in ("flgactive", "deletedAt", "updatedAt", "createdAt"):
        doc.pop(k, None)
    return doc


# ── Projects ──
async def list_projects() -> list[dict]:
    db = await get_db()
    docs = await db[PROJECTS].find({"flgactive": {"$ne": False}}).to_list(None)
    docs.sort(key=lambda d: (d.get("name") or "").lower())
    return [ProjectDoc.model_validate(_to(d)).model_dump() for d in docs]


async def create_project(data: dict) -> dict:
    db = await get_db()
    p = ProjectDoc.model_validate({**data, "id": data.get("id") or str(uuid.uuid4())})
    await db[PROJECTS].insert_one({"_id": p.id, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
                                   **{k: v for k, v in p.model_dump().items() if k != "id"}})
    return p.model_dump()


async def update_project(pid: str, data: dict) -> dict | None:
    db = await get_db()
    data = {k: v for k, v in data.items() if k not in ("id", "_id")}
    res = await db[PROJECTS].find_one_and_update(
        {"_id": pid, "flgactive": {"$ne": False}}, {"$set": {**data, "updatedAt": _now()}},
        return_document=ReturnDocument.AFTER)
    return ProjectDoc.model_validate(_to(res)).model_dump() if res else None


async def delete_project(pid: str) -> bool:
    db = await get_db()
    # Borra el proyecto sólo si está activo (idempotencia → 404 en el 2º delete),
    # y cascada el soft-delete a sus canvases Y sus carpetas (antes las folders
    # quedaban huérfanas y seguían accesibles).
    res = await db[PROJECTS].update_one(
        {"_id": pid, "flgactive": {"$ne": False}},
        {"$set": {"flgactive": False, "deletedAt": _now()}})
    if res.modified_count == 0:
        return False
    stamp = {"$set": {"flgactive": False, "deletedAt": _now()}}
    await db[SUBJECT_AREAS].update_many({"projectId": pid}, stamp)
    await db["folders"].update_many({"projectId": pid}, stamp)
    return True


# ── Subject Areas ──
async def list_subject_areas(project_id: str, folder_id: str | None = None) -> list[dict]:
    db = await get_db()
    query: dict = {"projectId": project_id, "flgactive": {"$ne": False}}
    if folder_id is not None:
        query["folderId"] = folder_id  # agrupar canvases por carpeta (aditivo)
    docs = await db[SUBJECT_AREAS].find(query).to_list(None)
    docs.sort(key=lambda d: (d.get("name") or "").lower())
    return [SubjectAreaDoc.model_validate(_to(d)).model_dump() for d in docs]


async def get_subject_area(sa_id: str) -> dict | None:
    db = await get_db()
    doc = await db[SUBJECT_AREAS].find_one({"_id": sa_id, "flgactive": {"$ne": False}})
    return SubjectAreaDoc.model_validate(_to(doc)).model_dump() if doc else None


async def create_subject_area(data: dict) -> dict:
    db = await get_db()
    sa = SubjectAreaDoc.model_validate({**data, "id": data.get("id") or str(uuid.uuid4())})
    await db[SUBJECT_AREAS].insert_one({"_id": sa.id, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
                                        **{k: v for k, v in sa.model_dump().items() if k != "id"}})
    return sa.model_dump()


async def update_subject_area(sa_id: str, fields: dict) -> dict | None:
    db = await get_db()
    fields = {k: v for k, v in fields.items() if k not in ("id", "_id")}
    res = await db[SUBJECT_AREAS].find_one_and_update(
        {"_id": sa_id, "flgactive": {"$ne": False}}, {"$set": {**fields, "updatedAt": _now()}},
        return_document=ReturnDocument.AFTER)
    return SubjectAreaDoc.model_validate(_to(res)).model_dump() if res else None


async def delete_subject_area(sa_id: str) -> bool:
    db = await get_db()
    # Filtra flgactive: un id inexistente o un doble-delete → modified_count 0 →
    # False → 404 (antes re-tocaba deletedAt y devolvía True falsamente).
    res = await db[SUBJECT_AREAS].update_one(
        {"_id": sa_id, "flgactive": {"$ne": False}},
        {"$set": {"flgactive": False, "deletedAt": _now()}})
    return res.modified_count > 0
