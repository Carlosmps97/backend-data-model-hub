"""Correcciones de partición DECIDIDAS POR EL OWNER (2026-07-17, doc 21 §3).

Las 3 tablas con UDP `Particion` incongruente del DDV real, con la decisión
tomada tabla por tabla:

1. FCA_SEMAFOROESTADISTICO — "que se actualice el orden FÍSICO al correlativo":
   los correlativos quedan (NBRTABLA=01, NUMPERIODO=02, FECDIA=03) y las 3
   columnas PERMUTAN sus posiciones físicas (slots ord 8/9/19 → NBRTABLA=8,
   NUMPERIODO=9, FECDIA=19).
2. FCA_RESULTADOUMBRALCONFIANZA — aclaración del owner (07-18): "NUMPERIODO
   sea 03 y FECDIA el 04" → correlativos CODAPP=01, NBRTABLA=02,
   NUMPERIODO=03, FECDIA=04 (solo FECDIA cambia respecto del XML), y el
   orden FÍSICO se alinea al correlativo (mismo criterio que la tabla 1):
   slots 8/9/19/23 → CODAPP=8, NBRTABLA=9, NUMPERIODO=19, FECDIA=23.
3. HD_VENTACAPITALTRABAJOCLIENTECPY — "primero CODMES y luego FECDIA":
   CODMES queda PART_01 y FECDIA (duplicado) pasa a PART_02 — el físico ya
   respeta ese orden (ord 28 < 51).

En las 3, tras corregir, se marca `isPartition=True` (quedan congruentes:
el DDL emite PARTITIONED BY en orden físico). Idempotente; sin `--apply` es
dry-run. OJO: si se re-migra el MISMO XML sin corregir los UDP en Erwin, el
re-run pisa udpValues/ordinales y el audit C10 volverá a marcar estas tablas.

Doc 75: las tablas y la UDP `Particion` se buscan DENTRO del proyecto
(`--project`, default «Modelo DDV» — el proyecto del manifiesto del one-shot).

Uso:
  .venv/bin/python -m scripts.fix_particiones_ddv_20260717           # dry-run
  .venv/bin/python -m scripts.fix_particiones_ddv_20260717 --apply [--project "Modelo DDV"]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

ACTIVE = {"flgactive": {"$ne": False}}

# physicalName de tabla → corrección. `udp`: valores finales del UDP Particion
# por columna (las no listadas no se tocan). `orden_fisico`: si True, las
# columnas de partición permutan sus ordinales para quedar en el orden de
# `particiones` (usando los mismos slots que ya ocupan).
CORRECCIONES = [
    {"tabla": "FCA_SEMAFOROESTADISTICO",
     "detalle": "orden físico → correlativo (owner: 'actualizar el orden físico al correlativo')",
     "udp": {},
     "particiones": ["NBRTABLA", "NUMPERIODO", "FECDIA"],
     "orden_fisico": True},
    {"tabla": "FCA_RESULTADOUMBRALCONFIANZA",
     "detalle": "NUMPERIODO=03 y FECDIA=04 (aclaración owner 07-18) + orden físico al correlativo",
     "udp": {"CODAPP": "PART_01", "NBRTABLA": "PART_02",
             "NUMPERIODO": "PART_03", "FECDIA": "PART_04"},
     "particiones": ["CODAPP", "NBRTABLA", "NUMPERIODO", "FECDIA"],
     "orden_fisico": True},
    {"tabla": "HD_VENTACAPITALTRABAJOCLIENTECPY",
     "detalle": "FECDIA duplicado PART_01 → PART_02 (owner: 'primero CODMES y luego FECDIA')",
     "udp": {"CODMES": "PART_01", "FECDIA": "PART_02"},
     "particiones": ["CODMES", "FECDIA"],
     "orden_fisico": False},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="escribir en la BD (sin esto: dry-run)")
    ap.add_argument("--project", default="Modelo DDV", help="proyecto donde viven las tablas del DDV")
    args = ap.parse_args()

    load_dotenv()
    from app.core.db.sync import get_sync_db
    db = get_sync_db()

    proj = db.projects.find_one({"name": args.project, **ACTIVE}, {"_id": 1})
    if not proj:
        print(f"No existe el proyecto «{args.project}» — nada que corregir.")
        return 1
    project_id = proj["_id"]
    udp = db.udp_definitions.find_one(
        {"name": "Particion", "level": "column", "projectId": project_id, **ACTIVE}, {"_id": 1})
    if not udp:
        print(f"No existe la udp_definition 'Particion' (level=column) en «{args.project}» — nada que corregir.")
        return 1
    pid = udp["_id"]
    tag = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{tag}] correcciones de partición (decisión owner 2026-07-17) en «{args.project}»")

    problemas = 0
    for cor in CORRECCIONES:
        t = db.canonical_tables.find_one(
            {"projectId": project_id, "physicalName": cor["tabla"], **ACTIVE}, {"_id": 1, "schema": 1})
        print(f"\n■ {cor['tabla']} — {cor['detalle']}")
        if not t:
            print("  ✗ tabla no encontrada en BD — corrección OMITIDA")
            problemas += 1
            continue
        cols = {c["physicalName"]: c for c in db.canonical_columns.find(
            {"tableId": t["_id"], **ACTIVE},
            {"physicalName": 1, "ordinal": 1, "isPartition": 1, f"udpValues.{pid}": 1})}
        faltan = [n for n in cor["particiones"] if n not in cols]
        if faltan:
            print(f"  ✗ columnas no encontradas: {faltan} — corrección OMITIDA")
            problemas += 1
            continue

        # 1 · valores UDP (correlativos) finales
        for name, val in cor["udp"].items():
            cur = (cols[name].get("udpValues") or {}).get(pid)
            if cur == val:
                continue
            print(f"  udp {name}: {cur!r} → {val!r}")
            if args.apply:
                db.canonical_columns.update_one(
                    {"_id": cols[name]["_id"]},
                    {"$set": {f"udpValues.{pid}": val, "updatedAt": _now()}})

        # 2 · permutación del orden físico (mismos slots, nuevo ocupante)
        if cor["orden_fisico"]:
            slots = sorted(cols[n]["ordinal"] for n in cor["particiones"])
            for name, slot in zip(cor["particiones"], slots):
                cur = cols[name]["ordinal"]
                if cur == slot:
                    continue
                print(f"  ordinal {name}: {cur} → {slot}")
                if args.apply:
                    db.canonical_columns.update_one(
                        {"_id": cols[name]["_id"]},
                        {"$set": {"ordinal": slot, "updatedAt": _now()}})

        # 3 · marca nativa
        for name in cor["particiones"]:
            if cols[name].get("isPartition") is True:
                continue
            print(f"  isPartition {name}: → True")
            if args.apply:
                db.canonical_columns.update_one(
                    {"_id": cols[name]["_id"]},
                    {"$set": {"isPartition": True, "updatedAt": _now()}})

        # 4 · verificación de congruencia RESULTANTE (post-corrección)
        finales = []
        for name in cor["particiones"]:
            ordv = cols[name]["ordinal"]
            if cor["orden_fisico"]:
                slots = sorted(cols[n]["ordinal"] for n in cor["particiones"])
                ordv = dict(zip(cor["particiones"], slots))[name]
            finales.append((ordv, name))
        por_fisico = [n for _, n in sorted(finales)]
        if por_fisico != cor["particiones"]:
            print(f"  ✗ INCONGRUENTE tras corrección: físico={por_fisico} vs correlativo={cor['particiones']}")
            problemas += 1
        else:
            print(f"  ✓ congruente: {' → '.join(cor['particiones'])}")

    if not args.apply:
        print("\nDry-run: nada se escribió. Repetir con --apply para aplicar.")
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main())
