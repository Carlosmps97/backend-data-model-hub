"""Seed greenfield del modelo canónico (M1). Borra la data mock LEGACY y la
del modelo canónico, y siembra dominios + diccionario + tablas/columnas nuevas.

Run:  backend-data-model-hub/.venv/bin/python scripts/seed_canonical.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pymongo import MongoClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.naming import physicalize  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


LEGACY = ["projects", "project_tables", "project_relationships", "semantic_types", "udps"]
NEW = ["parent_domains", "abbreviation_dict", "canonical_tables", "canonical_columns"]

DOMAINS = [
    {"_id": "pd-monto", "name": "monto", "defaultDataType": "decimal(24,4)"},
    {"_id": "pd-fecha", "name": "fecha", "defaultDataType": "date"},
    {"_id": "pd-codigo", "name": "código", "defaultDataType": "varchar(20)"},
    {"_id": "pd-porcentaje", "name": "porcentaje", "defaultDataType": "decimal(9,6)"},
    {"_id": "pd-descripcion", "name": "descripción", "defaultDataType": "varchar(255)"},
    {"_id": "pd-identificador", "name": "identificador", "defaultDataType": "bigint"},
]
DICT = [
    ("monto", "MTO"), ("deuda", "DEU"), ("dólares", "USD"), ("soles", "PEN"),
    ("fecha", "FEC"), ("código", "COD"), ("cliente", "CLI"), ("saldo", "SLD"),
    ("vencimiento", "VTO"), ("porcentaje", "PCT"), ("descripción", "DSC"),
    ("identificador", "ID"), ("producto", "PRD"), ("tipo de cambio", "TPC"),
]
# Tablas canónicas: (logicalName, schema, [(colLogical, parentDomainId, pk)])
TABLES = [
    ("cliente", "core", [
        ("identificador cliente", "pd-identificador", True),
        ("descripción cliente", "pd-descripcion", False),
        ("fecha alta", "pd-fecha", False),
    ]),
    ("deuda cliente", "riesgos", [
        ("identificador cliente", "pd-identificador", True),
        ("monto deuda dólares", "pd-monto", False),
        ("monto deuda soles", "pd-monto", False),
        ("fecha vencimiento", "pd-fecha", False),
        ("porcentaje", "pd-porcentaje", False),
    ]),
]


def main() -> None:
    conn = settings.COSMOS_CONNECTION_STRING
    if not conn:
        raise SystemExit("COSMOS_CONNECTION_STRING not set")
    cli = MongoClient(conn, serverSelectionTimeoutMS=20000)
    db = cli[settings.COSMOS_DATABASE]

    # 1) Wipe legacy mock + canónico previo (idempotencia del seed).
    for c in LEGACY + NEW:
        db[c].delete_many({})
    print(f"wiped {len(LEGACY)} legacy + {len(NEW)} canonical collections")

    # 2) Dominios + diccionario.
    for d in DOMAINS:
        db["parent_domains"].insert_one({**d, "flgactive": True, "createdAt": _now(), "updatedAt": _now()})
    mappings = {}
    for term, abbrev in DICT:
        db["abbreviation_dict"].insert_one(
            {"_id": f"ad-{abbrev.lower()}", "term": term, "abbrev": abbrev,
             "flgactive": True, "createdAt": _now(), "updatedAt": _now()}
        )
        mappings[term] = abbrev
    print(f"seeded {len(DOMAINS)} domains, {len(DICT)} dictionary entries")

    # 3) Tablas + columnas canónicas (físico derivado del diccionario, tipo del dominio).
    n_cols = 0
    for tlog, schema, cols in TABLES:
        tid = f"ct-{physicalize(tlog, mappings).lower()}"
        db["canonical_tables"].insert_one({
            "_id": tid, "logicalName": tlog, "physicalName": physicalize(tlog, mappings),
            "schema": schema, "flgactive": True, "createdAt": _now(), "updatedAt": _now(),
        })
        for i, (clog, pdid, pk) in enumerate(cols):
            default = next((d["defaultDataType"] for d in DOMAINS if d["_id"] == pdid), "")
            db["canonical_columns"].insert_one({
                "_id": f"{tid}-c{i}",
                "tableId": tid, "logicalName": clog, "physicalName": physicalize(clog, mappings),
                "parentDomainId": pdid, "dataType": default, "typeOverridden": False,
                "isPrimaryKey": pk, "ordinal": i, "flgactive": True,
                "createdAt": _now(), "updatedAt": _now(),
            })
            n_cols += 1
    print(f"seeded {len(TABLES)} canonical tables, {n_cols} columns")
    cli.close()


if __name__ == "__main__":
    main()
