"""Seed the UDP / Semantic Type catalog with example data and apply a Semantic
Type to real columns (Feature 3 demo).

Idempotent: semantic types / UDPs use fixed `_id`s and are upserted; the column
assignment only touches money-like **decimal** columns in the `lakehouse`
project's `project_tables`. Never touches `column_catalog` (agent-owned).

Run:
    backend-data-model-hub/.venv/bin/python scripts/seed_udp_catalog.py
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pymongo import MongoClient  # noqa: E402

from src.config import settings  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SEMANTIC_TYPES = [
    {
        "_id": "st-monto", "name": "Monto", "dataType": "decimal",
        "description": "Importe monetario.",
        "tags": [
            {"key": "Cross Attribute", "allowedValues": ["YES", "NO"]},
            {"key": "Data Classification", "allowedValues": ["DAC", "NO DAC"]},
        ],
    },
    {
        "_id": "st-porcentaje", "name": "Porcentaje", "dataType": "decimal",
        "description": "Valor porcentual (0–100).",
        "tags": [{"key": "Cross Attribute", "allowedValues": ["YES", "NO"]}],
    },
]

UDPS = [
    {
        "_id": "udp-sistema-origen", "name": "Sistema origen",
        "description": "Sistema fuente del dato.",
        "appliesTo": ["column", "table"], "valueType": "enum",
        "allowedValues": ["SAP", "Core", "Datalake", "Manual"],
    },
]

TARGET_PROJECT = "lakehouse"
MONEY = re.compile(
    r"(saldo|monto|importe|amount|exposicion|activos|pasivos|balance|precio|costo|valor)",
    re.I,
)


def main() -> None:
    conn = settings.COSMOS_CONNECTION_STRING
    if not conn:
        raise SystemExit("COSMOS_CONNECTION_STRING not set")
    cli = MongoClient(conn, serverSelectionTimeoutMS=20000)
    db = cli[settings.COSMOS_DATABASE]

    # 1) Upsert the catalog (fixed ids → idempotent).
    for st in SEMANTIC_TYPES:
        body = {k: v for k, v in st.items() if k != "_id"}
        db["semantic_types"].update_one(
            {"_id": st["_id"]},
            {"$set": {**body, "flgactive": True, "updatedAt": _now()},
             "$setOnInsert": {"createdAt": _now()}},
            upsert=True,
        )
    for u in UDPS:
        body = {k: v for k, v in u.items() if k != "_id"}
        db["udps"].update_one(
            {"_id": u["_id"]},
            {"$set": {**body, "flgactive": True, "updatedAt": _now()},
             "$setOnInsert": {"createdAt": _now()}},
            upsert=True,
        )
    print(f"upserted {len(SEMANTIC_TYPES)} semantic types, {len(UDPS)} udps")

    # 2) Apply "Monto" to decimal money columns in the target project. Mirrors
    #    the UI: assigning the type inherits its data type + sets controlled tags.
    applied = 0
    tables_touched = 0
    for t in db["project_tables"].find({"projectId": TARGET_PROJECT, "flgactive": {"$ne": False}}):
        cols = t.get("columns") or []
        changed = False
        for idx, c in enumerate(cols):
            dt = (c.get("dataType") or "").lower()
            if dt.startswith("decimal") and MONEY.search(c.get("name", "")):
                c["semanticTypeId"] = "st-monto"
                c["dataType"] = "decimal"
                c["semanticValues"] = {
                    "Cross Attribute": "NO",
                    "Data Classification": "DAC" if idx % 2 == 0 else "NO DAC",
                }
                changed = True
                applied += 1
        if changed:
            db["project_tables"].update_one(
                {"_id": t["_id"]}, {"$set": {"columns": cols, "updatedAt": _now()}}
            )
            tables_touched += 1
    print(f"applied 'Monto' to {applied} columns across {tables_touched} tables in '{TARGET_PROJECT}'")
    cli.close()


if __name__ == "__main__":
    main()
