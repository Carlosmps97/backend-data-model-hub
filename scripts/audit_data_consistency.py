"""Auditoría/saneo de la data (sintética) contra las reglas del lote doc 10.

La data sembrada ANTES del lote puede violar reglas que hoy los routers
imponen (se creó cuando no existían) y generar inconsistencias visibles:
duplicados que el guard #9 ya no dejaría crear, relaciones huérfanas que el
impact #8 listaría contra columnas muertas, vistas con fuentes rotas, keys
UDP muertas en reportes, etc. Este script deja la base en un estado que las
reglas actuales aceptarían.

Chequeos (C1–C9) y su fix:
  C1 tablas duplicadas (schema+physicalName, case-insens, activas)
       → renombra los duplicados (sufijo " 2"/"_2"; conserva el primero).
  C2 columnas duplicadas dentro de una tabla (physicalName case-insens)
       → renombra los duplicados dentro de la tabla.
  C3 glosario: duplicado exacto (term+scope, case-insens, activos)
       → desactiva los más nuevos (conserva el primero).
  C4 vistas: sourceTableIds vacío o con tablas muertas; tableId ≠ 1ª fuente;
     sources[].tableId fuera de sourceTableIds
       → poda fuentes muertas (vista sin fuentes → soft-delete);
         re-normaliza tableId; re-apunta sources.tableId en single-source.
  C5 vistas multi-fuente sin joinOverride NI relación modelada entre fuentes
       → REPORTE (el DDL emitiría fallback comentado; decisión humana).
  C6 relaciones huérfanas (tabla o columna de un extremo muerta/inexistente)
       → soft-delete (cierra el stock viejo del bug #21).
  C7 columnas con parentDomainId muerto → unset del campo.
  C8 udpValues con keys muertas (tablas/columnas/canvases) → unset de esas
     keys; valores fuera de allowedValues (defs tipo list) → REPORTE.
  C9 subject_areas: tableIds muertos → pull; layout con nodos que no son ni
     tabla activa ni vista activa → poda de entradas.

Uso:
    .venv/bin/python -m scripts.audit_data_consistency            # reporte
    .venv/bin/python -m scripts.audit_data_consistency --fix      # aplica
Idempotente: tras --fix, el reporte queda en 0 (salvo C5/C8b, informativos).
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne

load_dotenv()
FIX = "--fix" in sys.argv
db = MongoClient(os.environ["COSMOS_CONNECTION_STRING"])[
    os.environ.get("COSMOS_DATABASE", "db_modeler")]

ACTIVE = {"flgactive": {"$ne": False}}
now = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731
ISSUES: dict[str, int] = {}


def report(code: str, n: int, detail: str = ""):
    ISSUES[code] = n
    mark = "✗" if n else "✓"
    print(f"  {mark} {code}: {n}" + (f" — {detail}" if detail and n else ""))


def bulk(coll: str, ops: list):
    if FIX and ops:
        for i in range(0, len(ops), 500):
            db[coll].bulk_write(ops[i:i + 500], ordered=False)


def norm(s) -> str:
    return str(s or "").strip().lower()


def main():
    print(f"== AUDIT data vs reglas doc 10 · {'FIX' if FIX else 'REPORTE'} ==")

    # ── Pase tablas ──────────────────────────────────────────────────────────
    tables = list(db.canonical_tables.find(
        ACTIVE, {"_id": 1, "schema": 1, "physicalName": 1, "logicalName": 1}))
    active_tids = {t["_id"] for t in tables}

    # C1 · duplicados de tabla
    by_key: dict[str, list[dict]] = defaultdict(list)
    for t in tables:
        by_key[f"{norm(t.get('schema'))}|{norm(t.get('physicalName'))}"].append(t)
    dup_groups = {k: v for k, v in by_key.items() if len(v) > 1}
    ops = []
    for group in dup_groups.values():
        for i, t in enumerate(sorted(group, key=lambda x: x["_id"])[1:], start=2):
            ops.append(UpdateOne({"_id": t["_id"]}, {"$set": {
                "physicalName": f"{t.get('physicalName')}_{i}",
                "logicalName": f"{t.get('logicalName')} {i}",
                "updatedAt": now()}}))
    report("C1 tablas duplicadas (schema+nombre)", sum(len(v) - 1 for v in dup_groups.values()),
           f"{list(dup_groups)[:3]}")
    bulk("canonical_tables", ops)

    # ── Pase columnas (streaming, proyección mínima) ────────────────────────
    col_seen: dict[str, str] = {}          # tableId|name → primer _id
    col_dups: list[tuple[str, str, str]] = []   # (colId, tableId, physicalName)
    active_cids: set[str] = set()
    dead_domain_ops: list[UpdateOne] = []
    domains = {d["_id"] for d in db.parent_domains.find(ACTIVE, {"_id": 1})}
    dead_dom = 0
    for c in db.canonical_columns.find(
            ACTIVE, {"_id": 1, "tableId": 1, "physicalName": 1, "parentDomainId": 1}):
        active_cids.add(c["_id"])
        key = f"{c.get('tableId')}|{norm(c.get('physicalName'))}"
        if key in col_seen:
            col_dups.append((c["_id"], c.get("tableId"), c.get("physicalName")))
        else:
            col_seen[key] = c["_id"]
        pd = c.get("parentDomainId")
        if pd and pd not in domains:
            dead_dom += 1
            dead_dom_ops = dead_domain_ops  # alias corto
            dead_dom_ops.append(UpdateOne(
                {"_id": c["_id"]}, {"$set": {"parentDomainId": None, "updatedAt": now()}}))
    del col_seen

    # C2 · columnas duplicadas
    ops = [UpdateOne({"_id": cid}, {"$set": {
        "physicalName": f"{name}_{i + 2}", "updatedAt": now()}})
        for i, (cid, _tid, name) in enumerate(col_dups)]
    report("C2 columnas duplicadas (por tabla)", len(col_dups),
           f"{[(t, n) for _, t, n in col_dups[:3]]}")
    bulk("canonical_columns", ops)

    # C7 · dominios muertos
    report("C7 columnas con parentDomainId muerto", dead_dom)
    bulk("canonical_columns", dead_domain_ops)

    # ── C3 · glosario duplicado exacto ───────────────────────────────────────
    terms = list(db.glossary_terms.find(ACTIVE, {"_id": 1, "term": 1, "scope": 1}))
    tkey: dict[str, list[dict]] = defaultdict(list)
    for t in terms:
        tkey[f"{norm(t.get('term'))}|{t.get('scope') or 'column'}"].append(t)
    tdups = [t for group in tkey.values() if len(group) > 1
             for t in sorted(group, key=lambda x: x["_id"])[1:]]
    report("C3 glosario duplicado exacto (term+scope)", len(tdups),
           f"{[t.get('term') for t in tdups[:5]]}")
    bulk("glossary_terms", [UpdateOne({"_id": t["_id"]}, {"$set": {
        "flgactive": False, "deletedAt": now()}}) for t in tdups])

    # ── C4/C5 · vistas ───────────────────────────────────────────────────────
    rel_pairs: set[frozenset] = set()
    rels = list(db.relationships.find(ACTIVE, {
        "_id": 1, "sourceTableId": 1, "sourceColumnId": 1,
        "targetTableId": 1, "targetColumnId": 1}))
    for r in rels:
        rel_pairs.add(frozenset((r.get("sourceTableId"), r.get("targetTableId"))))

    v_dead_src = v_fix_tid = v_fix_sources = v_orphan = 0
    multi_nojoin: list[str] = []
    vops: list[UpdateOne] = []
    for v in db.views.find(ACTIVE, {"_id": 1, "name": 1, "tableId": 1,
                                    "sourceTableIds": 1, "sources": 1, "joinOverride": 1}):
        src = [s for s in (v.get("sourceTableIds") or []) if s]
        alive = [s for s in src if s in active_tids]
        upd: dict = {}
        if len(alive) != len(src):
            v_dead_src += 1
            upd["sourceTableIds"] = alive
        if not alive:
            v_orphan += 1
            vops.append(UpdateOne({"_id": v["_id"]}, {"$set": {
                "flgactive": False, "deletedAt": now()}}))
            continue
        if v.get("tableId") != alive[0]:
            v_fix_tid += 1
            upd["tableId"] = alive[0]
        sources = v.get("sources") or []
        fixed_sources, touched = [], False
        for s in sources:
            stid = s.get("tableId")
            if stid and stid not in alive:
                touched = True
                # single-source: la columna solo puede venir de la única fuente
                fixed_sources.append({**s, "tableId": alive[0] if len(alive) == 1 else None})
            else:
                fixed_sources.append(s)
        if touched:
            v_fix_sources += 1
            upd["sources"] = fixed_sources
        if upd:
            upd["updatedAt"] = now()
            vops.append(UpdateOne({"_id": v["_id"]}, {"$set": upd}))
        if len(alive) > 1 and not (v.get("joinOverride") or "").strip():
            connected = all(any(frozenset((a, b)) in rel_pairs for b in alive if b != a)
                            for a in alive)
            if not connected:
                multi_nojoin.append(v.get("name"))
    report("C4a vistas con fuentes muertas (podadas)", v_dead_src)
    report("C4b vistas sin NINGUNA fuente viva (soft-delete)", v_orphan)
    report("C4c vistas con tableId ≠ 1ª fuente (re-normalizado)", v_fix_tid)
    report("C4d sources[].tableId fuera de las fuentes (re-apuntado)", v_fix_sources)
    bulk("views", vops)
    report("C5 multi-fuente sin join NI relación (INFORME, no auto-fix)",
           len(multi_nojoin), f"{multi_nojoin[:5]}")

    # ── C6 · relaciones huérfanas ────────────────────────────────────────────
    orphans = [r["_id"] for r in rels
               if r.get("sourceTableId") not in active_tids
               or r.get("targetTableId") not in active_tids
               or r.get("sourceColumnId") not in active_cids
               or r.get("targetColumnId") not in active_cids]
    report("C6 relaciones huérfanas (extremo muerto)", len(orphans))
    bulk("relationships", [UpdateOne({"_id": rid}, {"$set": {
        "flgactive": False, "deletedAt": now()}}) for rid in orphans])

    # ── C8 · udpValues: keys muertas / valores fuera de enum ────────────────
    defs = {d["_id"]: d for d in db.udp_definitions.find(ACTIVE)}
    bad_enum: list[str] = []
    for coll, level in (("canonical_tables", "table"), ("canonical_columns", "column"),
                        ("subject_areas", "canvas")):
        ops, dead_keys = [], 0
        q = {**ACTIVE, "udpValues": {"$exists": True, "$ne": {}}}
        for doc in db[coll].find(q, {"_id": 1, "udpValues": 1, "name": 1, "physicalName": 1}):
            vals = doc.get("udpValues") or {}
            dead = [k for k in vals if k not in defs or defs[k].get("level", "column") != level]
            if dead:
                dead_keys += 1
                ops.append(UpdateOne({"_id": doc["_id"]},
                                     {"$unset": {f"udpValues.{k}": "" for k in dead}}))
            for k, val in vals.items():
                d = defs.get(k)
                if d and d.get("dataType") == "list" and d.get("allowedValues") \
                        and val not in d["allowedValues"]:
                    bad_enum.append(f"{coll}:{doc.get('physicalName') or doc.get('name')}·{d['name']}={val}")
        report(f"C8 {coll} con keys UDP muertas/nivel equivocado", dead_keys)
        bulk(coll, ops)
    report("C8b valores UDP fuera de allowedValues (INFORME)", len(bad_enum), f"{bad_enum[:5]}")

    # ── C9 · subject_areas: tableIds muertos + layout huérfano ──────────────
    active_vids = {v["_id"] for v in db.views.find(ACTIVE, {"_id": 1})}
    valid_nodes = active_tids | active_vids
    ops, n_tids, n_layout = [], 0, 0
    for sa in db.subject_areas.find({}, {"_id": 1, "tableIds": 1, "layout": 1}):
        upd = {}
        tids = sa.get("tableIds") or []
        alive = [t for t in tids if t in active_tids]
        if len(alive) != len(tids):
            n_tids += 1
            upd["tableIds"] = alive
        layout = sa.get("layout") or {}
        pruned = {k: v for k, v in layout.items() if k in valid_nodes}
        if len(pruned) != len(layout):
            n_layout += 1
            upd["layout"] = pruned
        if upd:
            ops.append(UpdateOne({"_id": sa["_id"]}, {"$set": upd}))
    report("C9a canvases con tableIds muertos", n_tids)
    report("C9b canvases con layout de nodos inexistentes", n_layout)
    bulk("subject_areas", ops)

    bad = sum(n for code, n in ISSUES.items() if not code.startswith(("C5", "C8b")))
    info = ISSUES.get("C5 multi-fuente sin join NI relación (INFORME, no auto-fix)", 0) \
        + ISSUES.get("C8b valores UDP fuera de allowedValues (INFORME)", 0)
    print(f"\n== {'APLICADO' if FIX else 'HALLADO'}: {bad} fixable · {info} informativos ==")
    return 0 if (FIX or bad == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
