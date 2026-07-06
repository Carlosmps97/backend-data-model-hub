"""Genera data sintética A ESCALA para pruebas de estrés del Data Model Hub.

Objetivo (configurable por env):
- `N_TABLES` (10000) tablas canónicas con ~`COLS_PER_TABLE` (40) columnas c/u
  (≈400k columnas).
- `VIEW_FRACTION` (0.8) de las tablas con al menos una vista (≈8k vistas).
- Canvases variados; `BIG_CANVASES` (30) con exactamente `BIG_SIZE` (100) tablas
  y relaciones entidad-relación (crow's-foot) para el caso de estrés del lienzo.

REGENERA (drop): projects, folders, subject_areas, canonical_tables,
canonical_columns, relationships, views. LIMPIA (drop): changesets,
changeset_changes (referencian tablas viejas). **PRESERVA**: users, roles,
standards_versions, parent_domains, glossary_terms, naming_config y NUNCA
toca `column_catalog` (del agente).

Inserta por lotes (`insert_many`, `ordered=False`) en streaming (bajo consumo de
memoria) con progreso y **tolerancia a throttling de Cosmos (429 / code 16500)**:
reintenta el lote con backoff exponencial. Recrea los índices al final.

Run:  backend-data-model-hub/.venv/bin/python scripts/seed_stress.py
Escala chica para probar:  N_TABLES=100 BIG_CANVASES=2 .venv/bin/python scripts/seed_stress.py
"""
from __future__ import annotations

import asyncio
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

random.seed(20260705)  # reproducible

# ── Configuración (env-overridable) ──────────────────────────────────────────
N_TABLES = int(os.getenv("N_TABLES", "10000"))
COLS_PER_TABLE = int(os.getenv("COLS_PER_TABLE", "40"))
VIEW_FRACTION = float(os.getenv("VIEW_FRACTION", "0.8"))
N_PROJECTS = int(os.getenv("N_PROJECTS", "12"))
BIG_CANVASES = int(os.getenv("BIG_CANVASES", "30"))   # canvases de 100 tablas
BIG_SIZE = int(os.getenv("BIG_SIZE", "100"))
SMALL_CANVASES = int(os.getenv("SMALL_CANVASES", "120"))
BATCH = int(os.getenv("BATCH", "1000"))

PROTECTED = "column_catalog"
REGEN = ["projects", "folders", "subject_areas", "canonical_tables",
         "canonical_columns", "relationships", "views"]
CLEAR = ["changesets", "changeset_changes"]
# Dominios preservados (del seed base) a los que apuntan algunas columnas.
DOMAIN_IDS = ["pd-importe", "pd-identificador", "pd-fecha", "pd-codigo"]

_NOW = datetime.now(timezone.utc).isoformat()

SCHEMAS = ["core", "lending", "cards", "risk", "customer", "payments", "digital",
           "treasury", "fraud", "marketing", "hr", "reporting"]
TBL_ROOTS = ["cliente", "cuenta", "movimiento", "producto", "tarjeta", "prestamo",
             "sucursal", "empleado", "transaccion", "saldo", "pago", "factura",
             "contrato", "garantia", "riesgo", "score", "canal", "dispositivo",
             "sesion", "campania", "beneficiario", "posicion", "instrumento",
             "limite", "auditoria", "notificacion", "segmento", "comision"]
COL_ROOTS = ["monto", "fecha", "codigo", "nombre", "descripcion", "estado", "tipo",
             "saldo", "limite", "tasa", "moneda", "cantidad", "total", "referencia",
             "numero", "indicador", "categoria", "nivel", "origen", "destino",
             "usuario", "canal", "score", "vigencia", "prioridad", "version"]
DTYPES = ["BIGINT", "INT", "DECIMAL(18,2)", "DECIMAL(20,4)", "VARCHAR(50)",
          "VARCHAR(255)", "STRING", "DATE", "TIMESTAMP", "BOOLEAN", "DOUBLE"]


def _ab(word: str) -> str:
    return word[:3].upper()


