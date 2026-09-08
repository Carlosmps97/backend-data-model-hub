"""Vuelve UN proyecto (o todos) a su VERSIÓN BASE: solo v1 (Model) + baseline
(Standards) — doc 75: las versiones son independientes por proyecto, así que
el reset también.

Por proyecto, deshace TODAS las versiones posteriores a su base directo en
producción usando sus imágenes previas (restaura las entidades modificadas,
borra las creadas), conserva la versión BASE — el changeset `versionLabel="v1"`
SIN cambios que marca el estado recién migrado del XML — y todos sus datos (la
migración y las correcciones aplicadas a publicado NO se tocan: acá no se
re-migra nada). Después borra los registros de las demás versiones del
proyecto (aplicadas, drafts, submitted y rejected: el historial de la web
queda solo con v1), crea el baseline de Data Standards del proyecto si no
existe ninguno y asegura el permiso `rollback` en los roles.

Resultado: la web muestra UNA versión por módulo en ese proyecto (Model v1 ·
Standards v1) y cualquier versión futura puede volver a la base con el
rollback normal de la plataforma (doc 27).

Prerrequisito: que exista el changeset base `versionLabel="v1"` del proyecto
(lo dejó la migración real; si no existe, ese proyecto se salta sin tocar
nada).

DESTRUCTIVO. **Dry-run por default**; `--apply` para escribir.
  .venv/bin/python -m scripts.reset_to_base_version                       # dry-run, todos
  .venv/bin/python -m scripts.reset_to_base_version --project "Modelo DDV" --apply
  .venv/bin/python -m scripts.reset_to_base_version --apply               # todos los proyectos
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STRUCT = ("projects", "folders", "subject_areas", "schemas", "views")


def _lbl(c: dict) -> str:
    return c.get("versionLabel") or (str(c.get("title") or c["_id"])[:18])


async def reset_project(db, project: dict, apply: bool) -> None:
    from app.features.changesets.service import rollback_plan
    from app.features.data_standards import service as std_service

    pid, name = project["id"], project["name"]
    print(f"\n{'═' * 72}\nPROYECTO «{name}» ({pid[:8]}…)")

    # 1) BASE (v1) vs versiones de PRUEBA (el resto) — del proyecto.
    css = await db["changesets"].find(
        {"projectId": pid}, {"versionLabel": 1, "title": 1, "status": 1, "appliedAt": 1}).to_list(None)
    base = next((c for c in css if c.get("versionLabel") == "v1"), None)
    if not base:
        print("  SALTADO: no encuentro la versión BASE (versionLabel='v1') del proyecto.")
        return
    base_id = str(base["_id"])
    tests = [c for c in css if str(c["_id"]) != base_id]
    applied = [c for c in tests if c.get("appliedAt")]
    # appliedAt DESC (más reciente primero) → al componer, el inverso más VIEJO
    # (más cercano a la base) queda de último y GANA por entidad.
    applied.sort(key=lambda c: c.get("appliedAt") or "", reverse=True)

    print(f"  BASE conservada:  {_lbl(base)}  ({base_id[:8]}…)")
    print(f"  Versiones de PRUEBA a deshacer ({len(applied)} aplicadas / {len(tests)} totales):")
    for c in applied:
        print(f"     - {_lbl(c)}  [{c.get('status')}]")

    # 2) Componer el inverso (oldest-wins) de las de prueba APLICADAS.
    inverse: dict[tuple[str, str], dict] = {}
    missing: list[str] = []
    for c in applied:
        raw = await db["changeset_changes"].find({"csId": str(c["_id"])}).to_list(None)
        changes: dict[str, dict] = {}
        for ch in raw:
            changes.setdefault(ch["collection"], {})[ch["entityId"]] = ch
        inv, miss = rollback_plan(changes)
        missing += miss
        for ic in inv:
            inverse[(ic["collection"], ic["entityId"])] = ic     # oldest (último) gana

    if missing:
        print(f"  ABORT: {len(missing)} cambios SIN imagen previa (no reconstruible): {missing[:6]}")
        return

    dels = [(c, e) for (c, e), ic in inverse.items() if ic["op"] == "delete"]
    ups = [(c, e, ic["payload"]) for (c, e), ic in inverse.items() if ic["op"] == "upsert"]
    print(f"  Inverso compuesto: {len(inverse)} entidades")
    print("    RESTAURAR (modificadas/borradas en prueba):", dict(Counter(c for c, _, _ in ups)))
    print("    BORRAR (creadas en prueba):                ", dict(Counter(c for c, _ in dels)))
    struct = dict(Counter(c for c, _ in dels if c in STRUCT))
    if struct:
        print("       · estructura basura a borrar:            ", struct)
    n_std = await db["standards_versions"].count_documents({"projectId": pid})

    if not apply:
        print("  ── DRY-RUN — nada escrito. Con --apply se aplica y además:")
        print(f"     · se borran los registros de {len(tests)} versión(es) de prueba (se conserva v1);")
        print("     · se crea el baseline de Data Standards del proyecto"
              + (" (no hay ninguno);" if n_std == 0 else f" — ya tiene {n_std}, no hace falta;"))
        return

    # 3) APLICAR el inverso en producción.
    for coll, eid, payload in ups:
        doc = {k: v for k, v in (payload or {}).items() if k != "id"}
        doc["_id"] = eid
        doc["flgactive"] = True
        await db[coll].replace_one({"_id": eid}, doc, upsert=True)
    for coll, eid in dels:
        await db[coll].delete_one({"_id": eid})
    print(f"  Aplicado: {len(ups)} restauradas, {len(dels)} borradas.")

    # 4) Borrar los registros de las versiones de prueba (todas menos la base).
    test_ids = [str(c["_id"]) for c in tests]
    r1 = await db["changeset_changes"].delete_many({"csId": {"$in": test_ids}})
    r2 = await db["changesets"].delete_many({"_id": {"$in": test_ids}})
    print(f"  Versiones de prueba borradas: {r2.deleted_count} changesets + {r1.deleted_count} cambios. Queda v1.")

    # 5) Baseline de Data Standards del proyecto (si no hay ninguno).
    if n_std == 0:
        v = await std_service._record(
            "system", pid, "baseline", "Base — Data Standards (XML)",
            "Baseline inicial de estándares del XML migrado.",
            {"added": [], "edited": [], "removed": []}, {"tables": 0, "columns": 0})
        print(f"  Baseline de Data Standards creado: {v.get('label')} (seq {v.get('seq')}).")
    else:
        print(f"  Data Standards ya tiene {n_std} versión(es) — no se crea baseline.")


async def main(apply: bool, project: str | None) -> None:
    from app.core.db.client import connect, disconnect, get_db
    from app.features.projects import repository as projects_repo

    await connect()
    db = await get_db()
    try:
        projects = await projects_repo.list_projects()
        if project:
            projects = [p for p in projects if (p.get("name") or "").strip().lower() == project.strip().lower()]
            if not projects:
                print(f"ERROR: no existe el proyecto «{project}». Aborto.")
                return
        print(f"Proyectos a resetear: {len(projects)} → " + ", ".join(f"«{p['name']}»" for p in projects))
        for p in projects:
            await reset_project(db, p, apply)

        if not apply:
            print("\n── DRY-RUN — nada escrito. Con --apply también se asegura el permiso `rollback` en los roles.")
            return
        # 6) Asegurar el permiso `rollback` en los roles (sin pisar otros permisos).
        #    Política doc 27: administrador y revisor pueden revertir versiones.
        for role_id, granted in (("administrador", True), ("revisor", True),
                                 ("modelador", False), ("lector", False)):
            await db["roles"].update_one(
                {"_id": role_id}, {"$set": {"permissions.rollback": granted}})
        print("Permiso `rollback` asegurado en los roles (administrador/revisor = true).")
        print("\nOK · UNA versión base por proyecto (v1) + baseline de Data Standards. Preservado todo el base.")
    finally:
        await disconnect()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Reset quirúrgico a una sola versión base (por proyecto)")
    ap.add_argument("--apply", action="store_true", help="escribir de verdad (sin esto: dry-run)")
    ap.add_argument("--project", help="nombre del proyecto (sin él: todos los proyectos activos)")
    _a = ap.parse_args()
    asyncio.run(main(_a.apply, _a.project))
