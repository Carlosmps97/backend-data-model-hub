"""Recuperación de un publish interrumpido: changesets `approved` SIN
`appliedAt` (el proceso murió entre el claim y el final del apply).

Doc 105: ya NO re-aplica a ciegas (eso saltaba los gates, las imágenes previas
del rollback, la poda de canvases y la cascada de un borrado de proyecto). Cada
versión trabada vuelve a REVISIÓN (`service.recover_interrupted_publish`, igual
que el revert del publish, con `partialApplyAt` conservador) y el revisor la
re-aprueba desde Review: corre el publish completo y converge (el apply es
idempotente).

Revisión R1: con `--workers 2` el publish puede estar VIVO en el otro proceso
(una versión grande tarda). Un claim de hace menos de 30 min
(`service.RECOVER_MIN_AGE_SECONDS`) se salta y se informa; `--force` lo
devuelve igual. Aun forzado, el publish vivo no deja un estado imposible: su
`appliedAt` va condicionado al claim, así que la versión queda en revisión.

Run:  backend-data-model-hub/.venv/bin/python scripts/reapply_changeset.py [--force] [cs_id]
      (sin cs_id: TODAS las approved sin appliedAt)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


async def recover(target: str | None, force: bool = False) -> dict[str, list]:
    """Devuelve a revisión las versiones trabadas (todas, o sólo `target`):
    {"reverted": [cabeceras], "recent": [ids con un claim reciente, salteados],
     "undated": [ids sin fecha de aprobación legible, salteados]}."""
    from app.features.changesets import repository, service

    stuck = [cs for cs in await repository.list_summaries()
             if cs.get("status") == "approved" and not cs.get("appliedAt")
             and (target is None or cs["id"] == target)]
    out: dict[str, list] = {"reverted": [], "recent": [], "undated": []}
    for cs in stuck:
        back = await service.recover_interrupted_publish(
            cs["id"], min_age_seconds=0 if force else service.RECOVER_MIN_AGE_SECONDS)
        if back in ("recent", "undated"):
            out[back].append(cs["id"])
        elif back:
            out["reverted"].append(back)
    return out


async def main() -> None:
    from app.core.db.client import connect, disconnect

    args = sys.argv[1:]
    force = "--force" in args
    rest = [a for a in args if a != "--force"]
    target = rest[0] if rest else None
    await connect()
    try:
        out = await recover(target, force=force)
        if not out["reverted"] and not out["recent"] and not out["undated"]:
            print("nada que reparar: no hay changesets approved sin appliedAt")
        for cs in out["reverted"]:
            print(f"devuelto a revisión {cs['id']} ({cs.get('versionLabel')}): "
                  "el revisor debe aprobarlo de nuevo desde Review")
        for cs_id in out["recent"]:
            print(f"salteado {cs_id}: su aprobación es de hace menos de 30 min y puede seguir "
                  "publicándose en el otro proceso — reintenta más tarde o usa --force")
        for cs_id in out["undated"]:
            print(f"salteado {cs_id}: su aprobación no tiene fecha legible (sin fecha) — verifica a mano "
                  "que ningún proceso la esté publicando y usa --force")
    finally:
        await disconnect()


if __name__ == "__main__":
    asyncio.run(main())
