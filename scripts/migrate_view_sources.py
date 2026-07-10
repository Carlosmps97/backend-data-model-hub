"""Migración: acondiciona las vistas a la nueva forma columna-por-columna del
editor 07b-2. Las vistas legacy no tienen `sources` (proyección explícita), así
que el DDL/editor las trataba como `SELECT *`. Este script materializa `sources`
= todas las columnas de la tabla base (en orden ordinal) y regenera `sql` con la
lista explícita, para que NINGUNA vista quede como `SELECT *`.

Idempotente: salta las vistas que ya tienen `sources`.

    .venv/bin/python scripts/migrate_view_sources.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import UpdateOne

ACTIVE = {"flgactive": {"$ne": False}}


async def main() -> None:
    from app.core.db.client import connect, disconnect, get_db
    await connect()
    db = await get_db()

    print("[1/4] columnas por tabla (ordinal)…")
    cols: dict[str, list[str]] = {}
    pipe = [{"$match": ACTIVE},
            {"$group": {"_id": "$tableId", "cols": {"$push": {"n": "$physicalName", "o": "$ordinal"}}}}]
    async for r in db["canonical_columns"].aggregate(pipe, maxTimeMS=180000):
        cols[r["_id"]] = [x["n"] for x in sorted(r["cols"], key=lambda x: x.get("o", 0))]
    print(f"      {len(cols)} tablas con columnas")

    print("[2/4] meta de tablas (schema, physicalName)…")
    meta: dict[str, tuple[str, str]] = {}
    async for t in db["canonical_tables"].find(ACTIVE, {"schema": 1, "physicalName": 1}):
        meta[str(t["_id"])] = (t.get("schema") or "", t.get("physicalName") or str(t["_id"]))

    print("[3/4] recorriendo vistas…")
    ops: list[UpdateOne] = []
    migrated = skipped = nocols = 0
    async for v in db["views"].find(ACTIVE, {"tableId": 1, "name": 1, "schema": 1, "filter": 1, "sources": 1}):
        srcs = v.get("sources") or []
        # Ya migrada si tiene al menos una proyección por COLUMNA (formato nuevo).
        if any(isinstance(s, dict) and s.get("column") for s in srcs):
            skipped += 1
            continue
        tid = v.get("tableId")
        cl = cols.get(tid) if tid else None
        if not cl:
            nocols += 1
            continue
        sources = [{"column": c} for c in cl]
        vschema = v.get("schema") or meta.get(tid, ("", ""))[0] or "default"
        tschema, tphys = meta.get(tid, ("default", tid))
        frm = f"{tschema}.{tphys}" if tschema else tphys
        select = ",\n".join(f"  {c}" for c in cl)
        where = f"\nWHERE {v['filter'].strip()}" if v.get("filter") else ""
        sql = f"CREATE VIEW {vschema}.{v.get('name')} AS\nSELECT\n{select}\nFROM {frm}{where};"
        ops.append(UpdateOne({"_id": v["_id"]}, {"$set": {"sources": sources, "sql": sql}}))
        migrated += 1
        if len(ops) >= 300:
            await db["views"].bulk_write(ops)
            ops = []
    if ops:
        await db["views"].bulk_write(ops)

    print(f"[4/4] OK · migradas {migrated} · ya tenían sources {skipped} · sin columnas {nocols}")
    await disconnect()


if __name__ == "__main__":
    asyncio.run(main())
