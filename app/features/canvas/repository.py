"""CRUD async del canvas de un proyecto: `project_tables` + `project_relationships`.

Arquitectura project-centric. Cada proyecto tiene un único canvas scoped; tablas y
relaciones se shardean por `projectId`.

Decisiones de diseño clave (INVARIANTE — no cambiar el contrato de persistencia):
- `_replace_entities` usa soft-delete + bulkWrite upsert para evitar E11000 sobre
  docs previamente soft-deleted.
- Las vistas NO son colección aparte — van embebidas en `views[]` de cada tabla.
- `position` se parchea vía `bulk_update_positions` (solo las tablas son nodos).
- Se evita `.sort()` — Cosmos DB RU lo rechaza en campos sin índice.
- Lectura: re-valida con Pydantic `extra="ignore"` → un campo no declarado en el
  modelo se descarta silenciosamente al releer (round-trip invariant).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from pymongo.operations import ReplaceOne

from app.core.db.client import get_db

from .models import RelationshipDoc, TableDoc

TABLES = "project_tables"
RELATIONSHIPS = "project_relationships"


# ── Helpers ────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_child(doc: dict) -> dict:
    """Quita internos de Mongo + `projectId` de un doc hijo."""
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    doc.pop("projectId", None)
    doc.pop("flgactive", None)
    doc.pop("deletedAt", None)
    doc.pop("updatedAt", None)
    return doc


def _ensure_column_ids(tables: list[dict]) -> list[dict]:
    """Backfill de un UUID en cualquier columna que llegue a persistencia sin uno.
    Idempotente — mantiene válidos los edges de relaciones (que referencian
    `TableColumn.id`) aunque un cliente olvide generar ids."""
    for table in tables or []:
        for col in table.get("columns") or []:
            cid = col.get("id")
            if not isinstance(cid, str) or not cid:
                col["id"] = str(uuid.uuid4())
    return tables


# ── replaceEntities (bulk upsert) ──────────────────────────────────────────

async def _replace_entities(
    col_name: str,
    project_id: str,
    entities: list[dict],
) -> None:
    """Reemplaza el set completo de entidades hijas de un proyecto.

      1. Soft-delete de docs cuyo `_id` NO está en el payload entrante.
      2. `bulkWrite` con `replaceOne(upsert=True)` por cada entidad entrante.
    """
    db = await get_db()
    col = db[col_name]
    now = _now()
    incoming_ids = [e["id"] for e in entities]

    await col.update_many(
        {"projectId": project_id, "_id": {"$nin": incoming_ids}},
        {"$set": {"flgactive": False, "deletedAt": now}},
    )

    if not entities:
        return

    ops = []
    for entity in entities:
        eid = entity["id"]
        rest = {k: v for k, v in entity.items() if k not in ("id", "projectId")}
        ops.append(
            ReplaceOne(
                {"_id": eid},
                {
                    "_id": eid,
                    "projectId": project_id,
                    "flgactive": True,
                    "updatedAt": now,
                    **rest,
                },
                upsert=True,
            )
        )
    await col.bulk_write(ops)


# ── CRUD ───────────────────────────────────────────────────────────────────

async def get_canvas(project_id: str) -> dict[str, list[dict]]:
    """Trae el canvas hidratado de un proyecto: `{tables, relationships}`."""
    db = await get_db()
    table_docs, rel_docs = await asyncio.gather(
        db[TABLES]
        .find({"projectId": project_id, "flgactive": {"$ne": False}})
        .to_list(None),
        db[RELATIONSHIPS]
        .find({"projectId": project_id, "flgactive": {"$ne": False}})
        .to_list(None),
    )
    tables = [
        TableDoc.model_validate(_to_child(d)).model_dump(by_alias=True)
        for d in table_docs
    ]
    relationships = [
        RelationshipDoc.model_validate(_to_child(d)).model_dump(by_alias=True)
        for d in rel_docs
    ]
    return {"tables": tables, "relationships": relationships}


async def replace_canvas(
    project_id: str,
    tables: list[dict[str, Any]] | None = None,
    relationships: list[dict[str, Any]] | None = None,
) -> dict[str, list[dict]] | None:
    """Reemplaza por completo las tablas y/o relaciones del proyecto.

    Devuelve el canvas recién hidratado, o None si el proyecto no existe.
    Los arrays hijos ausentes de la llamada quedan intactos.
    """
    db = await get_db()
    exists = await db["projects"].find_one({"_id": project_id}, {"_id": 1})
    if not exists:
        return None

    ops = []
    if tables is not None:
        _ensure_column_ids(tables)
        ops.append(_replace_entities(TABLES, project_id, tables))
    if relationships is not None:
        ops.append(_replace_entities(RELATIONSHIPS, project_id, relationships))
    if ops:
        await asyncio.gather(*ops)

    await db["projects"].update_one(
        {"_id": project_id}, {"$set": {"updatedAt": _now()}}
    )
    return await get_canvas(project_id)


async def bulk_update_positions(
    project_id: str,
    table_positions: dict[str, dict[str, float]] | None = None,
) -> dict[str, int]:
    """Parchea solo el campo `position` en las tablas dadas.

    Usado por `PATCH /api/projects/{id}/positions` para persistir rápido el
    layout y los drags sin reescribir todo el canvas.
    """
    db = await get_db()
    now = _now()
    tables_matched = 0

    if table_positions:
        tasks = []
        for tid, pos in table_positions.items():
            if not isinstance(pos, dict):
                continue
            x = pos.get("x")
            y = pos.get("y")
            if not (isinstance(x, (int, float)) and isinstance(y, (int, float))):
                continue
            tasks.append(
                db[TABLES].update_one(
                    {"_id": tid, "projectId": project_id, "flgactive": {"$ne": False}},
                    {"$set": {"position": {"x": float(x), "y": float(y)}, "updatedAt": now}},
                )
            )
        if tasks:
            results = await asyncio.gather(*tasks)
            tables_matched = sum(r.matched_count for r in results)

    if tables_matched:
        await db["projects"].update_one({"_id": project_id}, {"$set": {"updatedAt": now}})

    return {"tables": tables_matched}


async def delete_canvas(project_id: str) -> None:
    """Soft-delete de toda tabla y relación de un proyecto (cascada del borrado
    de proyecto)."""
    db = await get_db()
    now = _now()
    await asyncio.gather(
        db[TABLES].update_many(
            {"projectId": project_id},
            {"$set": {"flgactive": False, "deletedAt": now}},
        ),
        db[RELATIONSHIPS].update_many(
            {"projectId": project_id},
            {"$set": {"flgactive": False, "deletedAt": now}},
        ),
    )
