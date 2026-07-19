"""Reset previo a una migración Erwin REAL (doc 19 fase B).

Vacía las colecciones de MODELO + ESTÁNDARES + GOVERNANCE para que `migrate`
cargue el XML real sobre una base limpia (sin mezclarse con la demo sintética
ni chocar por claves naturales). PRESERVA la plataforma: usuarios/roles,
naming_config (motor de naming corporativo), column_catalog (invariante:
NUNCA se borra) y audit_log (historia).

A diferencia de `seed_ddv_synthetic` (que borra Y re-siembra TODO, usuarios
incluidos), esto solo limpia lo que la migración va a poblar.

Uso (desde la raíz del backend):
  .venv/bin/python -m scripts.reset_for_migration           # dry-run
  .venv/bin/python -m scripts.reset_for_migration --apply
"""
from __future__ import annotations

import argparse
import os

WIPE = [
    # modelo publicado
    "projects", "folders", "subject_areas", "schemas",
    "canonical_tables", "canonical_columns", "relationships", "views",
    # estándares (el XML real trae los suyos: glosario NSM, dominios, UDPs)
    "parent_domains", "glossary_terms", "udp_definitions", "standards_versions",
    # governance (los changesets de la demo referencian ids que dejan de existir)
    "changesets", "changeset_changes",
    # vestigiales (por si existen)
    "saved_reports", "project_relationships", "project_tables", "semantic_types", "udps",
]
PRESERVE = ["users", "roles", "naming_config", "column_catalog", "audit_log"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Reset de modelo/estándares para migración Erwin real")
    ap.add_argument("--apply", action="store_true", help="borrar de verdad (sin esto: dry-run)")
    args = ap.parse_args()

    from dotenv import load_dotenv

    from app.core.db.sync import get_sync_db
    load_dotenv()
    db = get_sync_db()

    existing = set(db.list_collection_names())
    assert "column_catalog" not in WIPE, "column_catalog NUNCA se borra"

    print("== PRESERVADAS (no se tocan) ==")
    for c in PRESERVE:
        print(f"  {c}: {db[c].count_documents({}) if c in existing else 0} docs")

    print(f"\n== {'BORRANDO' if args.apply else 'DRY-RUN (usar --apply)'} ==")
    total = 0
    for c in WIPE:
        n = db[c].count_documents({}) if c in existing else 0
        total += n
        print(f"  {c}: {n} docs")
        if args.apply and n:
            db[c].delete_many({})
    print(f"\n{'BORRADOS' if args.apply else 'A BORRAR'}: {total} docs en {len(WIPE)} colecciones")

    if args.apply:
        print("\nSiguiente paso: .venv/bin/python -m scripts.erwin_migration.migrate <xml> --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
