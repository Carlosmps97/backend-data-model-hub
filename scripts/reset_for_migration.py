"""Reset DESTRUCTIVO previo a una migración one-shot (doc 54).

Borra TODA la información de la plataforma: enumera las tablas existentes en
el schema (`list_collection_names`, que lee `pg_tables`) y las DROPea —
modelo, estándares, governance, usuarios, roles, auditoría y cualquier
vestigial. Incluye `column_catalog`: pertenecía al planteamiento inicial con
agente conversacional embebido, descartado por completo (la plataforma es
únicamente el reemplazo de Erwin; doc 54 §3.1).

No deja la base "vacía y rota": el siguiente `connect()` (p. ej.
`scripts/create_admin.py`, paso inmediato del one-shot) re-crea las 19
colecciones propias con sus índices (`KNOWN_COLLECTIONS` + `ensure_indexes`).
La secuencia completa la orquesta `scripts/run_migration.py --folder`.

Uso (desde la raíz del backend):
  .venv/bin/python -m scripts.reset_for_migration           # dry-run
  .venv/bin/python -m scripts.reset_for_migration --apply
"""
from __future__ import annotations

import argparse


def wipe(db, apply: bool) -> tuple[int, int]:
    """DROPea todas las tablas del schema. Retorna (tablas, docs)."""
    names = sorted(db.list_collection_names())
    print(f"== {'BORRANDO (drop)' if apply else 'DRY-RUN (usar --apply)'} ==")
    total = 0
    for c in names:
        n = db[c].count_documents({})
        total += n
        print(f"  {c}: {n} docs")
        if apply:
            db[c].drop()
    print(f"\n{'BORRADAS' if apply else 'A BORRAR'}: {len(names)} tablas · {total} docs")
    return len(names), total


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Reset DESTRUCTIVO: dropea TODAS las tablas del schema")
    ap.add_argument("--apply", action="store_true",
                    help="borrar de verdad (sin esto: dry-run)")
    args = ap.parse_args()

    from dotenv import load_dotenv

    from app.core.db.sync import get_sync_db
    load_dotenv()
    db = get_sync_db()
    wipe(db, args.apply)

    if args.apply:
        print("\nSiguiente paso: scripts/create_admin.py — re-crea tablas e "
              "índices (connect), los 4 roles de caja y admin/admin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
