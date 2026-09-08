"""Queries guardadas (saved reports): un `QuerySpec` con nombre, reutilizable y
compartible. Colección `saved_reports`. El spec es JSON portable chico.
Doc 75: cada reporte pertenece a UN proyecto (`projectId`, igual al del spec)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from app.core.db.client import get_db
from app.core.scope import scoped

COLL = "saved_reports"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SavedReportBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    projectId: str = Field(min_length=1)
    name: str
    description: str | None = None
    spec: dict                      # QuerySpec serializado
    shared: bool = False
    folderId: str | None = None


class SavedReportDoc(SavedReportBody):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    owner: str
    createdAt: str | None = None
    updatedAt: str | None = None


def _to(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    doc.pop("flgactive", None)
    return doc


async def list_reports(owner: str, project_id: str) -> list[dict]:
    """Reportes del proyecto: los del owner + los compartidos por otros."""
    db = await get_db()
    docs = await db[COLL].find(scoped(project_id, {
        "flgactive": {"$ne": False}, "$or": [{"owner": owner}, {"shared": True}]})).to_list(None)
    docs.sort(key=lambda d: (d.get("name") or "").lower())
    return [_to(d) for d in docs]


async def create_report(owner: str, body: SavedReportBody) -> dict:
    db = await get_db()
    d = SavedReportDoc(owner=owner, createdAt=_now(), updatedAt=_now(), **body.model_dump())
    await db[COLL].insert_one({"_id": d.id, "flgactive": True,
                               **{k: v for k, v in d.model_dump().items() if k != "id"}})
    return d.model_dump()


async def update_report(owner: str, rid: str, body: SavedReportBody) -> dict | None:
    from pymongo import ReturnDocument
    db = await get_db()
    # Un reporte no cambia de proyecto: el filtro exige el mismo `projectId`.
    res = await db[COLL].find_one_and_update(
        scoped(body.projectId, {"_id": rid, "owner": owner, "flgactive": {"$ne": False}}),
        {"$set": {**body.model_dump(), "updatedAt": _now()}}, return_document=ReturnDocument.AFTER)
    return _to(res) if res else None


async def delete_report(owner: str, rid: str) -> bool:
    db = await get_db()
    res = await db[COLL].update_one({"_id": rid, "owner": owner, "flgactive": {"$ne": False}},
                                    {"$set": {"flgactive": False, "deletedAt": _now()}})
    return res.modified_count > 0
