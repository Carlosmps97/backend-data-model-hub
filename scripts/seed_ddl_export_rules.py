"""Siembra las REGLAS BASE de DDL Export (doc 30 §8 + pedido owner 07-20) como
UNA versión de Data Standards: la versión base del ruleset.

Qué crea (8 reglas + 2 lookups):
  - enmascarar_dac            columna DAC-% → sha2({col}, 512)      [vista técnica]
  - desencriptar_dac_negocio  columna DAC-% → bcp_ddv_desencrypt({col}, '<sufijo>') [vista de negocio]
                              (el sufijo = lo que va después de 'DAC-', vía lookup dac_map)
  - tags_clasificacion        tag de gobierno por columna           [tabla física]
  - tags_tabla                tags dominio/estado/tipo de entidad   [tabla física]
  - tblproperties_vacuum      retención delta según Frecuencia Vacuum (lookup vacuum_map)
  - tabla_rechazos            generador: tabla _rej todo STRING sin constraints
  - vista_rechazos            generador: vista _rej en el esquema espejo _v
  - vista_tecnica             generador: vista técnica _v para tablas Regular
  Lookups: vacuum_map (9 valores) + dac_map (11 valores DAC-XXXX → XXXX).

Robustez en BD recién migrada (2026-07-30): el kit multi-archivo NO crea defs
UDP sin uso (política A4, doc 32b) y en el DDV real "Tipo de Vista" y
"Frecuencia Vacuum" tienen usedBy=0 → no existen tras una carga fresca, y las
reglas/lookups que las referencian fallarían la validación. Este seed las
AUTO-CREA en el MISMO batch (`udpUpsert` — la validación cuenta los UDP del
propio batch) con su catálogo canónico. Cualquier OTRO UDP referenciado que
falte (p.ej. "Clasificacion del Dato") significa que el modelo no está
migrado: se aborta LIMPIO con mensaje accionable, sin traceback.

Pasa por `data_standards.service.apply` (la MISMA ruta que la web): valida cada
regla contra el catálogo real de UDPs, deriva los bindings por id y registra la
versión "Base — DDL export rules" en el historial (rollback disponible). NO
re-siembra: si ya hay reglas activas, aborta (bórralas o edítalas en la web).

Dry-run por default; `--apply` para escribir.
  .venv/bin/python -m scripts.seed_ddl_export_rules            # dry-run
  .venv/bin/python -m scripts.seed_ddl_export_rules --apply
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

# Defs UDP que el ruleset base necesita pero que una carga fresca del kit
# OMITE por tener usedBy=0 en el DDV real (A4). Catálogo canónico:
#   - "Tipo de Vista": la regla vista_tecnica trata NULL como 'Regular'
#     (default del propio Erwin); 'Personalizada' queda fuera del generador.
#   - "Frecuencia Vacuum": allowedValues = las claves del lookup vacuum_map
#     (se derivan en runtime — una sola fuente de verdad); default de la DEF
#     de Erwin = CUSTOM_90 days (doc 30b).
_AUTO_UDPS: list[dict] = [
    {"name": "Tipo de Vista", "level": "table", "dataType": "list",
     "allowedValues": ["Regular", "Personalizada"],
     "description": "Tipo de vista técnica a generar por tabla (Regular por default)."},
    {"name": "Frecuencia Vacuum", "level": "table", "dataType": "list",
     "allowedValues": [],   # ← se llena con las claves de vacuum_map en runtime
     "defaultValue": "CUSTOM_90 days",
     "description": "Retención de vacuum (delta.deletedFileRetentionDuration) vía lookup vacuum_map."},
]

# Referencias a UDP dentro de las semillas: `columna.udp["X"]` / `tabla.udp["X"]`
# en condiciones y `{udp:X}` en acciones (el nivel de estas últimas es el
# target de la regla; los generadores operan sobre tabla).
_COND_REF = re.compile(r'(columna|tabla)\.udp\["([^"]+)"\]')
_ACTION_REF = re.compile(r"\{udp:([^}]+)\}")
_LEVEL = {"columna": "column", "tabla": "table"}


def _referenced_udps(seeds: list[dict]) -> set[tuple[str, str]]:
    """(level, name) de todo UDP que las semillas usan."""
    refs: set[tuple[str, str]] = set()
    for s in seeds:
        for prefix, name in _COND_REF.findall(s.get("condition") or ""):
            refs.add((_LEVEL[prefix], name))
        level = s.get("target") or "table"
        for name in _ACTION_REF.findall(str(s.get("action") or {})):
            refs.add((level, name))
    return refs


async def main(apply: bool) -> None:
    from fastapi import HTTPException

    from app.core.db.client import connect, disconnect
    from app.features.data_standards import service as std_service
    from app.features.data_standards.schemas import ApplyBody, DdlConfigPatch, DdlRuleEdit, UdpEdit
    from app.features.ddl_rules import repository as rules_repo
    from app.features.ddl_rules import service as rules_svc
    from app.features.ddl_rules.templates import SEED_LOOKUPS
    from app.features.udp import repository as udp_repo

    await connect()
    try:
        existing = await rules_repo.list_rules()
        if existing:
            print(f"ABORT: ya hay {len(existing)} regla(s) activas — no re-siembro.")
            for r in existing:
                print(f"   - {r['name']} [{r['kind']}]")
            return

        payload = await rules_svc.templates_payload()   # lookups con udpId REAL de esta BD
        seeds = payload["seedRules"]
        lookups = payload["seedLookups"]

        defs = await udp_repo.list_udp()
        by_level_name = {(d.get("level"), d.get("name")): d for d in defs}

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

        # Enlazar el lookup vacuum_map al UDP recién creado (mismo batch).
        for u in udp_upsert:
            if u.name == "Frecuencia Vacuum" and not lookups.get("vacuum_map", {}).get("fromUdpId"):
                lookups["vacuum_map"]["fromUdpId"] = u.id

        missing_required = sorted(
            f"{name} [{level}]"
            for (level, name) in _referenced_udps(seeds)
            if (level, name) not in by_level_name and (level, name) not in auto_by_key
        )
        if missing_required:
            print("ERROR: faltan UDP que el ruleset necesita y que deberían venir "
                  "del modelo migrado (no los invento):")
            for m in missing_required:
                print(f"   - {m}")
            print("Migra el/los XML primero (o crea esas defs en la web) y re-corre.")
            raise SystemExit(1)

        # ── Plan ──────────────────────────────────────────────────────────
        print(f"UDP defs en BD: {len(defs)} · reglas a crear: {len(seeds)} · lookups: {list(lookups)}")
        for u in udp_upsert:
            print(f"   UDP a crear en el batch: {u.name} [{u.level}] · "
                  f"{len(u.allowedValues)} valores · default={u.defaultValue or '—'}")
        for lk_name, lk in lookups.items():
            state = "OK" if lk.get("fromUdpId") else "SIN UDP (¡revisar catálogo!)"
            print(f"   lookup {lk_name}: fromUdpId={lk.get('fromUdpId')} · "
                  f"{len(lk.get('values') or {})} valores · {state}")
        for s in seeds:
            print(f"   regla {s['name']} [{s['kind']}]")

        if not apply:
            print("\n── DRY-RUN — nada escrito. Con --apply se crea la versión "
                  "'Base — DDL export rules' en Data Standards"
                  + (f" (incluye {len(udp_upsert)} def(s) UDP nuevas)" if udp_upsert else "")
                  + ".")
            return

        body = ApplyBody(
            kind="ddl",
            title="Base — DDL export rules",
            description=("Ruleset base del DDL Export (doc 30): masking técnico, "
                         "desencriptación de negocio, tags, TBLPROPERTIES y la "
                         "cascada de rechazos/vista técnica."),
            udpUpsert=udp_upsert,
            rulesUpsert=[DdlRuleEdit(**s) for s in seeds],
            ddlConfigPatch=DdlConfigPatch(lookups=lookups),
        )
        try:
            version = await std_service.apply("system", body)
        except HTTPException as exc:            # validación server-side: limpio, sin traceback
            print(f"\nERROR de validación del apply: {exc.detail}")
            raise SystemExit(1) from exc
        print(f"\nOK · versión {version['label']} '{version['title']}' registrada.")
        if udp_upsert:
            print(f"   + {len(udp_upsert)} def(s) UDP creadas en el mismo batch: "
                  + ", ".join(u.name for u in udp_upsert))
        rules = await rules_repo.list_rules()
        print(f"Reglas activas ahora: {len(rules)}")
        for r in rules:
            print(f"   - {r['name']} [{r['kind']}] · prio {r['priority']} · {r['validationState']}")
    finally:
        await disconnect()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Siembra el ruleset base de DDL Export Rules")
    ap.add_argument("--apply", action="store_true", help="escribir de verdad (sin esto: dry-run)")
    asyncio.run(main(ap.parse_args().apply))
