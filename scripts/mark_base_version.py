"""Marca lo cargado como la VERSION BASE (v1) de CADA PROYECTO.

La migracion Erwin escribe DIRECTO a las colecciones publicadas: no crea
changesets. Sin una version aplicada, la web bloquea el modulo Model del
proyecto (el dialogo "Open model" exige una version de produccion publicada
para abrir en read-only o para crear working versions). Este script cierra
ese huevo-y-gallina, proyecto por proyecto (doc 75: versiones independientes):
crea el changeset MARCADOR `versionLabel="v1"` (status approved, appliedAt
estampado, 0 cambios) que significa "todo lo migrado es la version base"
del proyecto.

Se corre UNA sola vez, AL FINAL de la carga completa (primero todos los
`migrate --apply`, al final este marcador). Ademas deja el resto del cierre:
  - baseline de Data Standards del proyecto si su stream esta vacio (si
    `seed_ddl_export_rules` ya registro su version, no hace falta);
  - permiso `rollback` en los roles (administrador/revisor).

Guardas (idempotente): un proyecto que ya tiene alguna version APLICADA no
recibe marcador (la plataforma ya tiene historial ahi).

Dry-run por default; `--apply` para escribir. `--title` pone el título del
marcador (default: "Base - Migración Erwin (XML)").
  .venv/bin/python -m scripts.mark_base_version            # dry-run
  .venv/bin/python -m scripts.mark_base_version --apply [--title "Base - ..."]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_ACTIVE = {"flgactive": {"$ne": False}}


async def main(apply: bool, title: str) -> None:
    from app.core.db.client import connect, disconnect, get_db
    from app.features.changesets import service as cs_service
    from app.features.data_standards import service as std_service
    from app.features.projects import repository as projects_repo

    await connect()
    db = await get_db()
    try:
        projects = await projects_repo.list_projects()
        print(f"Proyectos activos: {len(projects)}")
        plan: list[tuple[dict, bool, bool]] = []
        for p in projects:
            pid = p["id"]
            applied = await cs_service.applied_versions(pid)
            n_std = await db["standards_versions"].count_documents({"projectId": pid})
            n_tables = await db["canonical_tables"].count_documents({**_ACTIVE, "projectId": pid})
            make_marker, make_baseline = not applied, n_std == 0
            plan.append((p, make_marker, make_baseline))
            print(f"\n«{p['name']}» — tablas activas: {n_tables} · versiones aplicadas: "
                  f"{len(applied)} · standards_versions: {n_std}")
            print("  - marcador v1: " + (f"se crea '{title}' (approved, 0 cambios)." if make_marker
                                         else "ya hay versiones aplicadas → no se crea."))
            print("  - baseline de Data Standards: " + ("se crea." if make_baseline
                                                        else "ya hay versiones, no hace falta."))
        print("\n- Permiso `rollback`: se asegura en administrador/revisor.")

        if not apply:
            print("\nDRY-RUN - nada escrito. Corre con --apply.")
            return

        for p, make_marker, make_baseline in plan:
            pid = p["id"]
            if make_marker:
                cs = await cs_service.create_base_marker(pid, title)
                print(f"«{p['name']}»: v1 creado — changeset {cs['id'][:8]}... (approved, 0 cambios).")
            if make_baseline:
                v = await std_service._record(
                    "system", pid, "baseline", "Base - Data Standards (XML)",
                    "Baseline inicial de estandares del XML migrado.",
                    {"added": [], "edited": [], "removed": []},
                    {"tables": 0, "columns": 0})
                print(f"«{p['name']}»: baseline de Data Standards {v.get('label')} (seq {v.get('seq')}).")

        for role_id, granted in (("administrador", True), ("revisor", True),
                                 ("modelador", False), ("lector", False)):
            await db["roles"].update_one(
                {"_id": role_id}, {"$set": {"permissions.rollback": granted}})
        print("Permiso `rollback` asegurado.")
        print("\nOK - cada proyecto tiene produccion v1 (Model y Data Standards operativos).")
    finally:
        await disconnect()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Crea el marcador de version base v1 de cada proyecto tras la migracion")
    ap.add_argument("--apply", action="store_true",
                    help="escribir de verdad (sin esto: dry-run)")
    ap.add_argument("--title", default="Base - Migración Erwin (XML)",
                    help="título del changeset marcador v1")
    _args = ap.parse_args()
    asyncio.run(main(_args.apply, _args.title))
