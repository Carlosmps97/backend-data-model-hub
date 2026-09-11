"""Siembra el RULESET BASE de DDL Export (doc 76: los 7 DDL de la macro BCP)
como UNA versión de Data Standards — POR PROYECTO (doc 75: cada proyecto tiene
sus reglas, lookups, UDPs y su historial). `--project "Nombre"` siembra uno;
`--all-projects`, todos (el one-shot usa este último).

Qué crea (10 reglas + 5 generadores + 4 lookups — `templates.py` es la fuente):
  - excluir_dac_vista_sin_dac  vista NoDAC de tabla DAC → quita las columnas DAC-*
  - desencriptar_dac           columna DAC-% → bcp_encrypt_function.decrypt_column_view({col}, '<sufijo>')
                               [vistas DAC + vista de negocio]
  - tags_dac_columna           ALTER … ALTER COLUMN … SET TAGS ('DAC' = '<sufijo>')
  - char_a_varchar             columna CHAR(n) → VARCHAR(n) en la física y la _rej (acción `types`, doc 90)
  - drop_comentado             -- DROP TABLE IF EXISTS … comentado antes del CREATE (física y _rej)
  - tags_update_frequency      SET TAGS ('updateFrecuency' = …) [lookup update_frequency_map]
  - tags_isdac / tags_isdac_sin_dac   SET TAGS ('isDAC' = 'True'|'False')
  - tblproperties_vacuum       delta.logRetentionDuration + deletedFileRetentionDuration [lookup vacuum_map]
  - particiones_al_final       particiones al final del CREATE, ordenadas por el UDP «Particion»
  - tabla_rechazos             generador: _rej todo STRING (particiones conservan tipo) + tiporeject
  - vista_tecnica / vista_tecnica_dac / vista_rechazos / vista_rechazos_dac   generadores de vistas _v
  Lookups: vacuum_map · update_frequency_map · dac_flag_map · dac_map.

Robustez en BD recién migrada: el kit siembra el catálogo fijo completo de UDPs
(doc 68), pero si «Frecuencia Vacuum» faltara (cargas viejas con la política
A4 de defs sin uso) este seed la AUTO-CREA en el MISMO batch (`udpUpsert` — la
validación cuenta los UDP del propio batch). Cualquier OTRO UDP que las reglas
o los lookups necesiten y falte (p.ej. «Clasificacion del Dato», «Particion»)
significa que el modelo no está migrado: se aborta LIMPIO con mensaje
accionable, sin traceback.

Pasa por `data_standards.service.apply` (la MISMA ruta que la web): valida cada
regla contra el catálogo real de UDPs del proyecto, deriva los bindings por id
y registra la versión "Base — DDL export rules" en el historial del proyecto
(rollback disponible). NO re-siembra: si el proyecto ya tiene reglas activas,
lo salta (bórralas o edítalas en la web → «Restore the base rule set»).

Dry-run por default; `--apply` para escribir.
  .venv/bin/python -m scripts.seed_ddl_export_rules --all-projects            # dry-run
  .venv/bin/python -m scripts.seed_ddl_export_rules --all-projects --apply
  .venv/bin/python -m scripts.seed_ddl_export_rules --project "Modelo DDV" --apply
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Defs UDP que el ruleset base necesita y que una carga vieja del kit podría
# OMITIR por tener usedBy=0 en el DDV real (A4). Catálogo canónico:
#   - "Frecuencia Vacuum": allowedValues = las claves del lookup vacuum_map
#     (se derivan en runtime — una sola fuente de verdad); default de la DEF
#     de Erwin = CUSTOM_90 days (doc 30b).
_AUTO_UDPS: list[dict] = [
    {"name": "Frecuencia Vacuum", "level": "table", "view": "physical", "dataType": "list",
     "allowedValues": [],   # ← se llena con las claves de vacuum_map en runtime
     "defaultValue": "CUSTOM_90 days",
     "description": "Retención de vacuum y frecuencia de actualización (lookups vacuum_map / update_frequency_map)."},
]


def physical_udp_keys(defs: list[dict]) -> dict[tuple[str, str], dict]:
    """(level, name) → def SOLO de la faceta FÍSICA (doc 69). Las reglas DDL
    enlazan UDP físicos (`validate_rule` ignora los lógicos), así que un
    homónimo LÓGICO no cuenta como presente (doc 73 §11.3). Puro."""
    from app.core.facets import normalize_udp_view
    return {(d.get("level"), d.get("name")): d for d in defs
            if normalize_udp_view(d.get("level"), d.get("view")) == "physical"}

# Referencias a UDP dentro de las semillas: `columna.udp["X"]` / `tabla.udp["X"]`
# en condiciones y `{udp:X}` en acciones (el nivel de estas últimas es el
# target de la regla; los generadores operan sobre tabla).
_COND_REF = re.compile(r'(columna|tabla)\.udp\["([^"]+)"\]')
_ACTION_REF = re.compile(r"\{udp:([^}]+)\}")
_LEVEL = {"columna": "column", "tabla": "table"}


def select_projects(projects: list[dict], project: str | None, all_projects: bool) -> list[dict]:
    """Proyectos destino: uno por nombre (case-insensitive) o todos. Puro;
    sin destino o nombre desconocido → SystemExit con mensaje accionable."""
    if all_projects:
        return list(projects)
    if not project:
        raise SystemExit("Indica --project \"Nombre\" o --all-projects.")
    hits = [p for p in projects if (p.get("name") or "").strip().lower() == project.strip().lower()]
    if not hits:
        names = ", ".join(f"«{p.get('name')}»" for p in projects) or "(ninguno)"
        raise SystemExit(f"No existe el proyecto «{project}». Proyectos activos: {names}.")
    return hits


def _referenced_udps(seeds: list[dict], seed_lookups: dict | None = None) -> set[tuple[str, str]]:
    """(level, name) de todo UDP que las semillas usan: condiciones, `{udp:…}`
    de las acciones, `layout.partitionOrderUdp` (UDP de columna, doc 76) y el
    UDP de ORIGEN de cada lookup (`fromUdpName`/`fromLevel`). Puro."""
    refs: set[tuple[str, str]] = set()
    for s in seeds:
        for prefix, name in _COND_REF.findall(s.get("condition") or ""):
            refs.add((_LEVEL[prefix], name))
        level = s.get("target") or "table"
        action = s.get("action") or {}
        for name in _ACTION_REF.findall(str(action)):
            refs.add((level, name))
        order_udp = str((action.get("layout") or {}).get("partitionOrderUdp") or "").strip()
        if order_udp:
            refs.add(("column", order_udp))
    for lk in (seed_lookups or {}).values():
        if lk.get("fromUdpName"):
            refs.add((lk.get("fromLevel") or "table", lk["fromUdpName"]))
    return refs


async def seed_one(project_id: str, project_name: str, apply: bool) -> None:
    """Siembra el ruleset base en UN proyecto (o lo salta si ya tiene reglas)."""
    from fastapi import HTTPException

    from app.features.data_standards import service as std_service
    from app.features.data_standards.schemas import ApplyBody, DdlConfigPatch, DdlRuleEdit, UdpEdit
    from app.features.ddl_rules import repository as rules_repo
    from app.features.ddl_rules import service as rules_svc
    from app.features.ddl_rules.templates import SEED_LOOKUPS
    from app.features.udp import repository as udp_repo

    print(f"\n{'═' * 72}\nPROYECTO «{project_name}» ({project_id[:8]}…)")
    existing = await rules_repo.list_rules(project_id)
    if existing:
        print(f"SALTADO: ya hay {len(existing)} regla(s) activas — no re-siembro.")
        for r in existing:
            print(f"   - {r['name']} [{r['kind']}]")
        return

    payload = await rules_svc.templates_payload(project_id)   # lookups con udpId REAL del proyecto
    seeds = payload["seedRules"]
    lookups = payload["seedLookups"]

    defs = await udp_repo.list_udp(project_id)
    by_level_name = physical_udp_keys(defs)   # doc 73 §11.3: sólo la faceta física cuenta

    # ── UDP faltantes: auto-creables vs requeridos ────────────────────
    auto_by_key = {(a["level"], a["name"]): a for a in _AUTO_UDPS}
    udp_upsert: list[UdpEdit] = []
    for key, spec in auto_by_key.items():
        if key in by_level_name:
            continue
        spec = dict(spec)
        if spec["name"] == "Frecuencia Vacuum":
            spec["allowedValues"] = list(
                (SEED_LOOKUPS["vacuum_map"].get("values") or {}).keys())
        spec["id"] = str(uuid.uuid4())
        udp_upsert.append(UdpEdit(**spec))

    # Enlazar TODOS los lookups que nacen de un UDP recién creado (mismo batch).
    for u in udp_upsert:
        for lk_name, lk in lookups.items():
            seed_lk = SEED_LOOKUPS.get(lk_name) or {}
            if (seed_lk.get("fromUdpName"), seed_lk.get("fromLevel")) == (u.name, u.level) and not lk.get("fromUdpId"):
                lk["fromUdpId"] = u.id

    missing_required = sorted(
        f"{name} [{level}]"
        for (level, name) in _referenced_udps(seeds, SEED_LOOKUPS)
        if (level, name) not in by_level_name and (level, name) not in auto_by_key
    )
    if missing_required:
        print("ERROR: faltan UDP que el ruleset necesita y que deberían venir "
              "del modelo migrado (no los invento):")
        for m in missing_required:
            print(f"   - {m}")
        print("Migra el/los XML del proyecto primero (o crea esas defs en la web) y re-corre.")
        raise SystemExit(1)

    # ── Plan ──────────────────────────────────────────────────────────
    print(f"UDP defs del proyecto: {len(defs)} · reglas a crear: {len(seeds)} · lookups: {list(lookups)}")
    for u in udp_upsert:
        print(f"   UDP a crear en el batch: {u.name} [{u.level}] · "
              f"{len(u.allowedValues)} valores · default={u.defaultValue or '—'}")
    for lk_name, lk in lookups.items():
        state = "OK" if lk.get("fromUdpId") else "SIN UDP (¡revisar catálogo!)"
        print(f"   lookup {lk_name}: fromUdpId={lk.get('fromUdpId')} · "
              f"{len(lk.get('values') or {})} valores · default={lk.get('default')!r} · {state}")
    for s in seeds:
        print(f"   regla {s['name']} [{s['kind']}]")

    if not apply:
        print("── DRY-RUN — nada escrito. Con --apply se crea la versión "
              "'Base — DDL export rules' en Data Standards del proyecto"
              + (f" (incluye {len(udp_upsert)} def(s) UDP nuevas)" if udp_upsert else "")
              + ".")
        return

    body = ApplyBody(
        kind="ddl",
        title="Base — DDL export rules",
        description=("Ruleset base del DDL Export (doc 76, macro BCP): tabla física con "
                     "DROP comentado/TBLPROPERTIES/tags, tabla de rechazos, vistas técnicas "
                     "NoDAC/DAC, vistas de rechazos y decoración de las vistas de negocio."),
        udpUpsert=udp_upsert,
        rulesUpsert=[DdlRuleEdit(**s) for s in seeds],
        ddlConfigPatch=DdlConfigPatch(lookups=lookups),
    )
    try:
        version = await std_service.apply("system", project_id, body)
    except HTTPException as exc:            # validación server-side: limpio, sin traceback
        print(f"\nERROR de validación del apply: {exc.detail}")
        raise SystemExit(1) from exc
    print(f"\nOK · versión {version['label']} '{version['title']}' registrada en «{project_name}».")
    if udp_upsert:
        print(f"   + {len(udp_upsert)} def(s) UDP creadas en el mismo batch: "
              + ", ".join(u.name for u in udp_upsert))
    rules = await rules_repo.list_rules(project_id)
    print(f"Reglas activas ahora: {len(rules)}")
    for r in rules:
        print(f"   - {r['name']} [{r['kind']}] · prio {r['priority']} · {r['validationState']}")


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
    ap = argparse.ArgumentParser(description="Siembra el ruleset base de DDL Export Rules (por proyecto)")
    ap.add_argument("--apply", action="store_true", help="escribir de verdad (sin esto: dry-run)")
    scope = ap.add_mutually_exclusive_group(required=True)
    scope.add_argument("--project", help="nombre del proyecto destino")
    scope.add_argument("--all-projects", action="store_true", help="todos los proyectos activos")
    _a = ap.parse_args()
    asyncio.run(main(_a.apply, _a.project, _a.all_projects))