# ── Insert por lotes con tolerancia a throttling ─────────────────────────────
def _is_throttle(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    if code in (16500, 429):
        return True
    det = getattr(exc, "details", None) or {}
    for we in (det.get("writeErrors") or []):
        if we.get("code") in (16500, 429):
            return True
    return "TooManyRequests" in str(exc) or "16500" in str(exc) or "RequestRateTooLarge" in str(exc)


async def _flush(db, coll: str, docs: list[dict], stats: dict) -> None:
    if not docs:
        return
    delay = 0.5
    for attempt in range(10):
        try:
            await db[coll].insert_many(docs, ordered=False)
            stats[coll] = stats.get(coll, 0) + len(docs)
            return
        except Exception as exc:  # noqa: BLE001
            if _is_throttle(exc) and attempt < 9:
                stats["_throttles"] = stats.get("_throttles", 0) + 1
                await asyncio.sleep(delay)
                delay = min(delay * 2, 20)
                continue
            raise
    raise RuntimeError(f"no se pudo insertar lote en {coll}")


class Batcher:
    """Acumula docs y los descarga por lotes (streaming, poca memoria)."""

    def __init__(self, db, coll: str, stats: dict, size: int = BATCH):
        self.db, self.coll, self.stats, self.size = db, coll, stats, size
        self.buf: list[dict] = []

    async def add(self, doc: dict) -> None:
        self.buf.append(doc)
        if len(self.buf) >= self.size:
            await self.close()

    async def close(self) -> None:
        if self.buf:
            await _flush(self.db, self.coll, self.buf, self.stats)
            self.buf = []


# ── Generación ───────────────────────────────────────────────────────────────
def build_projects() -> list[dict]:
    out = []
    for i in range(N_PROJECTS):
        out.append({"_id": f"stp-{i}", "name": f"Stress Project {i+1}",
                    "description": f"Proyecto sintético {i+1} para pruebas de estrés.",
                    "flgactive": True, "createdAt": _NOW, "updatedAt": _NOW})
    return out


def build_production_baseline(project_ids: list[str]) -> dict:
    """Versión de producción BASELINE (`approved` + `appliedAt`): marca el estado
    ya publicado (las 10k tablas) como producción v1. Sin ella, `current_production`
    es None y el canvas ('Open model') no tiene qué abrir ni de dónde snapshotear.
    No lleva cambios en `changeset_changes` — refleja lo publicado directamente."""
    return {
        "_id": "cs-stress-v1",
        "title": "Baseline stress (10k tablas)",
        "owner": "admin",
        "status": "approved",
        "description": "Línea base publicada de la data de estrés (10k tablas / 400k columnas).",
        "versionLabel": "v1",
        "projectIds": project_ids,
        "reviewers": ["ana", "beto"],
        "approvals": {
            "ana": {"status": "approved", "note": "Baseline", "at": _NOW},
            "beto": {"status": "approved", "at": _NOW},
        },
        "comments": [{"author": "admin", "text": "Publicada como línea base de estrés.", "at": _NOW}],
        "createdAt": _NOW, "updatedAt": _NOW,
        "submittedAt": _NOW, "reviewedBy": "beto", "reviewedAt": _NOW,
        "appliedAt": _NOW, "flgactive": True,
    }


def build_folders(projects: list[dict]) -> list[dict]:
    out = []
    for p in projects:
        for f in range(3):
            out.append({"_id": f"{p['_id']}-fld-{f}", "projectId": p["_id"],
                        "parentFolderId": None, "name": f"Folder {f+1}", "order": f,
                        "flgactive": True, "createdAt": _NOW, "updatedAt": _NOW})
    return out


def table_doc(i: int) -> dict:
    root = TBL_ROOTS[i % len(TBL_ROOTS)]
    schema = SCHEMAS[i % len(SCHEMAS)]
    return {"_id": f"stt-{i}", "physicalName": f"{_ab(root)}{root[3:6].upper()}{i:05d}",
            "logicalName": f"{root} {i}", "schema": schema,
            "description": f"Tabla sintética {root} #{i} (schema {schema}).",
            "flgactive": True, "createdAt": _NOW, "updatedAt": _NOW}


def column_docs(i: int) -> list[dict]:
    tid = f"stt-{i}"
    cols = []
    # c0 = PK
    cols.append({"_id": f"{tid}.c0", "tableId": tid, "physicalName": "ID",
                 "logicalName": f"id {TBL_ROOTS[i % len(TBL_ROOTS)]}",
                 "parentDomainId": "pd-identificador", "dataType": "BIGINT",
                 "typeOverridden": False, "isPrimaryKey": True, "isForeignKey": False,
                 "isNullable": False, "isPartition": False, "ordinal": 0,
                 "flgactive": True, "createdAt": _NOW, "updatedAt": _NOW})
    for j in range(1, COLS_PER_TABLE):
        root = COL_ROOTS[(i + j) % len(COL_ROOTS)]
        is_fk = j == 1  # c1 se usa como FK en las relaciones ER
        dom = None
        if root in ("monto", "saldo", "limite", "total"):
            dom, dtype = "pd-importe", "DECIMAL(18,2)"
        elif root == "fecha" or root == "vigencia":
            dom, dtype = "pd-fecha", "DATE"
        elif root in ("codigo", "referencia", "numero"):
            dom, dtype = "pd-codigo", "STRING"
        elif is_fk:
            dom, dtype = "pd-identificador", "BIGINT"
        else:
            dtype = DTYPES[(i + j) % len(DTYPES)]
        cols.append({"_id": f"{tid}.c{j}", "tableId": tid,
                     "physicalName": f"{_ab(root)}_{j:02d}", "logicalName": f"{root} {j}",
                     "parentDomainId": dom, "dataType": dtype, "typeOverridden": False,
                     "isPrimaryKey": False, "isForeignKey": is_fk,
                     "isNullable": bool(j % 3), "isPartition": j == 2,
                     "ordinal": j, "flgactive": True, "createdAt": _NOW, "updatedAt": _NOW})
    return cols


def view_doc(vid: int, tid: int) -> dict:
    root = TBL_ROOTS[tid % len(TBL_ROOTS)]
    table_id = f"stt-{tid}"
    return {"_id": f"stv-{vid}", "name": f"v_{root}_{tid}",
            "sql": f"SELECT * FROM {root}{tid} WHERE estado = 'A'",
            "description": f"Vista sintética de {root} #{tid}.",
            "tableId": table_id, "schema": SCHEMAS[tid % len(SCHEMAS)],
            "tags": [], "filter": "estado = 'A'",
            "sources": [{"table": f"{root}{tid}"}], "outputAlias": None, "expression": None,
            "flgactive": True, "createdAt": _NOW, "updatedAt": _NOW}


# ── Orquestación ─────────────────────────────────────────────────────────────
async def main() -> None:
    from app.core.db.client import connect, disconnect, get_db
    from app.core.db.indexes import ensure_indexes

    print(f"[stress] config: N_TABLES={N_TABLES} COLS={COLS_PER_TABLE} "
          f"views={VIEW_FRACTION} big_canvases={BIG_CANVASES}x{BIG_SIZE} batch={BATCH}")
    await connect()
    db = await get_db()
    before = await db[PROTECTED].count_documents({})
    stats: dict = {}
    t0 = time.monotonic()

    # 1) Drop de las colecciones a regenerar/limpiar (nunca column_catalog).
    assert PROTECTED not in REGEN + CLEAR, "no tocar column_catalog"
    for coll in REGEN + CLEAR:
        await db[coll].drop()
    print(f"[stress] dropped {len(REGEN + CLEAR)} colecciones")

    # 2) Projects + folders + versión de producción baseline (sin ella el canvas
    #    no puede abrir el modelo: 'Open model' no encuentra producción).
    projects = build_projects()
    folders = build_folders(projects)
    await _flush(db, "projects", projects, stats)
    await _flush(db, "folders", folders, stats)
    await _flush(db, "changesets", [build_production_baseline([p["_id"] for p in projects])], stats)

    # 3) Tablas + columnas (streaming por lotes).
    tb = Batcher(db, "canonical_tables", stats)
    cb = Batcher(db, "canonical_columns", stats)
    for i in range(N_TABLES):
        await tb.add(table_doc(i))
        for c in column_docs(i):
            await cb.add(c)
        if (i + 1) % 500 == 0:
            el = time.monotonic() - t0
            print(f"[stress] tablas {i+1}/{N_TABLES} · columnas ~{stats.get('canonical_columns',0)} "
                  f"· {el:.0f}s · throttles {stats.get('_throttles',0)}", flush=True)
    await tb.close()
    await cb.close()
    print(f"[stress] tablas+columnas OK ({time.monotonic()-t0:.0f}s)")

    # 4) Vistas (80% de las tablas; algunas 2).
    vb = Batcher(db, "views", stats)
    vid = 0
    for i in range(N_TABLES):
        if random.random() < VIEW_FRACTION:
            await vb.add(view_doc(vid, i)); vid += 1
            if random.random() < 0.15:  # algunas tablas con 2 vistas
                await vb.add(view_doc(vid, i)); vid += 1
    await vb.close()
    print(f"[stress] vistas {vid} OK")

    # 5) Canvases + relaciones ER.
    #    Big: BIG_CANVASES de 100 tablas; small: SMALL_CANVASES de 8..40.
    ca = Batcher(db, "subject_areas", stats, size=200)
    rb = Batcher(db, "relationships", stats)
    canvas_specs = ([(BIG_SIZE, True)] * BIG_CANVASES
                    + [(random.randint(8, 40), False) for _ in range(SMALL_CANVASES)])
    cursor = 0
    for ci, (size, big) in enumerate(canvas_specs):
        # tablas del canvas: ventana deslizante sobre las 10k (con wraparound).
        ids = [f"stt-{(cursor + k) % N_TABLES}" for k in range(size)]
        cursor = (cursor + size) % N_TABLES
        layout = {tid: {"x": (k % 12) * 300, "y": (k // 12) * 380} for k, tid in enumerate(ids)}
        proj = projects[ci % len(projects)]["_id"]
        await ca.add({"_id": f"stc-{ci}", "projectId": proj,
                      "folderId": f"{proj}-fld-{ci % 3}", "name": f"Canvas {ci+1} ({size} tablas)",
                      "tableIds": ids, "layout": layout, "drawings": [],
                      "flgactive": True, "createdAt": _NOW, "updatedAt": _NOW})
        # Relaciones ER dentro del canvas: cadena + algunos enlaces extra.
        links = set()
        for k in range(1, size):
            links.add((k, k - 1))
        for _ in range(size // 3):  # extras (star/mesh)
            a, b = random.randrange(size), random.randrange(size)
            if a != b:
                links.add((a, b))
        for (a, b) in links:
            src, tgt = ids[a], ids[b]
            await rb.add({"_id": f"str-{ci}-{a}-{b}", "sourceTableId": src,
                          "sourceColumnId": f"{src}.c1", "targetTableId": tgt,
                          "targetColumnId": f"{tgt}.c0", "sourceCardinality": "many",
                          "targetCardinality": "one", "identifying": False,
                          "flgactive": True, "createdAt": _NOW, "updatedAt": _NOW})
    await ca.close()
    await rb.close()
    print(f"[stress] canvases {len(canvas_specs)} + relaciones {stats.get('relationships',0)} OK")

    # 6) Índices correctos (los drop se llevaron los legacy).
    await ensure_indexes(db)
    print("[stress] indexes ensured")

    # 7) Resumen + guardrail.
    after = await db[PROTECTED].count_documents({})
    el = time.monotonic() - t0
    print(f"\n[stress] LISTO en {el:.0f}s · throttles {stats.get('_throttles',0)}")
    for c in REGEN:
        print(f"  {c:<20} {stats.get(c, 0):>8}")
    print(f"  column_catalog intacta: {before} → {after}  [{'OK' if before == after else 'MISMATCH'}]")
    await disconnect()


if __name__ == "__main__":
    asyncio.run(main())
