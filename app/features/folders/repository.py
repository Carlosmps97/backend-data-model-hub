"""CRUD async de `folders` (jerarquía anidada del Model Explorer).

Único punto que toca el store para esta feature (guardrail
`tests/architecture/test_store_boundary.py`).

Decisión de borrado en cascada (documentada por requerimiento):
`delete_folder` hace soft-delete (`flgactive=False`) de la carpeta y de TODAS
sus subcarpetas descendientes (transitivo). Los canvases (colección
`subject_areas`, aún sin renombrar) contenidos en cualquiera de esas carpetas NO
se borran: se "desadjuntan" poniendo `folderId=None` para que caigan a la raíz
del proyecto en el Model Explorer y sigan siendo accesibles. El conjunto de
descendientes se calcula en Python (BFS sobre los docs vivos del proyecto):
el store no soporta consultas recursivas y así evitamos N round-trips por nivel.
El ordenamiento (`order`, luego `name`) también se hace en Python porque a
escala un orden sin índice sería full-scan.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.core.db.client import get_db

from .models import FolderDoc

FOLDERS = "folders"
SUBJECT_AREAS = "subject_areas"  # = "canvases" (rename pendiente, fase posterior)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    for k in ("flgactive", "deletedAt", "updatedAt", "createdAt"):
        doc.pop(k, None)
    return doc


def _order_key(d: dict) -> tuple:
    return (d.get("order") or 0, (d.get("name") or "").lower())


async def list_folders(project_id: str) -> list[dict]:
    db = await get_db()
    docs = await db[FOLDERS].find(
        {"projectId": project_id, "flgactive": {"$ne": False}}
    ).to_list(None)
    docs.sort(key=_order_key)  # ordenar en Python (a escala, campo sin índice)
    return [FolderDoc.model_validate(_to(d)).model_dump() for d in docs]


async def get_folder(folder_id: str) -> dict | None:
    db = await get_db()
    doc = await db[FOLDERS].find_one({"_id": folder_id, "flgactive": {"$ne": False}})
    return FolderDoc.model_validate(_to(doc)).model_dump() if doc else None


async def create_folder(data: dict) -> dict:
    db = await get_db()
    f = FolderDoc.model_validate({**data, "id": data.get("id") or str(uuid.uuid4())})
    await db[FOLDERS].insert_one(
        {"_id": f.id, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
         **{k: v for k, v in f.model_dump().items() if k != "id"}}
    )
    return f.model_dump()


async def update_folder(folder_id: str, fields: dict) -> dict | None:
    db = await get_db()
    fields = {k: v for k, v in fields.items() if k not in ("id", "_id", "projectId")}
    if not fields:
        return await get_folder(folder_id)
    res = await db[FOLDERS].find_one_and_update(
        {"_id": folder_id, "flgactive": {"$ne": False}},
        {"$set": {**fields, "updatedAt": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    return FolderDoc.model_validate(_to(res)).model_dump() if res else None


async def delete_folder(folder_id: str) -> bool:
    """Borra la carpeta + descendientes (cascada). Ver docstring del módulo."""
    db = await get_db()
    target = await db[FOLDERS].find_one({"_id": folder_id, "flgactive": {"$ne": False}})
    if not target:
        return False

    # Descendientes: BFS en Python sobre las carpetas vivas del proyecto.
    project_id = target.get("projectId")
    siblings = await db[FOLDERS].find(
        {"projectId": project_id, "flgactive": {"$ne": False}}
    ).to_list(None)
    children: dict[str | None, list[str]] = {}
    for d in siblings:
        children.setdefault(d.get("parentFolderId"), []).append(str(d["_id"]))

    to_delete: list[str] = [folder_id]
    stack = [folder_id]
    while stack:
        current = stack.pop()
        for child_id in children.get(current, []):
            to_delete.append(child_id)
            stack.append(child_id)

    # Canvases contenidos → desadjuntar (folderId=None), no borrar.
    await db[SUBJECT_AREAS].update_many(
        {"folderId": {"$in": to_delete}, "flgactive": {"$ne": False}},
        {"$set": {"folderId": None, "updatedAt": _now()}},
    )
    res = await db[FOLDERS].update_many(
        {"_id": {"$in": to_delete}},
        {"$set": {"flgactive": False, "deletedAt": _now()}},
    )
    return res.modified_count > 0
