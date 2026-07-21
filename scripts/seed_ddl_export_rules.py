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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


async def main(apply: bool) -> None:
    from app.core.db.client import connect, disconnect
    from app.features.data_standards import service as std_service
    from app.features.data_standards.schemas import ApplyBody, DdlConfigPatch, DdlRuleEdit
    from app.features.ddl_rules import repository as rules_repo
    from app.features.ddl_rules import service as rules_svc
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
        by_name_level = {(d["name"], d["level"]) for d in defs}
        print(f"UDP defs en BD: {len(defs)} · reglas a crear: {len(seeds)} · lookups: {list(lookups)}")
        for lk_name, lk in lookups.items():
            state = "OK" if lk.get("fromUdpId") else "SIN UDP (¡revisar catálogo!)"
            print(f"   lookup {lk_name}: fromUdpId={lk.get('fromUdpId')} · {len(lk.get('values') or {})} valores · {state}")
        for s in seeds:
            need = "Clasificacion del Dato" in s["condition"] and \
                ("Clasificacion del Dato", "column" if s.get("target") == "column" else "table") not in by_name_level
            print(f"   regla {s['name']} [{s['kind']}]"
                  + (" · ⚠ UDP de la condición no existe" if need else ""))

        if not apply:
            print("\n── DRY-RUN — nada escrito. Con --apply se crea la versión "
                  "'Base — DDL export rules' en Data Standards.")
            return

        body = ApplyBody(
            kind="ddl",
            title="Base — DDL export rules",
            description=("Ruleset base del DDL Export (doc 30): masking técnico, "
                         "desencriptación de negocio, tags, TBLPROPERTIES y la "
                         "cascada de rechazos/vista técnica."),
            rulesUpsert=[DdlRuleEdit(**s) for s in seeds],
            ddlConfigPatch=DdlConfigPatch(lookups=lookups),
        )
        version = await std_service.apply("system", body)
        print(f"\nOK · versión {version['label']} '{version['title']}' registrada.")
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
