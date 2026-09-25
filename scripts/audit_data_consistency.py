"""Auditoría/saneo de la data de la BD contra las reglas de la plataforma.

Data cargada ANTES de una regla (migraciones, lotes viejos) puede violar lo
que hoy los routers imponen y generar inconsistencias visibles:
duplicados que el guard #9 ya no dejaría crear, relaciones huérfanas que el
impact #8 listaría contra columnas muertas, vistas con fuentes rotas, keys
UDP muertas en reportes, etc. Este script deja la base en un estado que las
reglas actuales aceptarían.

Chequeos (C1–C9) y su fix:
  C1 tablas duplicadas (proyecto+physicalName, case-insens, activas — doc 75)
       → renombra los duplicados (sufijo " 2"/"_2"; conserva el primero).
  C2 columnas duplicadas dentro de una tabla (physicalName case-insens)
       → renombra los duplicados dentro de la tabla.
  C3 glosario: duplicado exacto (proyecto+term+scope, case-insens, activos)
       → desactiva los más nuevos (conserva el primero).
  C4 vistas: sourceTableIds vacío o con tablas muertas; tableId ≠ 1ª fuente;
     sources[].tableId fuera de sourceTableIds
       → poda fuentes muertas (vista sin fuentes → soft-delete);
         re-normaliza tableId; re-apunta sources.tableId en single-source.
  C5 (retirado, doc 91 D12): multi-fuente sin join ya no es anomalía — el
     DDL las lista `FROM t1, t2` como Erwin; un JOIN vive en el User-Defined SQL.
  C6 relaciones huérfanas (tabla o columna de un extremo muerta/inexistente)
       → soft-delete (cierra el stock viejo del bug #21).
  C7 columnas con parentDomainId muerto → unset del campo.
  C8 udpValues con keys muertas (tablas/columnas/canvases) → unset de esas
     keys; valores fuera de allowedValues (defs tipo list) → REPORTE.
  C9 subject_areas: tableIds muertos → pull; layout con nodos que no son ni
     tabla activa ni vista activa → poda de entradas.
  C11 campos retirados (doc 94): `glossary_terms.wordType` (D12) y
      `canonical_columns.pkPosition` (D1) → unset.

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
from pymongo import UpdateOne

from app.core.db.sync import get_sync_db

load_dotenv()
FIX = "--fix" in sys.argv
db = get_sync_db()

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


# Campos retirados del modelo (doc 94): quedan en docs viejos hasta el saneo.
RETIRED_FIELDS = (("glossary_terms", "wordType"), ("canonical_columns", "pkPosition"))


def unset_retired_fields(database, fix: bool) -> dict[str, int]:
    """C11 · docs (activos o no) que aún traen un campo retirado (doc 94:
    `glossary_terms.wordType`, `canonical_columns.pkPosition`). Cuenta por
    `coleccion.campo` y, con `fix`, los quita con un `$unset`. Idempotente:
    tras el fix las cuentas quedan en 0."""
    out: dict[str, int] = {}
    for coll, field in RETIRED_FIELDS:
        flt = {field: {"$exists": True}}
        n = database[coll].count_documents(flt)
        if fix and n:
            database[coll].update_many(flt, {"$unset": {field: ""}})
        out[f"{coll}.{field}"] = n
    return out


def main():
    print(f"== AUDIT data vs reglas doc 10 · {'FIX' if FIX else 'REPORTE'} ==")

    # ── Pase tablas ──────────────────────────────────────────────────────────
    tables = list(db.canonical_tables.find(
        ACTIVE, {"_id": 1, "schema": 1, "physicalName": 1, "logicalName": 1, "projectId": 1}))
    active_tids = {t["_id"] for t in tables}

    # C1 · duplicados de tabla — doc 75: el físico es único POR PROYECTO
    by_key: dict[str, list[dict]] = defaultdict(list)
    for t in tables:
        by_key[f"{t.get('projectId')}|{norm(t.get('physicalName'))}"].append(t)
    dup_groups = {k: v for k, v in by_key.items() if len(v) > 1}
    ops = []
    for group in dup_groups.values():
        for i, t in enumerate(sorted(group, key=lambda x: x["_id"])[1:], start=2):
            ops.append(UpdateOne({"_id": t["_id"]}, {"$set": {
                "physicalName": f"{t.get('physicalName')}_{i}",
                "logicalName": f"{t.get('logicalName')} {i}",
                "updatedAt": now()}}))
    report("C1 tablas duplicadas (proyecto+nombre)", sum(len(v) - 1 for v in dup_groups.values()),
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
    terms = list(db.glossary_terms.find(ACTIVE, {"_id": 1, "term": 1, "scope": 1, "projectId": 1}))
    tkey: dict[str, list[dict]] = defaultdict(list)
    for t in terms:
        tkey[f"{t.get('projectId')}|{norm(t.get('term'))}|{t.get('scope') or 'column'}"].append(t)
    tdups = [t for group in tkey.values() if len(group) > 1
             for t in sorted(group, key=lambda x: x["_id"])[1:]]
    report("C3 glosario duplicado exacto (proyecto+term+scope)", len(tdups),
           f"{[t.get('term') for t in tdups[:5]]}")
    bulk("glossary_terms", [UpdateOne({"_id": t["_id"]}, {"$set": {
        "flgactive": False, "deletedAt": now()}}) for t in tdups])

    # ── C4 · vistas ──────────────────────────────────────────────────────────
    rels = list(db.relationships.find(ACTIVE, {
        "_id": 1, "parentTableId": 1, "childTableId": 1, "pairs": 1,
        "sourceTableId": 1, "sourceColumnId": 1,
        "targetTableId": 1, "targetColumnId": 1}))

    def _rel_ends(r: dict) -> tuple:
        """(padre, hijo) con fallback a los campos legacy pre-v2."""
        return (r.get("parentTableId") or r.get("targetTableId"),
                r.get("childTableId") or r.get("sourceTableId"))

    v_dead_src = v_fix_tid = v_fix_sources = v_orphan = 0
    vops: list[UpdateOne] = []
    for v in db.views.find(ACTIVE, {"_id": 1, "name": 1, "tableId": 1,
                                    "sourceTableIds": 1, "sources": 1}):
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
    report("C4a vistas con fuentes muertas (podadas)", v_dead_src)
    report("C4b vistas sin NINGUNA fuente viva (soft-delete)", v_orphan)
    report("C4c vistas con tableId ≠ 1ª fuente (re-normalizado)", v_fix_tid)
    report("C4d sources[].tableId fuera de las fuentes (re-apuntado)", v_fix_sources)
    bulk("views", vops)

    # ── C6 · relaciones huérfanas ────────────────────────────────────────────
    def _rel_orphan(r: dict) -> bool:
        parent_t, child_t = _rel_ends(r)
        if parent_t not in active_tids or child_t not in active_tids:
            return True
        prs = r.get("pairs")
        if prs:  # v2: cualquier par con columna muerta = huérfana
            return any(p.get("parentColumnId") not in active_cids
                       or p.get("childColumnId") not in active_cids for p in prs)
        return (r.get("sourceColumnId") not in active_cids
                or r.get("targetColumnId") not in active_cids)

    orphans = [r["_id"] for r in rels if _rel_orphan(r)]
    report("C6 relaciones huérfanas (extremo muerto)", len(orphans))
    bulk("relationships", [UpdateOne({"_id": rid}, {"$set": {
        "flgactive": False, "deletedAt": now()}}) for rid in orphans])

    # ── C6b/C6c · orientación (INFORME, no auto-fix): en cada par el padre
    # debería ser PK y el hijo FK (doc 19). Un conteo alto delata relaciones
    # invertidas o flags de columna sin sincronizar.
    key_flags = {c["_id"]: c for c in db.canonical_columns.find(
        ACTIVE, {"isPrimaryKey": 1, "isForeignKey": 1})}
    bad_parent = bad_child = 0
    for r in rels:
        for p in (r.get("pairs") or []):
            pf, cf = key_flags.get(p.get("parentColumnId")), key_flags.get(p.get("childColumnId"))
            if pf is not None and not pf.get("isPrimaryKey"):
                bad_parent += 1
            if cf is not None and not cf.get("isForeignKey"):
                bad_child += 1
    report("C6b pares con columna padre sin PK (INFORME, no auto-fix)", bad_parent)
    report("C6c pares con columna hija sin FK (INFORME, no auto-fix)", bad_child)

    # ── C10 · partición: UDP PART_nn vs orden físico y flag nativo (INFORME) ──
    # Política v2 (owner 2026-07-24, doc 32b R6): TODA columna PART_nn se
    # marca isPartition; el orden EFECTIVO es el físico (lo que emite el DDL).
    # Correlativos duplicados/en desorden = REASIGNADOS por orden físico al
    # migrar — acá solo se INFORMA que el valor UDP no coincide con el orden
    # (útil para corregirlo en Erwin algún día). C10b vigila lo accionable:
    # columnas con PART_nn que quedaron SIN marcar (cargas pre-política).
    from scripts.erwin_migration import policies as pol
    part_def = db.udp_definitions.find_one(
        {**ACTIVE, "name": "Particion", "level": "column"}, {"_id": 1})
    part_bad: list[str] = []
    part_unmarked = 0
    if part_def:
        updk = f"udpValues.{part_def['_id']}"
        per_table: dict[str, list] = {}
        for c in db.canonical_columns.find(
                {**ACTIVE, updk: {"$exists": True}},
                {"tableId": 1, "ordinal": 1, "isPartition": 1, "physicalName": 1, updk: 1}):
            corr = pol.partition_correlative((c.get("udpValues") or {}).get(part_def["_id"]))
            if corr is not None:
                per_table.setdefault(c["tableId"], []).append((corr, c))
        tnames = {t["_id"]: t.get("physicalName") or t["_id"]
                  for t in db.canonical_tables.find(
                      {"_id": {"$in": list(per_table)}}, {"physicalName": 1})}
        for tid, entries in per_table.items():
            entries.sort(key=lambda e: e[1].get("ordinal", 0))  # orden físico
            nums = [n for n, _ in entries]
            if len(nums) != len(set(nums)) or nums != sorted(nums):
                part_bad.append(f"{tnames.get(tid, tid)}: correlativos en orden físico {nums}")
            part_unmarked += sum(1 for _, c in entries if not c.get("isPartition"))
    report("C10 correlativo PART_nn ≠ orden físico (INFORME — reasignado por "
           "orden físico al migrar, doc 32b R6)", len(part_bad), f"{part_bad[:5]}")
    report("C10b columnas PART_nn SIN isPartition (INFORME — cargas pre-política)",
           part_unmarked)

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

    # ── C11 · campos retirados del modelo (doc 94) ──────────────────────────
    for key, n in unset_retired_fields(db, FIX).items():
        report(f"C11 {key} (campo retirado, doc 94)", n)

    bad = sum(n for code, n in ISSUES.items() if "INFORME" not in code)
    info = sum(n for code, n in ISSUES.items() if "INFORME" in code)
    print(f"\n== {'APLICADO' if FIX else 'HALLADO'}: {bad} fixable · {info} informativos ==")
    return 0 if (FIX or bad == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
