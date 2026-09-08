"""CRUD async de `projects` + `subject_areas`."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.core.db.client import get_db
from app.core.scope import PROJECT_SCOPED, scoped

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


_ALIVE = {"flgactive": {"$ne": False}}


# ── Projects ──
async def get_project(pid: str) -> dict | None:
    """Proyecto ACTIVO o None (borrado/inexistente). Doc 75 D5/I11."""
    db = await get_db()
    doc = await db[PROJECTS].find_one({"_id": pid, **_ALIVE})
    return ProjectDoc.model_validate(_to(doc)).model_dump() if doc else None


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


async def find_by_name(name: str) -> dict | None:
    """Proyecto ACTIVO por nombre exacto case-insensitive (unicidad global del
    nombre, doc 75 D6)."""
    db = await get_db()
    doc = await db[PROJECTS].find_one({**_ALIVE, "name": {"$regex": f"^{re.escape(name.strip())}$", "$options": "i"}})
    return ProjectDoc.model_validate(_to(doc)).model_dump() if doc else None


async def discard_project(pid: str) -> None:
    """Retira un proyecto cuya creación falló a mitad (nunca llegó a ser visible)."""
    db = await get_db()
    await db[PROJECTS].update_one({"_id": pid}, {"$set": {"flgactive": False, "deletedAt": _now()}})


# Doc 75 D5: colecciones que se soft-deletean en cascada al aplicar el borrado
# del proyecto. `changesets`/`standards_versions` son historial: no se tocan.
CASCADE_COLLECTIONS: tuple[str, ...] = tuple(sorted(PROJECT_SCOPED - {"changesets", "standards_versions"}))
_COUNT_LABELS = {"canonical_tables": "tables", "canonical_columns": "columns", "views": "views",
                 "relationships": "relationships", "schemas": "schemas", "folders": "folders",
                 "subject_areas": "canvases"}


async def count_scope(pid: str) -> dict[str, int]:
    """Conteos de activos del proyecto (aviso crítico del borrado, doc 75 D5)."""
    db = await get_db()
    return {label: await db[coll].count_documents({**scoped(pid), **_ALIVE})
            for coll, label in _COUNT_LABELS.items()}


async def cascade_delete(pid: str, cs_id: str) -> dict[str, int]:
    """Doc 75 D5: al aplicar el changeset que borra el proyecto, soft-delete de
    TODO lo suyo (modelo + estándares) — una `update_many` por colección.
    Idempotente (sólo toca activos)."""
    db = await get_db()
    now = _now()
    stamp = {"$set": {"flgactive": False, "deletedAt": now, "deletedIn": cs_id, "updatedAt": now}}
    counts: dict[str, int] = {}
    for coll in CASCADE_COLLECTIONS:
        res = await db[coll].update_many({**scoped(pid), **_ALIVE}, stamp)
        counts[coll] = res.modified_count
    users = await db["users"].find({"projectIds": pid}, {"projectIds": 1}).to_list(None)
    for u in users:
        await db["users"].update_one({"_id": u["_id"]},
                                     {"$set": {"projectIds": [p for p in u.get("projectIds") or [] if p != pid]}})
    return counts


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
