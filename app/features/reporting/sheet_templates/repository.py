"""CRUD async de `sheet_templates` (doc 95 D11). Alcance por proyecto en TODA
lectura/escritura (`scoped`); soft-delete. Quién ve qué (las suyas + las
compartidas) lo filtra `list_templates`; quién edita lo decide el servicio."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.core.db.client import get_db
from app.core.scope import scoped

from .models import SheetTemplateDoc

COLL = "sheet_templates"
_AUDIT = ("flgactive", "deletedAt", "createdAt", "updatedAt")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_doc(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    meta = {k: doc.pop(k, None) for k in _AUDIT}
    out = SheetTemplateDoc.model_validate(doc).model_dump()
    out["createdAt"], out["updatedAt"] = meta["createdAt"], meta["updatedAt"]
    return out


async def list_templates(project_id: str, username: str) -> list[dict]:
    """Plantillas activas DEL PROYECTO que ve `username`: las suyas + las
    compartidas (como los saved reports), por nombre."""
    db = await get_db()
    docs = await db[COLL].find(scoped(project_id, {
        "flgactive": {"$ne": False}, "$or": [{"owner": username}, {"shared": True}]})).to_list(None)
    out = [_to_doc(d) for d in docs]
    out.sort(key=lambda t: (t["name"] or "").lower())
    return out


async def get_template(project_id: str, template_id: str) -> dict | None:
    """Una plantilla activa del proyecto (sin filtro de visibilidad)."""
    db = await get_db()
    doc = await db[COLL].find_one(scoped(project_id, {"_id": template_id, "flgactive": {"$ne": False}}))
    return _to_doc(doc) if doc else None


async def has_origin(project_id: str, origin: str) -> bool:
    """¿El proyecto ya tiene una plantilla activa de ese origen? (la vea o no
    quien pregunta: la built-in se siembra una vez por proyecto)."""
    db = await get_db()
    doc = await db[COLL].find_one(scoped(project_id, {"origin": origin, "flgactive": {"$ne": False}}), {"_id": 1})
    return doc is not None


async def create_template(project_id: str, data: dict) -> dict:
    db = await get_db()
    d = SheetTemplateDoc.model_validate({**data, "projectId": project_id, "id": data.get("id") or str(uuid.uuid4())})
    payload = d.model_dump()
    now = _now()
    await db[COLL].insert_one({"_id": d.id, "flgactive": True, "createdAt": now, "updatedAt": now,
                               **{k: v for k, v in payload.items() if k != "id"}})
    return {**payload, "createdAt": now, "updatedAt": now}


async def update_template(project_id: str, template_id: str, data: dict) -> dict | None:
    """Reemplaza el contenido editable (nombre, hoja, descripción, columnas,
    compartida); el origen, el proyecto, el dueño y el autor no cambian."""
    db = await get_db()
    sets = {k: v for k, v in data.items() if k not in ("id", "projectId", "origin", "owner", "createdBy")}
    res = await db[COLL].find_one_and_update(
        scoped(project_id, {"_id": template_id, "flgactive": {"$ne": False}}),
        {"$set": {**sets, "updatedAt": _now()}}, return_document=ReturnDocument.AFTER)
    return _to_doc(res) if res else None


async def delete_template(project_id: str, template_id: str) -> bool:
    db = await get_db()
    res = await db[COLL].update_one(scoped(project_id, {"_id": template_id, "flgactive": {"$ne": False}}),
                                    {"$set": {"flgactive": False, "deletedAt": _now()}})
    return res.modified_count > 0
