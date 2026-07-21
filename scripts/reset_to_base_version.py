"""Vuelve la plataforma a la VERSIÓN BASE: solo v1 (Model) + baseline (Standards).

Deshace TODAS las versiones posteriores a la base directo en producción usando
sus imágenes previas (restaura las entidades modificadas, borra las creadas),
conserva la versión BASE — el changeset `versionLabel="v1"` SIN cambios que
marca el estado recién migrado del XML — y todos sus datos (la migración y las
correcciones aplicadas a publicado NO se tocan: acá no se re-migra nada).
Después borra los registros de las demás versiones (aplicadas, drafts,
submitted y rejected: el historial de la web queda solo con v1), crea el
baseline de Data Standards si no existe ninguno y asegura el permiso
`rollback` en los roles.

Resultado: la web muestra UNA versión por módulo (Model v1 · Standards v1) y
cualquier versión futura puede volver a la base con el rollback normal de la
plataforma (doc 27).

Prerrequisito: que exista el changeset base `versionLabel="v1"` (lo dejó la
migración real; si no existe, el script aborta sin tocar nada).

DESTRUCTIVO. **Dry-run por default**; `--apply` para escribir.
  .venv/bin/python -m scripts.reset_to_base_version            # dry-run
  .venv/bin/python -m scripts.reset_to_base_version --apply
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


async def main(apply: bool) -> None:
    from app.core.db.client import connect, disconnect, get_db
    from app.features.changesets.service import rollback_plan
    from app.features.data_standards import service as std_service

    await connect()
    db = await get_db()

    # 1) BASE (v1) vs versiones de PRUEBA (el resto).
    css = await db["changesets"].find({}, {"versionLabel": 1, "title": 1, "status": 1, "appliedAt": 1}).to_list(None)
    base = next((c for c in css if c.get("versionLabel") == "v1"), None)
    if not base:
        print("ERROR: no encuentro la versión BASE (versionLabel='v1'). Aborto.")
        await disconnect(); return
    base_id = str(base["_id"])
    tests = [c for c in css if str(c["_id"]) != base_id]
    applied = [c for c in tests if c.get("appliedAt")]
    # appliedAt DESC (más reciente primero) → al componer, el inverso más VIEJO
    # (más cercano a la base) queda de último y GANA por entidad.
    applied.sort(key=lambda c: c.get("appliedAt") or "", reverse=True)

    def _lbl(c):
        return c.get("versionLabel") or (str(c.get("title") or c["_id"])[:18])

    print(f"BASE conservada:  {_lbl(base)}  ({base_id[:8]}…)")
    print(f"Versiones de PRUEBA a deshacer ({len(applied)} aplicadas / {len(tests)} totales):")
    for c in applied:
        print(f"   - {_lbl(c)}  [{c.get('status')}]")

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
        print(f"\nABORT: {len(missing)} cambios SIN imagen previa (no reconstruible): {missing[:6]}")
        await disconnect(); return

    dels = [(c, e) for (c, e), ic in inverse.items() if ic["op"] == "delete"]
    ups = [(c, e, ic["payload"]) for (c, e), ic in inverse.items() if ic["op"] == "upsert"]
    print(f"\nInverso compuesto: {len(inverse)} entidades")
    print("  RESTAURAR (modificadas/borradas en prueba):", dict(Counter(c for c, _, _ in ups)))
    print("  BORRAR (creadas en prueba):                ", dict(Counter(c for c, _ in dels)))
    struct = dict(Counter(c for c, _ in dels if c in STRUCT))
    if struct:
        print("     · estructura basura a borrar:            ", struct)

    if not apply:
        print("\n── DRY-RUN — nada escrito. Con --apply se aplica y además:")
        print(f"   · se borran los registros de {len(tests)} versión(es) de prueba (se conserva v1);")
        print("   · se crea un baseline de Data Standards (si no hay ninguno);")
        print("   · se asegura el permiso `rollback` en los roles.")
        await disconnect(); return

    # 3) APLICAR el inverso en producción.
    for coll, eid, payload in ups:
        doc = {k: v for k, v in (payload or {}).items() if k != "id"}
        doc["_id"] = eid
        doc["flgactive"] = True
        await db[coll].replace_one({"_id": eid}, doc, upsert=True)
    for coll, eid in dels:
        await db[coll].delete_one({"_id": eid})
    print(f"Aplicado: {len(ups)} restauradas, {len(dels)} borradas.")

    # 4) Borrar los registros de las versiones de prueba (todas menos la base).
    test_ids = [str(c["_id"]) for c in tests]
    r1 = await db["changeset_changes"].delete_many({"csId": {"$in": test_ids}})
    r2 = await db["changesets"].delete_many({"_id": {"$in": test_ids}})
    print(f"Versiones de prueba borradas: {r2.deleted_count} changesets + {r1.deleted_count} cambios. Queda v1.")

    # 5) Baseline de Data Standards (si no hay ninguno).
    n_std = await db["standards_versions"].count_documents({})
    if n_std == 0:
        v = await std_service._record(
            "system", "baseline", "Base — Data Standards (XML DDV)",
            "Baseline inicial de estándares del XML migrado.",
            {"added": [], "edited": [], "removed": []}, {"tables": 0, "columns": 0})
        print(f"Baseline de Data Standards creado: {v.get('label')} (seq {v.get('seq')}).")
    else:
        print(f"Data Standards ya tiene {n_std} versión(es) — no se crea baseline.")

    # 6) Asegurar el permiso `rollback` en los roles (sin pisar otros permisos).
    #    Política doc 27: administrador y revisor pueden revertir versiones.
    for role_id, granted in (("administrador", True), ("revisor", True),
                             ("modelador", False), ("lector", False)):
        await db["roles"].update_one(
            {"_id": role_id}, {"$set": {"permissions.rollback": granted}})
    print("Permiso `rollback` asegurado en los roles (administrador/revisor = true).")

    print("\nOK · UNA versión base para Model (v1) + baseline de Data Standards. Preservado todo el base.")
    await disconnect()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Reset quirúrgico a una sola versión base")
    ap.add_argument("--apply", action="store_true", help="escribir de verdad (sin esto: dry-run)")
    asyncio.run(main(ap.parse_args().apply))
