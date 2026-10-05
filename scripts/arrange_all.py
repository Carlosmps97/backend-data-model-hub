"""Re-organiza (auto-arrange) TODOS los canvases (`subject_areas`) con elkjs —
mismo motor y config que el botón "Autoarrange". Incluye **tablas Y nodos de
vista**: si solo se arreglan las tablas, el front coloca cada vista a +280px de
su fuente (default de viewGraph.ts) y con tablas anchas la vista queda ENCIMA de
su tabla. Acá las vistas entran al grafo ELK (con su tamaño real + el wire de
derivación tabla→vista) → ELK las separa y persistimos su posición también.

Pipeline: Python (datos) → arrange_all.cjs (elkjs) → Python (persistir).

    .venv/bin/python scripts/arrange_all.py [--project "Nombre"]

`--project` (doc 32b): limita el arrange a los canvases de ESE proyecto —
imprescindible en cargas incrementales para no pisar los layouts ya
trabajados de los proyectos/archivos anteriores.

Doc 99: cada canvas que se arregla pierde sus trazos manuales de wires
(`routes`) — con las posiciones nuevas ya no calzan; los wires vuelven al
camino automático. En el one-shot los canvases son nuevos: no hay trazos.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Consola en UTF-8 (Windows viene en cp1252 y estos prints llevan acentos).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from pymongo import UpdateOne

from scripts.erwin_migration.layout import APP_HEADER_H, APP_ROW_H, box_width, view_box, view_rows

# Directorio temporal para el hand-off Python↔elkjs (estable, no acoplado a una
# sesión). Override con la env var ARRANGE_SCRATCH si se quiere otro sitio.
SCRATCH = os.environ.get("ARRANGE_SCRATCH", tempfile.gettempdir())
ACTIVE = {"flgactive": {"$ne": False}}


def table_size(dims: dict, meta: dict, tid: str) -> tuple[int, int]:
    """Tamaño renderizado del nodo TABLA (coincide con `.wk-node`: ancho
    max-content acotado 240..880, header = SOLO esquema.nombre del modo activo).
    Se toma el MÁXIMO entre naming físico y lógico: el layout persistido debe
    servir en ambos modos de vista sin solaparse."""
    n, maxcol = dims.get(tid, (1, 12))
    schema, phys, logical = meta.get(tid, ("", tid, tid))
    name = max(phys, logical, key=len)
    header_chars = len(f"{schema}.{name}") if schema else len(name)
    chars = max(header_chars, maxcol + 3)
    # Doc 109: la MISMA métrica que usa la migración (erwin_migration/layout.py).
    return box_width(chars), APP_HEADER_H + n * APP_ROW_H


def view_size(v: dict, col_types: dict[tuple[str, str], str]) -> tuple[int, int]:
    """Tamaño del nodo VISTA (`.wk-vnode`: header + 1 fila por source con su
    tipo). Doc 109: la MISMA métrica que la migración (`layout.view_box`)."""
    return view_box(v.get("schema") or "", v.get("name") or "", view_rows(v.get("sources") or [], col_types))


def view_src_ids(v: dict) -> list[str]:
    s = v.get("sourceTableIds") or []
    return s if s else ([v["tableId"]] if v.get("tableId") else [])


def layout_updates(positions: dict[str, dict]) -> list[UpdateOne]:
    """Escrituras del arrange, una por canvas. Doc 99: el arrange cambia TODAS
    las posiciones del canvas, así que sus trazos manuales de wires (`routes`)
    dejan de calzar con los bloques — se reinician en la misma escritura (como
    el botón Autoarrange de la web). Los canvases que el arrange no tocó no
    aparecen en `positions`: conservan posiciones y trazos. Puro."""
    return [UpdateOne({"_id": sa_id}, {"$set": {"layout": layout, "routes": {}}})
            for sa_id, layout in positions.items()]


async def main(project: str | None = None) -> None:
    from app.core.db.client import connect, disconnect, get_db
    await connect()
    db = await get_db()

    sa_filter = dict(ACTIVE)
    if project:
        p = await db["projects"].find_one({"name": project, **ACTIVE}, {"_id": 1})
        if not p:
            print(f"ERROR: el proyecto '{project}' no existe")
            await disconnect()
            return
        sa_filter["projectId"] = str(p["_id"])
        print(f"(solo canvases del proyecto '{project}')")

    print("[1/6] dims de columnas por tabla…")
    dims: dict[str, tuple[int, int]] = {}
    # maxphys/maxlog por separado; el máximo entre ambos modos se resuelve
    # en Python (evita $max de expresiones anidadas en el adaptador).
    pipe = [{"$group": {"_id": "$tableId", "n": {"$sum": 1},
                        "maxphys": {"$max": {"$add": [
                            {"$strLenCP": {"$ifNull": ["$physicalName", ""]}},
                            {"$strLenCP": {"$ifNull": ["$dataType", ""]}}]}},
                        "maxlog": {"$max": {"$add": [
                            {"$strLenCP": {"$ifNull": ["$logicalName", ""]}},
                            {"$strLenCP": {"$ifNull": ["$dataType", ""]}}]}}}}]
    async for r in db["canonical_columns"].aggregate(pipe, maxTimeMS=120000):
        maxcol = max(r.get("maxphys") or 12, r.get("maxlog") or 12)
        dims[r["_id"]] = (r.get("n", 1), maxcol)
    print(f"      {len(dims)} tablas con columnas")

    print("[2/6] meta de tablas (schema, physicalName, logicalName)…")
    meta: dict[str, tuple[str, str, str]] = {}
    async for t in db["canonical_tables"].find(ACTIVE, {"schema": 1, "physicalName": 1, "logicalName": 1}):
        meta[str(t["_id"])] = (t.get("schema") or "",
                               t.get("physicalName") or str(t["_id"]),
                               t.get("logicalName") or t.get("physicalName") or str(t["_id"]))

    print("[3/6] relaciones, vistas (showOnCanvas) y canvases…")
    # v2 parent/child (doc 19) con fallback legacy; la dirección da igual para ELK.
    rels = [(str(r["_id"]),
             r.get("childTableId") or r.get("sourceTableId"),
             r.get("parentTableId") or r.get("targetTableId"))
            async for r in db["relationships"].find(
                ACTIVE, {"parentTableId": 1, "childTableId": 1,
                         "sourceTableId": 1, "targetTableId": 1})]
    views: list[dict] = []
    async for v in db["views"].find({**ACTIVE, "showOnCanvas": True},
                                    {"sourceTableIds": 1, "tableId": 1, "name": 1, "schema": 1, "sources": 1}):
        v["_id"] = str(v["_id"])
        views.append(v)
    sas = [(str(sa["_id"]), sa.get("tableIds") or [])
           async for sa in db["subject_areas"].find(sa_filter, {"tableIds": 1})]
    print(f"      {len(rels)} relaciones · {len(views)} vistas · {len(sas)} canvases")
    # Doc 109: tipo de cada columna que muestran las vistas (mide la fila).
    col_types: dict[tuple[str, str], str] = {}
    src_tables = sorted({s.get("tableId") for v in views for s in (v.get("sources") or []) if s.get("tableId")})
    for i in range(0, len(src_tables), 500):
        async for c in db["canonical_columns"].find({**ACTIVE, "tableId": {"$in": src_tables[i:i + 500]}},
                                                    {"tableId": 1, "physicalName": 1, "dataType": 1}):
            col_types[(c["tableId"], (c.get("physicalName") or "").upper())] = c.get("dataType") or ""

    graphs: dict[str, dict] = {}
    for sa_id, tids in sas:
        if not tids:
            continue
        tset = set(tids)
        nodes = [{"id": tid, "w": (wh := table_size(dims, meta, tid))[0], "h": wh[1]} for tid in tids]
        edges = [{"id": rid, "source": s, "target": t} for (rid, s, t) in rels if s in tset and t in tset]
        # Vistas cuyo origen está en el canvas (MISMA regla que el diagrama del
        # backend: showOnCanvas + sourceTableIds ∩ tableIds). Se agregan como
        # nodo + wire de derivación tabla→vista para que ELK las ubique.
        for v in views:
            present = [tid for tid in view_src_ids(v) if tid in tset]
            if not present:
                continue
            vw, vh = view_size(v, col_types)
            nodes.append({"id": v["_id"], "w": vw, "h": vh})
            for tid in present:
                edges.append({"id": f"deriv__{v['_id']}__{tid}", "source": tid, "target": v["_id"]})
        graphs[sa_id] = {"nodes": nodes, "edges": edges}

    gin, gout = os.path.join(SCRATCH, "arrange_graphs.json"), os.path.join(SCRATCH, "arrange_positions.json")
    Path(gin).write_text(json.dumps(graphs), encoding="utf-8")
    print(f"[4/6] elkjs sobre {len(graphs)} canvases (tablas+vistas)…")
    subprocess.run(["node", str(Path(__file__).parent / "arrange_all.cjs"), gin, gout], check=True)
    positions = json.loads(Path(gout).read_text(encoding="utf-8"))

    print("[5/6] persistiendo layouts (bulk)…")
    ops = layout_updates(positions)
    for i in range(0, len(ops), 200):
        await db["subject_areas"].bulk_write(ops[i:i + 200])
    print(f"[6/6] OK · {len(ops)} canvases re-organizados con ELK (tablas+vistas)")
    await disconnect()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Auto-arrange ELK de canvases")
    ap.add_argument("--project", help="limitar al proyecto con este nombre")
    args = ap.parse_args()
    asyncio.run(main(args.project))
