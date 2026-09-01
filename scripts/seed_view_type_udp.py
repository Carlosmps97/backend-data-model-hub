"""Seed del UDP "Tipo de Vista" (doc 61 r2) — el único UDP inicial de nivel
'view'. La definición sale del CATÁLOGO FIJO del kit
(`scripts/erwin_migration/standard_udps.py`, una sola fuente de verdad):
{name: "Tipo de Vista", level: 'view', dataType: 'list',
allowedValues: ["Regular", "Personalizada"], defaultValue: "Regular"} — VÍA el
flujo versionado de Data Standards (kind='udp' → nueva StandardsVersion con
snapshot y rollback), NUNCA insert directo a `udp_definitions`.

Para BD ya migrada SIN re-correr el one-shot: este seed agrega solo el UDP de
vistas. Una re-migración (doc 54) siembra el catálogo completo por sí sola.

Semántica: "Regular" = la vista se genera de sources/filter/joinOverride;
"Personalizada" = la gobierna su Query SQL custom. El valor por vista lo
sincroniza la web al guardar desde la pestaña Query SQL; este UDP es el dato
REPORTABLE del modo (la verdad técnica es `customSql`, ver doc 61 §2.3).

Guardas (idempotente): si ya existe una definición ACTIVA con level='view' y
name "View Type" (case-insensitive), no hace nada.

Dry-run por default; `--apply` para escribir. Lo ejecuta el owner:
  .venv/bin/python -m scripts.seed_view_type_udp            # dry-run
  .venv/bin/python -m scripts.seed_view_type_udp --apply [--actor admin]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.erwin_migration.standard_udps import FIXED_UDPS

_FIXED_VT = next(d for d in FIXED_UDPS if d["level"] == "view")
VIEW_TYPE_NAME = _FIXED_VT["name"]           # "Tipo de Vista"
ALLOWED = list(_FIXED_VT["allowedValues"])   # ["Regular", "Personalizada"]
DEFAULT = _FIXED_VT["defaultValue"]          # "Regular"


def find_existing(defs: list[dict]) -> dict | None:
    """Definición ya sembrada (name CI + level 'view'). Pura."""
    for d in defs:
        if d.get("level") == "view" and (d.get("name") or "").strip().lower() == VIEW_TYPE_NAME.lower():
            return d
    return None


async def main(apply: bool, actor: str) -> None:
    from app.core.db.client import connect, disconnect
    from app.features.data_standards import service as std_service
    from app.features.data_standards.schemas import ApplyBody, UdpEdit
    from app.features.udp import repository as udp_repo

    await connect()
    try:
        existing = find_existing(await udp_repo.list_udp())
        if existing:
            print(f"OK (idempotente): ya existe '{existing.get('name')}' "
                  f"(id={existing.get('id')}, level=view). Nada que hacer.")
            return
        print(f"Se creará el UDP '{VIEW_TYPE_NAME}' (level=view, list, "
              f"allowed={ALLOWED}, default={DEFAULT!r}) vía Standards apply.")
        if not apply:
            print("DRY-RUN: no se escribió nada. Ejecutar con --apply.")
            return
        body = ApplyBody(
            kind="udp", title="UDP Tipo de Vista (doc 61)",
            udpUpsert=[UdpEdit(name=VIEW_TYPE_NAME, level="view", dataType="list",
                               allowedValues=ALLOWED, defaultValue=DEFAULT,
                               description=_FIXED_VT.get("description"))])
        version = await std_service.apply(actor, body)
        print(f"APLICADO: versión Standards {version.get('seq') or version}")
    finally:
        await disconnect()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="escribe (default: dry-run)")
    ap.add_argument("--actor", default="admin", help="username que firma la versión")
    args = ap.parse_args()
    asyncio.run(main(args.apply, args.actor))
