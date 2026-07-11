"""Migración F3a: materializa `sourceTableIds` en las vistas activas.

Para cada vista activa SIN `sourceTableIds` (ausente o vacío) y CON `tableId`:
    sourceTableIds = [tableId]
`tableId` se conserva tal cual (compat legacy / rollback — spec 10 §3-5).

Idempotente: salta las vistas que ya tienen `sourceTableIds`.
DRY-RUN por defecto (solo conteos); `--apply` para escribir.

    .venv/bin/python scripts/migrate_view_source_tables.py          # dry-run
    .venv/bin/python scripts/migrate_view_source_tables.py --apply  # escribe
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import UpdateOne

ACTIVE = {"flgactive": {"$ne": False}}
BATCH = 300


def build_ops(views: list[dict]) -> tuple[list[UpdateOne], int, int, int]:
    """Pura: (ops, a_migrar, ya_migradas, sin_tableid) sobre docs crudos."""
    ops: list[UpdateOne] = []
    migrate = skipped = no_table = 0
    for v in views:
        if v.get("sourceTableIds"):
            skipped += 1
            continue
        tid = v.get("tableId")
        if not tid:
            no_table += 1
            continue
        ops.append(UpdateOne({"_id": v["_id"]}, {"$set": {"sourceTableIds": [tid]}}))
        migrate += 1
    return ops, migrate, skipped, no_table


async def main() -> None:
    apply = "--apply" in sys.argv[1:]
    from app.core.db.client import connect, disconnect, get_db
    await connect()
    db = await get_db()

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[1/2] recorriendo vistas activas… ({mode})")
    views = await db["views"].find(ACTIVE, {"tableId": 1, "sourceTableIds": 1}).to_list(None)
    ops, migrate, skipped, no_table = build_ops(views)

    if apply and ops:
        for i in range(0, len(ops), BATCH):
            await db["views"].bulk_write(ops[i:i + BATCH])
    verb = "migradas" if apply else "a migrar (dry-run, nada escrito)"
    print(f"[2/2] OK · total activas {len(views)} · {verb} {migrate} · "
          f"ya tenían sourceTableIds {skipped} · sin tableId {no_table}")
    await disconnect()


if __name__ == "__main__":
    asyncio.run(main())
