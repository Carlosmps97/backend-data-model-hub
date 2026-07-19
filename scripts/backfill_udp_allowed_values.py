"""Backfill de VALORES PERMITIDOS de UDP (estándares COMPLETOS) SIN re-migrar.

Carga la lista explícita `tag_Udp_Values_List` de cada UDP del XML — TODOS los
valores aunque NO se usen en ninguna tabla — y crea las defs que no existían por
0 uso. Las UDP/glosario/dominios se repiten entre XML y pueden usarse en otras
tablas, así que el estándar debe estar COMPLETO (pedido owner 2026-07-18).

NO re-migra (eso pisaría las particiones/layout del doc 21). Mismo criterio que
`migrate.standards()` → deja la BD igual que una migración con el parser nuevo.
Multi-XML, idempotente, dry-run por defecto.

Uso:
  .venv/bin/python -m scripts.backfill_udp_allowed_values                    # dry-run
  .venv/bin/python -m scripts.backfill_udp_allowed_values a.xml b.xml --apply
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

DEFAULT_XML = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "folder_data", "DDV - CPYBCA.xml"))
ACTIVE = {"flgactive": {"$ne": False}}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _allowed_for(entry: dict) -> list[str]:
    """Lista explícita + default (list types); [] para texto. Preserva orden."""
    if entry["dataType"] != "list":
        return []
    allowed = list(entry.get("allowed_values") or [])
    if entry.get("default") and entry["default"] not in allowed:
        allowed.append(entry["default"])
    return allowed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("xml", nargs="*", help="XML(s) de Erwin (default: DDV)")
    ap.add_argument("--apply", action="store_true", help="escribir en la BD (sin esto: dry-run)")
    args = ap.parse_args()
    xmls = args.xml or [DEFAULT_XML]

    load_dotenv()
    from pymongo import MongoClient
    from scripts.erwin_migration import policies as pol
    from scripts.erwin_migration.erwin_parser import parse
    db = MongoClient(os.environ["COSMOS_CONNECTION_STRING"])[
        os.environ.get("COSMOS_DATABASE", "db_modeler")]

    tag = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{tag}] backfill de valores permitidos de UDP (estándares completos)")
    created = updated = 0
    for xml in xmls:
        if not os.path.exists(xml):
            print(f"\n✗ no existe: {xml}")
            continue
        print(f"\n■ {xml}")
        try:
            m = parse(xml)
        except Exception as e:  # XML ilegible: no aborta el lote
            print(f"  ✗ no se pudo parsear ({e}) — se omite")
            continue
        collapsed = pol.collapse_udp_defs(m.udp_defs)
        for key, entry in collapsed.items():
            if entry["dataType"] != "list":
                continue  # el backfill carga LISTAS de valores; texto no aporta
            allowed = _allowed_for(entry)
            existing = db.udp_definitions.find_one(
                {"name": re.compile(f"^{re.escape(entry['name'])}$", re.I),
                 "level": entry["level"], **ACTIVE},
                {"_id": 1, "allowedValues": 1})
            if existing:
                cur = existing.get("allowedValues") or []
                missing = [v for v in allowed if v not in cur]
                if missing:
                    updated += 1
                    print(f"  ~ {entry['level']}·{entry['name']}: +{len(missing)} → {missing}")
                    if args.apply:
                        db.udp_definitions.update_one(
                            {"_id": existing["_id"]},
                            {"$set": {"allowedValues": cur + missing, "updatedAt": _now()}})
                continue
            # def inexistente (no migró por 0 uso) → crearla COMPLETA
            created += 1
            vals = f"{len(allowed)} valor(es)" if allowed else "texto"
            print(f"  + {entry['level']}·{entry['name']} ({entry['dataType']}): {vals}")
            if args.apply:
                pid = pol.platform_id(f"udp|{key}")
                db.udp_definitions.update_one(
                    {"_id": pid},
                    {"$set": {"name": entry["name"], "level": entry["level"],
                              "dataType": entry["dataType"], "defaultValue": entry["default"],
                              "allowedValues": allowed, "description": "Migrada de Erwin",
                              "updatedAt": _now()},
                     "$setOnInsert": {"flgactive": True, "createdAt": _now()}},
                    upsert=True)

    print(f"\n[{tag}] defs creadas={created} · defs con valores agregados={updated}")
    if not args.apply:
        print("Dry-run: nada se escribió. Repetir con --apply para aplicar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
