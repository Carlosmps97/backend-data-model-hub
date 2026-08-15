"""Backfill de `schemas.kind` (doc 44) sobre la BD PUBLICADA.

El XML de Erwin no trae ningún atributo/UDP que marque un esquema como "de
vistas" (verificado sobre los `Hive_Database` de DDV - CPYBCA.xml), así que la
clasificación nace del USO real en la BD:

  - tiene >= 1 tabla activa           -> kind = "tables"   (mixto: tables gana)
  - solo vistas activas               -> kind = "views"
  - sin tablas ni vistas (vacío)      -> se deja SIN tocar (se reporta)

Por defecto DRY-RUN (no escribe nada). Con `--apply` escribe y re-lee para
verificar. Con `--force` re-clasifica también los que ya tienen kind.

Aviso adicional: un changeset DRAFT con upserts de `schemas` cuyo payload no
trae kind PISARÍA este backfill al publicarse (el payload es el doc completo).
El script los cuenta; re-correrlo después de publicar lo repara.

Uso (desde la raíz del backend, con la BD alcanzable):
    .venv/bin/python scripts/backfill_schema_kind.py            # dry-run
    .venv/bin/python scripts/backfill_schema_kind.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db.client import connect, get_db  # noqa: E402

VALID = ("tables", "views")
ACT = {"flgactive": {"$ne": False}}


async def main(apply: bool, force: bool) -> None:
    await connect()
    db = await get_db()

    schemas = await db["schemas"].find(ACT).to_list(None)
    t_sch = {(t.get("schema") or "").strip()
             for t in await db["canonical_tables"].find(ACT, {"schema": 1}).to_list(None)}
    v_sch = {(v.get("schema") or "").strip()
             for v in await db["views"].find(ACT, {"schema": 1}).to_list(None)}

    plan: list[tuple[str, str, str | None, str]] = []
    unused: list[str] = []
    kept = 0
    for s in schemas:
        name = (s.get("name") or "").strip()
        cur = s.get("kind")
        if cur in VALID and not force:
            kept += 1
            continue
        kind = "tables" if name in t_sch else "views" if name in v_sch else None
        if kind is None:
            unused.append(name)
        elif kind != cur:
            plan.append((str(s["_id"]), name, cur, kind))

    n_t = sum(1 for *_x, k in plan if k == "tables")
    n_v = sum(1 for *_x, k in plan if k == "views")
    print(f"esquemas activos: {len(schemas)} | ya clasificados (se conservan): {kept}")
    print(f"a clasificar: {len(plan)} (tables={n_t} · views={n_v}) | sin uso (quedan sin kind): {len(unused)}")
    for _id, name, cur, kind in plan[:10]:
        print(f"  {name}: {cur!r} -> {kind}")
    if len(plan) > 10:
        print(f"  … y {len(plan) - 10} más")
    if unused:
        extra = " …" if len(unused) > 10 else ""
        print("sin uso:", ", ".join(sorted(unused)[:10]) + extra)

    # Drafts cuyos upserts de schemas no traen kind (pisarían el backfill).
    drafts = {str(c["_id"]) for c in await db["changesets"].find(
        {"status": "draft"}, {"_id": 1}).to_list(None)}
    stale = [c for c in await db["changeset_changes"].find(
        {"collection": "schemas", "op": "upsert"}).to_list(None)
        if str(c.get("csId")) in drafts and (c.get("payload") or {}).get("kind") not in VALID]
    if stale:
        by_cs: dict[str, int] = {}
        for c in stale:
            by_cs[str(c.get("csId"))] = by_cs.get(str(c.get("csId")), 0) + 1
        print(f"AVISO: {len(stale)} upsert(s) de schemas SIN kind en draft(s) {sorted(by_cs)} "
              "— al publicar pisarían la clasificación de esos esquemas; re-correr este script después.")

    if not apply:
        print("\nDRY-RUN: no se escribió nada. Corre con --apply para aplicar.")
        return

    now = datetime.now(timezone.utc).isoformat()
    for _id, _name, _cur, kind in plan:
        await db["schemas"].update_one({"_id": _id}, {"$set": {"kind": kind, "updatedAt": now}})

    fresh = await db["schemas"].find(ACT).to_list(None)
    counts = {"tables": 0, "views": 0, "sin_kind": 0}
    for s in fresh:
        k = s.get("kind")
        counts[k if k in VALID else "sin_kind"] += 1
    print(f"\nAPLICADO ({len(plan)} esquemas). read-back: tables={counts['tables']} "
          f"views={counts['views']} sin_kind={counts['sin_kind']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="escribe (default: dry-run)")
    ap.add_argument("--force", action="store_true", help="re-clasifica también los ya clasificados")
    args = ap.parse_args()
    asyncio.run(main(apply=args.apply, force=args.force))
