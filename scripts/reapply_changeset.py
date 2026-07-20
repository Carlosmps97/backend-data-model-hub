"""Recuperación de un publish interrumpido: re-aplica changesets `approved`
SIN `appliedAt` (el proceso murió entre el claim y el final del apply).

El apply es idempotente (upserts por `_id` + soft-deletes), así que re-ejecutar
converge al estado correcto aunque la corrida anterior haya aplicado parte.
Respeta la ventana del ciclo (`at <= submittedAt`) igual que el publish normal.

Run:  backend-data-model-hub/.venv/bin/python scripts/reapply_changeset.py [cs_id]
      (sin argumento: repara TODOS los approved sin appliedAt)
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def main() -> None:
    from app.core.db.client import connect, disconnect
    from app.features.changesets import repository, service

    target = sys.argv[1] if len(sys.argv) > 1 else None
    await connect()

    stuck = [
        cs for cs in await repository.list_summaries()
        if cs.get("status") == "approved" and not cs.get("appliedAt")
        and (target is None or cs["id"] == target)
    ]
    if not stuck:
        print("nada que reparar: no hay changesets approved sin appliedAt")
        await disconnect()
        return

    for cs in stuck:
        changes = service.changes_in_cycle(
            await repository.changes_map(cs["id"]), cs.get("submittedAt")
        )
        counts = await repository.apply_changes(service.apply_plan(changes))
        if changes.get("parent_domains"):
            await repository.cascade_domain_types(changes["parent_domains"])
        await repository.set_status(cs["id"], {"appliedAt": _now()})
        print(f"re-aplicado {cs['id']} ({cs.get('versionLabel')}): {counts}")

    await disconnect()


if __name__ == "__main__":
    asyncio.run(main())
