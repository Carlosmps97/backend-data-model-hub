"""Siembra la plantilla de hoja Excel «QA_MODELO» (doc 95 D11) POR PROYECTO.

Pasa por `sheet_templates.service.create_default` (la MISMA ruta que el botón
«Create QA_MODELO template» de Reporting): queda COMPARTIDA, con dueño `system`
(la gobierna un admin). NO re-siembra: si el proyecto ya tiene la built-in
(editada o no), lo salta.

Dry-run por default; `--apply` para escribir.
  .venv/bin/python -m scripts.seed_sheet_templates --all-projects            # dry-run
  .venv/bin/python -m scripts.seed_sheet_templates --all-projects --apply
  .venv/bin/python -m scripts.seed_sheet_templates --project "MODELO DDV" --apply
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


def plan_lines(template: dict) -> list[str]:
    """Descripción legible de la plantilla (dry-run y apply). Puro."""
    out = [f"   hoja «{template['sheetName']}» · {len(template['columns'])} columnas"]
    if template.get("fileName"):
        out.append(f"   archivo «{template['fileName']}.xlsx» (marcadores con la hora local de quien exporta)")
    out += [f"      {c['header']:<28} ← {c['source']}" for c in template["columns"]]
    return out


async def seed_one(project_id: str, project_name: str, apply: bool) -> None:
    from app.features.reporting.sheet_templates import repository, service
    from app.features.reporting.sheet_templates.builtin import BUILTIN_ORIGIN, QA_MODELO

    print(f"\n{'═' * 72}\nPROYECTO «{project_name}» ({project_id[:8]}…)")
    if await repository.has_origin(project_id, BUILTIN_ORIGIN):
        print("SALTADO: el proyecto ya tiene la plantilla «QA_MODELO» — no re-siembro.")
        return
    for line in plan_lines(QA_MODELO):
        print(line)
    if not apply:
        print("── DRY-RUN — nada escrito. Con --apply se crea la plantilla «QA_MODELO» en el proyecto.")
        return
    try:
        doc = await service.create_default("system", project_id)
    except service.TemplateNameTaken:
        print("SALTADO: ya hay una plantilla de usuario llamada «QA_MODELO» en el proyecto.")
        return
    if doc is None:
        print("SALTADO (carrera): la plantilla apareció entre el plan y el apply.")
        return
    print(f"\nOK · plantilla «{doc['name']}» ({doc['id'][:8]}…) creada")


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
    ap = argparse.ArgumentParser(description="Siembra la plantilla Excel «QA_MODELO» (por proyecto)")
    ap.add_argument("--apply", action="store_true", help="escribir de verdad (sin esto: dry-run)")
    scope = ap.add_mutually_exclusive_group(required=True)
    scope.add_argument("--project", help="nombre del proyecto destino")
    scope.add_argument("--all-projects", action="store_true", help="todos los proyectos activos")
    _a = ap.parse_args()
    asyncio.run(main(_a.apply, _a.project, _a.all_projects))
