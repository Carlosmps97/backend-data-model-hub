"""Marca lo cargado como la VERSION BASE (v1) de la plataforma.

La migracion Erwin escribe DIRECTO a las colecciones publicadas: no crea
changesets. Sin una version aplicada, la web bloquea el modulo Model (el
dialogo "Open model" exige una version de produccion publicada para abrir en
read-only o para crear working versions). Este script cierra ese
huevo-y-gallina: crea el changeset MARCADOR `versionLabel="v1"` (status
approved, appliedAt estampado, 0 cambios) que significa "todo lo migrado es
la version base".

Se corre UNA sola vez, AL FINAL de la carga completa (aunque sean 15 XML:
primero todos los `migrate --apply`, al final este marcador). Ademas deja el
resto del cierre:
  - baseline de Data Standards si el stream esta vacio (si
    `seed_ddl_export_rules` ya registro su version, no hace falta);
  - permiso `rollback` en los roles (administrador/revisor).

Guardas (idempotente): si ya existe una version `v1` o cualquier version
APLICADA, no crea el marcador (la plataforma ya tiene historial).

Dry-run por default; `--apply` para escribir.
  .venv/bin/python -m scripts.mark_base_version            # dry-run
  .venv/bin/python -m scripts.mark_base_version --apply
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_ACTIVE = {"flgactive": {"$ne": False}}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def main(apply: bool) -> None:
    from app.core.db.client import connect, disconnect, get_db
    from app.features.changesets.models import ChangesetDoc
    from app.features.data_standards import service as std_service

    await connect()
    db = await get_db()
    try:
        css = await db["changesets"].find(
            {}, {"versionLabel": 1, "status": 1, "appliedAt": 1, "title": 1}
        ).to_list(None)
        v1 = next((c for c in css if c.get("versionLabel") == "v1"), None)
        applied = [c for c in css if c.get("appliedAt")]
        n_std = await db["standards_versions"].count_documents({})
        n_tables = await db["canonical_tables"].count_documents(_ACTIVE)

        print(f"Changesets: {len(css)} (aplicados: {len(applied)}) | "
              f"standards_versions: {n_std} | tablas activas: {n_tables}")

        make_marker = v1 is None and not applied
        if v1 is not None:
            print("- v1 ya existe -> no se crea marcador.")
        elif applied:
            print("- OJO: hay versiones APLICADAS sin label v1 -> no se crea "
                  "marcador (revisar a mano).")
        else:
            print("- Se crea el marcador v1 'Base - Modelo DDV (XML)' "
                  "(approved, 0 cambios).")
        print("- Baseline de Data Standards: "
              + ("se crea." if n_std == 0 else "ya hay versiones, no hace falta."))
        print("- Permiso `rollback`: se asegura en administrador/revisor.")

        if not apply:
            print("\nDRY-RUN - nada escrito. Corre con --apply.")
            return

        if make_marker:
            now = _now()
            projects = await db["projects"].find(_ACTIVE, {"_id": 1}).to_list(None)
            cs = ChangesetDoc.model_validate({
                "id": str(uuid.uuid4()),
                "title": "Base - Modelo DDV (XML)",
                "owner": "system",
                "status": "approved",
                "description": ("Version base: el estado publicado tal como "
                                "quedo tras la migracion de los XML de Erwin. "
                                "Marcador sin cambios."),
                "versionLabel": "v1",
                "projectIds": [str(p["_id"]) for p in projects],
                "createdAt": now, "updatedAt": now, "submittedAt": now,
                "reviewedBy": "system", "reviewedAt": now,
                "reviewNote": "Marcador creado por scripts.mark_base_version.",
                "appliedAt": now,
            })
            await db["changesets"].insert_one(
                {"_id": cs.id, **cs.model_dump(exclude={"id"})})
            print(f"v1 creado: changeset {cs.id[:8]}... (approved, 0 cambios).")

        if n_std == 0:
            v = await std_service._record(
                "system", "baseline", "Base - Data Standards (XML DDV)",
                "Baseline inicial de estandares del XML migrado.",
                {"added": [], "edited": [], "removed": []},
                {"tables": 0, "columns": 0})
            print(f"Baseline de Data Standards: {v.get('label')} (seq {v.get('seq')}).")

        for role_id, granted in (("administrador", True), ("revisor", True),
                                 ("modelador", False), ("lector", False)):
            await db["roles"].update_one(
                {"_id": role_id}, {"$set": {"permissions.rollback": granted}})
        print("Permiso `rollback` asegurado.")
        print("\nOK - la web ya tiene produccion v1 (Model y Data Standards operativos).")
    finally:
        await disconnect()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Crea el marcador de version base v1 tras la migracion")
    ap.add_argument("--apply", action="store_true",
                    help="escribir de verdad (sin esto: dry-run)")
    asyncio.run(main(ap.parse_args().apply))
