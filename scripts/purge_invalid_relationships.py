"""Depuración one-off (doc 47): relaciones PUBLICADAS que no migran la llave
completa del padre (N≠N) — basura de pruebas manuales pre-regla; los modelos
migrados de Erwin son N=N por diseño. NO es una migración: se BORRAN.

Por defecto DRY-RUN (reporte, cero escrituras). Con `--apply`: soft-delete
(flgactive=False + deletedAt — la MISMA semántica que repository.delete) y
limpieza conservadora del flag FK huérfano (la columna hija pierde
isForeignKey sólo si ninguna relación superviviente la tiene de hija).

Uso (desde la raíz del backend, con la BD alcanzable; también pegable en un
notebook contra el Lakebase corporativo — patrón docs 35/36):
    .venv/bin/python scripts/purge_invalid_relationships.py            # dry-run
    .venv/bin/python scripts/purge_invalid_relationships.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db.client import connect, get_db  # noqa: E402
from app.features.relationships.models import RelationshipDoc  # noqa: E402

ACT = {"flgactive": {"$ne": False}}


def classify(rels: list[dict], pk_by_table: dict[str, set[str]]) -> list[dict]:
    """Relaciones cuyo set de columnas padre de los pares ≠ llave del padre.
    Devuelve [{id, parentTableId, childTableId, expected, got, pairs}]. Pura."""
    bad = []
    for r in rels:
        pks = pk_by_table.get(r.get("parentTableId"), set())
        parents = {p.get("parentColumnId") for p in (r.get("pairs") or [])}
        if parents != pks:
            bad.append({"id": r["id"], "parentTableId": r.get("parentTableId"),
                        "childTableId": r.get("childTableId"),
                        "expected": len(pks), "got": len(r.get("pairs") or []),
                        "pairs": r.get("pairs") or []})
    return bad


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def main(apply: bool) -> None:
    await connect()
    db = await get_db()

    raw = await db["relationships"].find(ACT).to_list(None)
    rels = []
    for d in raw:
        d = dict(d)
        d["id"] = str(d.pop("_id"))
        rels.append(RelationshipDoc.model_validate(d).model_dump())

    cols = await db["canonical_columns"].find(
        ACT, {"tableId": 1, "isPrimaryKey": 1}).to_list(None)
    pk_by_table: dict[str, set[str]] = {}
    for c in cols:
        pk_by_table.setdefault(c.get("tableId"), set())
        if c.get("isPrimaryKey"):
            pk_by_table[c["tableId"]].add(str(c["_id"]))

    tabs = await db["canonical_tables"].find(ACT, {"physicalName": 1}).to_list(None)
    tname = {str(t["_id"]): t.get("physicalName") or str(t["_id"]) for t in tabs}

    bad = classify(rels, pk_by_table)
    print(f"Relaciones activas: {len(rels)} · inconsistentes (N≠N): {len(bad)}")
    for b in bad:
        print(f"  - {b['id']}: {tname.get(b['parentTableId'], b['parentTableId'])} → "
              f"{tname.get(b['childTableId'], b['childTableId'])} · llave={b['expected']} pares={b['got']}")
    if not apply:
        print("DRY-RUN: nada se escribió. Repetir con --apply para depurar.")
        return

    bad_ids = {b["id"] for b in bad}
    survivors = [r for r in rels if r["id"] not in bad_ids]
    still_child = {p.get("childColumnId") for r in survivors for p in (r.get("pairs") or [])}
    for b in bad:
        await db["relationships"].update_one(
            {"_id": b["id"]}, {"$set": {"flgactive": False, "deletedAt": _now()}})
    cleaned = 0
    orphan_ids = sorted({p.get("childColumnId") for b in bad for p in b["pairs"]} - still_child)
    for cid in orphan_ids:
        res = await db["canonical_columns"].update_one(
            {"_id": cid, "isForeignKey": True, **ACT},
            {"$set": {"isForeignKey": False, "updatedAt": _now()}})
        cleaned += res.modified_count
    print(f"APLICADO: {len(bad)} relación(es) depuradas · {cleaned} flag(s) FK huérfanos limpiados.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="escribe (default: dry-run)")
    asyncio.run(main(ap.parse_args().apply))
