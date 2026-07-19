"""Backfill de definiciones de COLUMNA-DE-VISTA (F5) SIN re-migrar.

Puebla `views.sources[].description` con la definición PROPIA de cada columna en
la vista del XML — SOLO las que APORTAN (difieren de la del origen físico, o el
origen no tiene). NO re-corre el migrate (que pisaría layouts y las correcciones
de partición del owner); toca ÚNICAMENTE `sources[].description`.

Mismo criterio que `migrate.views()` → un backfill deja la BD igual que una
migración fresca, columna por columna. Idempotente. Multi-XML (para los exports
que vengan). Dry-run por defecto.

Uso:
  .venv/bin/python -m scripts.backfill_view_col_defs                    # dry-run, XML DDV
  .venv/bin/python -m scripts.backfill_view_col_defs a.xml b.xml --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

DEFAULT_XML = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "folder_data", "DDV - CPYBCA.xml"))
ACTIVE = {"flgactive": {"$ne": False}}
PRINT_CAP = 15  # detalle por vista hasta acá; el resto se resume


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _distinct_defs_for_view(v, attr_idx) -> dict[str, str]:
    """{outputAlias → definición} de las columnas de la vista cuya def PROPIA
    APORTA (difiere del origen físico o el origen no tiene). Espeja migrate."""
    from scripts.erwin_migration import policies as pol
    out: dict[str, str] = {}
    cols, _ = pol.dedupe_columns(v.attributes)
    for a in cols:
        origin = attr_idx.get(a.parent_attr_ref or "")
        vdef = (a.definition or a.comment or "").strip()
        odef = ((origin.definition or origin.comment or "").strip() if origin else "")
        if vdef and vdef != odef:
            out[a.physical] = vdef
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("xml", nargs="*", help="XML(s) de Erwin (default: DDV)")
    ap.add_argument("--apply", action="store_true", help="escribir en la BD (sin esto: dry-run)")
    args = ap.parse_args()
    xmls = args.xml or [DEFAULT_XML]

    load_dotenv()
    from app.core.db.sync import get_sync_db
    from scripts.erwin_migration import policies as pol
    from scripts.erwin_migration.erwin_parser import parse
    db = get_sync_db()

    tag = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{tag}] backfill de definiciones de columna-de-vista (F5)")
    tot_views = tot_cols = tot_missing = 0
    printed = 0
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
        attr_idx = m.attr_index()
        for v in m.views.values():
            defs = _distinct_defs_for_view(v, attr_idx)
            if not defs:
                continue
            pid = pol.platform_id(v.id)
            doc = db.views.find_one({"_id": pid, **ACTIVE}, {"sources": 1, "name": 1})
            if not doc:
                tot_missing += 1
                continue
            sources = doc.get("sources") or []
            n = 0
            for s in sources:
                alias = s.get("outputAlias") or s.get("column")
                d = defs.get(alias)
                if d and s.get("description") != d:
                    s["description"] = d
                    n += 1
            if not n:
                continue
            tot_views += 1
            tot_cols += n
            if printed < PRINT_CAP:
                print(f"  · {doc.get('name') or pid}: {n} columna(s) con def propia")
                printed += 1
            elif printed == PRINT_CAP:
                print("  · … (resto omitido del detalle; ver totales)")
                printed += 1
            if args.apply:
                db.views.update_one(
                    {"_id": pid}, {"$set": {"sources": sources, "updatedAt": _now()}})

    print(f"\n[{tag}] vistas afectadas={tot_views} · columnas con def propia={tot_cols}"
          f" · vistas del XML no migradas (omitidas)={tot_missing}")
    if not args.apply:
        print("Dry-run: nada se escribió. Repetir con --apply para aplicar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
