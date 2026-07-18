"""Backfill quirúrgico (2026-07-17, doc 21): dos campos, desde el/los XML.

1) `relationships.parentCardinality` — migrate lo hardcodeaba en "one"; el XML
   trae `Null_Option_Type` por relación ("100" = Nulls Allowed → el padre es
   OPCIONAL, 0..1 — el rombo del diagrama Erwin). Se recalcula con
   `policies.map_parent_cardinality` y se actualizan SOLO los docs que difieren.

2) `canonical_columns.isPartition` — el UDP de columna "Particion" (PART_nn,
   convención DDV) pasa a partición NATIVA, sólo en tablas CONGRUENTES
   (correlativo en orden físico, sin duplicados — `policies.partition_marks`).
   Las incongruentes NO se tocan (decisión del owner) y se listan.

Acepta VARIOS XML (se procesan en orden; pensado para los exports que vayan
llegando). Matching por `erwinLongId` (id determinista de la migración) —
objetos de un XML aún no migrado simplemente cuentan como "sin doc en BD".
Tolerante a XMLs sin relaciones o sin el UDP de partición. Idempotente:
re-correrlo no cambia nada si ya está aplicado. Sin `--apply` es dry-run.
Exit code: 0 ok · 1 hubo XMLs ilegibles.

Uso (desde la raíz del backend):
  .venv/bin/python -m scripts.backfill_cardinalidad_particion "../folder_data/DDV - CPYBCA.xml" [otro.xml ...]
  .venv/bin/python -m scripts.backfill_cardinalidad_particion "../folder_data/DDV - CPYBCA.xml" --apply
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from scripts.erwin_migration import erwin_parser as ep
from scripts.erwin_migration import policies as pol

ACTIVE = {"flgactive": {"$ne": False}}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _process(db, xml_path: str, apply: bool, tot: dict) -> None:
    m = ep.parse(xml_path)

    # ── 1 · parentCardinality desde Null_Option_Type ────────────────────────
    n_upd = n_same = n_missing = 0
    for r in m.relationships.values():
        if r.rel_type not in (ep.REL_IDENTIFYING, ep.REL_NON_IDENTIFYING):
            continue
        want = pol.map_parent_cardinality(r.null_option)
        doc = db.relationships.find_one(
            {"erwinLongId": r.id, **ACTIVE}, {"parentCardinality": 1})
        if doc is None:
            n_missing += 1
            continue
        if doc.get("parentCardinality") == want:
            n_same += 1
            continue
        n_upd += 1
        print(f"  rel {r.name}: parentCardinality {doc.get('parentCardinality')!r} → {want!r}"
              f" (Null_Option_Type={r.null_option!r})")
        if apply:
            db.relationships.update_one(
                {"_id": doc["_id"]},
                {"$set": {"parentCardinality": want, "updatedAt": _now()}})
    print(f"  relaciones: {n_upd} a actualizar · {n_same} ya correctas · {n_missing} sin doc en BD")
    tot["rel_upd"] += n_upd
    tot["rel_missing"] += n_missing

    # ── 2 · isPartition desde el UDP "Particion" (PART_nn congruentes) ──────
    collapsed = pol.collapse_udp_defs(m.udp_defs)
    if pol.PARTITION_UDP_KEY not in collapsed:
        print("  particiones: el XML no define el UDP 'Particion' a nivel columna — se omite")
        return
    udp_vals = pol.resolve_udp_values(m.udp_values, collapsed)
    c_upd = c_same = c_missing = 0
    skipped: list[str] = []
    for e in m.entities.values():
        cols, _dropped = pol.dedupe_columns(e.attributes)
        part_vals = [(a.id, corr) for a in cols
                     if (corr := pol.partition_correlative(
                         udp_vals.get((a.id, pol.PARTITION_UDP_KEY)))) is not None]
        part_ids, skip = pol.partition_marks(part_vals)
        if skip:
            skipped.append(f"{e.physical}: {skip}")
            continue
        for aid in part_ids:
            doc = db.canonical_columns.find_one(
                {"erwinLongId": aid, **ACTIVE}, {"isPartition": 1, "physicalName": 1})
            if doc is None:
                c_missing += 1
                continue
            if doc.get("isPartition") is True:
                c_same += 1
                continue
            c_upd += 1
            print(f"  col {e.physical}.{doc.get('physicalName')}: isPartition → True")
            if apply:
                db.canonical_columns.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"isPartition": True, "updatedAt": _now()}})
    print(f"  columnas partición: {c_upd} a marcar · {c_same} ya marcadas · {c_missing} sin doc en BD")
    if skipped:
        print(f"  ⚠ tablas INCONGRUENTES sin marcar (decisión del owner): {len(skipped)}")
        for s in skipped:
            print(f"    - {s}")
    tot["col_upd"] += c_upd
    tot["col_missing"] += c_missing
    tot["incongruentes"].extend(skipped)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("xml", nargs="+", help="uno o más exports XML de Erwin")
    ap.add_argument("--apply", action="store_true", help="escribir en la BD (sin esto: dry-run)")
    args = ap.parse_args()

    load_dotenv()
    from pymongo import MongoClient
    db = MongoClient(os.environ["COSMOS_CONNECTION_STRING"])[
        os.environ.get("COSMOS_DATABASE", "db_modeler")]

    tag = "APPLY" if args.apply else "DRY-RUN"
    tot = {"rel_upd": 0, "rel_missing": 0, "col_upd": 0, "col_missing": 0,
           "incongruentes": [], "errores": 0}
    for path in args.xml:
        print(f"\n{'=' * 68}\n[{tag}] XML: {path}")
        try:
            _process(db, path, args.apply, tot)
        except Exception as exc:  # XML ilegible/estructura inesperada: seguir con el resto
            tot["errores"] += 1
            print(f"  ✗ ERROR procesando el archivo (se continúa con el resto): {exc}")

    print(f"\n{'=' * 68}\nTOTAL ({len(args.xml)} archivo(s)): "
          f"{tot['rel_upd']} relaciones + {tot['col_upd']} columnas "
          f"{'actualizadas' if args.apply else 'por actualizar'} · "
          f"{tot['rel_missing'] + tot['col_missing']} sin doc en BD · "
          f"{len(tot['incongruentes'])} tablas incongruentes · {tot['errores']} XML con error")
    if not args.apply:
        print("Dry-run: nada se escribió. Repetir con --apply para aplicar.")
    return 1 if tot["errores"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
