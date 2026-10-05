"""CRUD async de `diagram_themes` (doc 109). Las escrituras las hace el apply
versionado de Data Standards (`themesUpsert`/`themesDelete`) y su rollback; la
lectura es abierta (el canvas la usa para pintar las cajas)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo import UpdateOne

from app.core.db.client import get_db
from app.core.scope import scoped

from .models import ThemeDoc

COLL = "diagram_themes"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_doc(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    for k in ("flgactive", "deletedAt", "updatedAt", "createdAt", "erwinLongId", "migratedFrom"):
        doc.pop(k, None)
    return doc


async def list_themes(project_id: str) -> list[dict]:
    """Themes activos DEL PROYECTO, en su orden (y por nombre a igual orden)."""
    db = await get_db()
    docs = await db[COLL].find(scoped(project_id, {"flgactive": {"$ne": False}})).to_list(None)
    out = [ThemeDoc.model_validate(_to_doc(d)).model_dump() for d in docs]
    out.sort(key=lambda t: (t.get("order") or 0, (t.get("name") or "").lower()))
    return out


async def create_theme(project_id: str, data: dict) -> dict:
    db = await get_db()
    t = ThemeDoc.model_validate({**data, "projectId": project_id, "id": data.get("id") or str(uuid.uuid4())})
    payload = t.model_dump()
    await db[COLL].insert_one({"_id": t.id, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
                               **{k: v for k, v in payload.items() if k != "id"}})
    return payload


async def update_theme(theme_id: str, data: dict) -> None:
    db = await get_db()
    clean = {k: v for k, v in data.items() if k in ("name", "color", "order")}
    if "color" in clean and isinstance(clean["color"], str):
        clean["color"] = clean["color"].strip().upper()
    await db[COLL].update_one({"_id": theme_id, "flgactive": {"$ne": False}},
                              {"$set": {**clean, "updatedAt": _now()}})


async def delete_theme(theme_id: str) -> None:
    db = await get_db()
    await db[COLL].update_one({"_id": theme_id, "flgactive": {"$ne": False}},
                              {"$set": {"flgactive": False, "deletedAt": _now()}})


async def restore_themes(project_id: str, themes: list[dict]) -> None:
    """Rollback: deja activos SOLO los themes del snapshot (con su nombre, color
    y orden) — los demás se dan de baja. ACOTADO al proyecto (doc 75 I5)."""
    db = await get_db()
    keep = [t["id"] for t in themes if t.get("id")]
    await db[COLL].update_many(scoped(project_id, {"_id": {"$nin": keep}, "flgactive": {"$ne": False}}),
                               {"$set": {"flgactive": False, "deletedAt": _now()}})
    ops = []
    for t in themes:
        if not t.get("id"):
            continue
        doc = ThemeDoc.model_validate({**t, "projectId": project_id}).model_dump()
        ops.append(UpdateOne({"_id": doc["id"]},
                             {"$set": {"flgactive": True, "updatedAt": _now(),
                                       **{k: v for k, v in doc.items() if k != "id"}},
                              "$setOnInsert": {"createdAt": _now()}}, upsert=True))
    if ops:
        await db[COLL].bulk_write(ops, ordered=False)
