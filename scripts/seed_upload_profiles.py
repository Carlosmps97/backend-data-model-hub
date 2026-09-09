"""Siembra el perfil de carga «Plantilla BCP» (doc 78 §9) POR PROYECTO: hojas
Cargar_Tablas / Cargar_Campos de Plantilla.xlsx, cabecera en la fila 5, UDP de
tabla en ambas facetas, clasificación de campo en Attribute (L) + Column (P).

Pasa por `profiles.service.create_default` (la MISMA ruta que el botón «Create
default profile» de la web): resuelve los UDP por (level, view, name) contra el
catálogo del proyecto; una def ausente deja la columna en `ignore` (aviso).
NO re-siembra: si el proyecto ya tiene el perfil built-in, lo salta.

Dry-run por default; `--apply` para escribir.
  .venv/bin/python -m scripts.seed_upload_profiles --all-projects            # dry-run
  .venv/bin/python -m scripts.seed_upload_profiles --all-projects --apply
  .venv/bin/python -m scripts.seed_upload_profiles --project "MODELO DDV" --apply
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.seed_ddl_export_rules import select_projects  # noqa: E402

# Consola en UTF-8 (Windows viene en cp1252 y estos prints llevan acentos).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def plan_lines(body: dict, udp_defs: list[dict]) -> list[str]:
    """Descripción legible del perfil resuelto (dry-run y apply). Puro."""
    names = {str(d.get("id")): f"{d.get('name')} ({d.get('view') or 'physical'})" for d in udp_defs}
    out: list[str] = []
    for role, sheet in body["sheets"].items():
        out.append(f"   hoja {role}: «{sheet['name']}» · cabecera en fila {sheet.get('headerRow')} · "
                   f"{'requerida' if sheet.get('required', True) else 'opcional'} · {len(sheet['mappings'])} mapeos")
        for m in sheet["mappings"]:
            t = m["target"]
            if t["kind"] == "field":
                dest = f"field {t['field']}"
            elif t["kind"] == "udp":
                dest = "udp " + " + ".join(names.get(u, u) for u in t["udpIds"])
            else:
                dest = "ignore"
            rules = ", ".join(f"{r['type']}{'=' + str(r['value']) if r.get('value') is not None else ''}"
                              for r in m.get("rules") or [])
            out.append(f"      {m['header']:<28} → {dest}" + (f"   [{rules}]" if rules else ""))
    return out


async def seed_one(project_id: str, project_name: str, apply: bool) -> None:
    from app.features.bulk_upload.profiles import repository, service
    from app.features.bulk_upload.profiles.builtin import BUILTIN_ORIGIN, PLANTILLA_BCP, materialize
    from app.features.udp import repository as udp_repo

    print(f"\n{'═' * 72}\nPROYECTO «{project_name}» ({project_id[:8]}…)")
    existing = await repository.list_profiles(project_id)
    if any(p.get("origin") == BUILTIN_ORIGIN for p in existing):
        print("SALTADO: el proyecto ya tiene el perfil «Plantilla BCP» — no re-siembro.")
        return
    defs = await udp_repo.list_udp(project_id)
    body, warnings = materialize(PLANTILLA_BCP, defs)
    print(f"UDP defs del proyecto: {len(defs)} · perfiles existentes: {len(existing)}")
    for line in plan_lines(body, defs):
        print(line)
    for w in warnings:
        print(f"   AVISO: {w}")
    if not apply:
        print("── DRY-RUN — nada escrito. Con --apply se crea el perfil «Plantilla BCP» en el proyecto.")
        return
    try:
        res = await service.create_default("system", project_id)
    except service.ProfileInvalid as exc:
        print("\nERROR de validación del perfil:")
        for p in exc.problems:
            print(f"   - {p['path']}: {p['message']}")
        raise SystemExit(1) from exc
    if res is None:
        print("SALTADO (carrera): el perfil apareció entre el plan y el apply.")
        return
    doc, _ = res
    print(f"\nOK · perfil «{doc['name']}» ({doc['id'][:8]}…) creado · default={doc['isDefault']}")


async def main(apply: bool, project: str | None = None, all_projects: bool = False) -> None:
    from app.core.db.client import connect, disconnect
    from app.features.projects import repository as projects_repo

    await connect()
    try:
        targets = select_projects(await projects_repo.list_projects(), project, all_projects)
        print(f"Proyectos destino: {len(targets)} → " + ", ".join(f"«{p['name']}»" for p in targets))
        for p in targets:
            await seed_one(p["id"], p["name"], apply)
    finally:
        await disconnect()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Siembra el perfil de carga «Plantilla BCP» (por proyecto)")
    ap.add_argument("--apply", action="store_true", help="escribir de verdad (sin esto: dry-run)")
    scope = ap.add_mutually_exclusive_group(required=True)
    scope.add_argument("--project", help="nombre del proyecto destino")
    scope.add_argument("--all-projects", action="store_true", help="todos los proyectos activos")
    _a = ap.parse_args()
    asyncio.run(main(_a.apply, _a.project, _a.all_projects))
