"""Re-organiza (auto-arrange) TODOS los canvases (`subject_areas`) con elkjs — el
mismo motor y config que el botón "Autoarrange" del canvas. El seed de estrés dejó
las tablas en una grilla naïve (380px de alto) y las tablas de ~40 columnas miden
~1100px → se superponen. Este script estima el tamaño real de cada nodo (alto por
nº de columnas, ancho por el naming más largo), corre ELK por canvas y persiste
`subject_areas.layout`.

Pipeline: Python (datos) → arrange_all.cjs (elkjs) → Python (persistir).

    .venv/bin/python scripts/arrange_all.py
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import UpdateOne

SCRATCH = ("/private/tmp/claude-501/-Users-carlosperez-Desktop-Projects-Agentes-"
           "GitHub-WebApp-MODELER/57557898-1de4-4147-8757-4bb1c8fafee9/scratchpad")
ACTIVE = {"flgactive": {"$ne": False}}


def node_size(dims: dict, meta: dict, tid: str) -> tuple[int, int]:
    """Estimación del tamaño renderizado del nodo de tabla (coincide con el CSS
    `.wk-node`: ancho max-content acotado 240..520; alto = header + filas×~28)."""
    n, maxcol = dims.get(tid, (1, 12))
    schema, phys = meta.get(tid, ("", tid))
    header_chars = len(f"{schema}.{phys}") if schema else len(phys)
    chars = max(header_chars, maxcol + 3)          # +3 ≈ ícono de key + gap
    w = min(520, max(240, round(chars * 8 + 72)))  # ~8px/char mono + padding
    h = 32 + n * 28                                 # header + filas
    return w, h


async def main() -> None:
    from app.core.db.client import connect, disconnect, get_db
    await connect()
    db = await get_db()

    print("[1/5] dims de columnas por tabla…")
    dims: dict[str, tuple[int, int]] = {}
    pipe = [{"$group": {"_id": "$tableId", "n": {"$sum": 1}, "maxcol": {"$max": {"$add": [
        {"$strLenCP": {"$ifNull": ["$physicalName", ""]}},
        {"$strLenCP": {"$ifNull": ["$dataType", ""]}}]}}}}]
    async for r in db["canonical_columns"].aggregate(pipe, maxTimeMS=120000):
        dims[r["_id"]] = (r.get("n", 1), r.get("maxcol", 12) or 12)
    print(f"      {len(dims)} tablas con columnas")

    print("[2/5] meta de tablas (schema, physicalName)…")
    meta: dict[str, tuple[str, str]] = {}
    async for t in db["canonical_tables"].find(ACTIVE, {"schema": 1, "physicalName": 1}):
        meta[str(t["_id"])] = (t.get("schema") or "", t.get("physicalName") or str(t["_id"]))

    print("[3/5] relaciones + subject areas…")
    rels = [(str(r["_id"]), r.get("sourceTableId"), r.get("targetTableId"))
            async for r in db["relationships"].find(ACTIVE, {"sourceTableId": 1, "targetTableId": 1})]
    sas = [(str(sa["_id"]), sa.get("tableIds") or [])
           async for sa in db["subject_areas"].find(ACTIVE, {"tableIds": 1})]
    print(f"      {len(rels)} relaciones · {len(sas)} canvases")

    graphs: dict[str, dict] = {}
    for sa_id, tids in sas:
        if not tids:
            continue
        tset = set(tids)
        nodes = [{"id": tid, "w": (wh := node_size(dims, meta, tid))[0], "h": wh[1]} for tid in tids]
        edges = [{"id": rid, "source": s, "target": t} for (rid, s, t) in rels if s in tset and t in tset]
        graphs[sa_id] = {"nodes": nodes, "edges": edges}

    gin, gout = os.path.join(SCRATCH, "arrange_graphs.json"), os.path.join(SCRATCH, "arrange_positions.json")
    Path(gin).write_text(json.dumps(graphs))
    print(f"[4/5] elkjs sobre {len(graphs)} canvases…")
    subprocess.run(["node", str(Path(__file__).parent / "arrange_all.cjs"), gin, gout], check=True)
    positions = json.loads(Path(gout).read_text())

    print("[5/5] persistiendo layouts (bulk)…")
    ops = [UpdateOne({"_id": sa_id}, {"$set": {"layout": layout}}) for sa_id, layout in positions.items()]
    for i in range(0, len(ops), 200):
        await db["subject_areas"].bulk_write(ops[i:i + 200])
    print(f"OK · {len(ops)} canvases re-organizados con ELK")
    await disconnect()


if __name__ == "__main__":
    asyncio.run(main())
