"""One-shot: mueve el dict embebido `changes` de cada changeset a la colección
`changeset_changes` (UN doc por cambio) y quita el campo del doc padre.

Motivo: Cosmos RU capea 2MB por documento — un changeset con ~2-4k entidades
tocadas dejaba de poder guardarse. El backend ya lee/escribe SOLO la colección
nueva; este script recupera los cambios de docs legacy (drafts/requests creados
antes del refactor). Idempotente: re-ejecutarlo no duplica (upsert por `_id`
determinista) ni toca changesets ya migrados (sin campo `changes`).

Run:  backend-data-model-hub/.venv/bin/python scripts/migrate_changes_to_collection.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


async def main() -> None:
    from app.core.db.client import connect, disconnect, get_db

    await connect()
    db = await get_db()

    moved = migrated_docs = 0
    legacy = await db["changesets"].find(
        {"changes": {"$exists": True, "$nin": [None, {}]}}
    ).to_list(None)
    for cs in legacy:
        cs_id = str(cs["_id"])
        for collection, col_changes in (cs.get("changes") or {}).items():
            for eid, ch in (col_changes or {}).items():
                key = f"{cs_id}::{collection}::{eid}"
                doc = {
                    "_id": key,
                    "csId": cs_id,
                    "collection": collection,
                    "entityId": eid,
                    "op": ch.get("op") or "upsert",
                    "at": ch.get("at"),
                }
                if doc["op"] != "delete":
                    doc["payload"] = ch.get("payload") or {}
                await db["changeset_changes"].replace_one({"_id": key}, doc, upsert=True)
                moved += 1
        await db["changesets"].update_one({"_id": cs["_id"]}, {"$unset": {"changes": ""}})
        migrated_docs += 1

    # Limpieza del resto: changesets con `changes` vacío también sueltan el campo.
    res = await db["changesets"].update_many(
        {"changes": {"$exists": True}}, {"$unset": {"changes": ""}}
    )
    print(f"migrated {moved} changes from {migrated_docs} changesets "
          f"(+{res.modified_count} headers limpiados)")

    await disconnect()


if __name__ == "__main__":
    asyncio.run(main())
