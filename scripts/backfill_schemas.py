"""Backfill de la entidad `schemas` (doc 18) desde los esquemas YA existentes.

Idempotente: por cada nombre distinto de `schema` en canonical_tables ∪ views
(activos) que no tenga doc activo en la colección `schemas`, inserta uno con
id determinista `sch-<name>` (mismo esquema de ids que el seed sintético).
NUNCA borra ni modifica esquemas existentes.

    .venv/bin/python -m scripts.backfill_schemas            # dry-run
    .venv/bin/python -m scripts.backfill_schemas --apply    # inserta faltantes
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.db.client import connect, disconnect, get_db  # noqa: E402

ACTIVE = {"flgactive": {"$ne": False}}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run(apply: bool) -> int:
    await connect()
    db = await get_db()
    used: set[str] = set()
    for coll in ("canonical_tables", "views"):
        for name in await db[coll].distinct("schema", ACTIVE):
            if name and str(name).strip():
                used.add(str(name).strip())
    existing = {str(d.get("name") or "").strip()
                for d in await db["schemas"].find(ACTIVE, {"name": 1}).to_list(None)}
    missing = sorted(used - existing)
    print(f"esquemas en uso: {len(used)} · ya catalogados: {len(existing)} · faltantes: {len(missing)}")
    for name in missing:
        print(f"  + {name}")
    if not apply:
        print("\nDRY-RUN (sin --apply no se escribe nada).")
    elif missing:
        now = _now()
        await db["schemas"].insert_many([
            {"_id": f"sch-{name}", "name": name, "description": None,
             "flgactive": True, "createdAt": now, "updatedAt": now}
            for name in missing
        ])
        print(f"\ninsertados {len(missing)} esquemas.")
    else:
        print("\nnada que insertar.")
    await disconnect()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill de la colección `schemas`")
    ap.add_argument("--apply", action="store_true", help="inserta los faltantes (sin esto: dry-run)")
    args = ap.parse_args(argv)
    return asyncio.run(run(args.apply))


if __name__ == "__main__":
    sys.exit(main())
