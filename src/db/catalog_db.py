"""Acceso a la colección `column_catalog` en Azure Cosmos DB for MongoDB.

El catálogo de columnas estandarizadas del agente se almacena en Cosmos DB
(misma cuenta que el frontend) en la colección `column_catalog` dentro de
la base de datos `db_modeler`.

Cada documento tiene la estructura:
    {
        "_id":                  "<column_name>",    # nombre es la PK
        "functional_definition": "...",
        "data_type":            "STRING",
        "used_in_tables":       ["tabla1", "tabla2"],
        "is_new":               true | false,
        "flgactive":            true              # soft-delete flag
    }

Solo se exponen operaciones síncronas (pymongo) para compatibilidad con
los tools `@tool` del Agent Framework que no corren en un event loop.
"""

from __future__ import annotations

import threading
from typing import Any

from pymongo import MongoClient
from pymongo.collection import Collection

from src.config import settings

# ─── Singleton de conexión (thread-safe) ────────────────────────────────

_client_lock = threading.Lock()
_client: MongoClient | None = None


def _get_collection() -> Collection:
    """Devuelve la colección `column_catalog` (lazy singleton)."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                if not settings.COSMOS_CONNECTION_STRING:
                    raise RuntimeError(
                        "COSMOS_CONNECTION_STRING no está configurado en .env. "
                        "El catálogo de columnas requiere Azure Cosmos DB."
                    )
                _client = MongoClient(
                    settings.COSMOS_CONNECTION_STRING,
                    serverSelectionTimeoutMS=10_000,
                )
    return _client[settings.COSMOS_DATABASE]["column_catalog"]


# ─── CRUD ───────────────────────────────────────────────────────────────

def load_all_entries() -> list[dict[str, Any]]:
    """Devuelve todas las entradas activas del catálogo."""
    col = _get_collection()
    docs = col.find({"flgactive": {"$ne": False}}, {"_id": 0, "column_name": 1,
                                                      "functional_definition": 1,
                                                      "data_type": 1,
                                                      "used_in_tables": 1,
                                                      "is_new": 1})
    return list(docs)


def find_by_name(column_name: str) -> dict[str, Any] | None:
    """Busca una entrada por nombre de columna exacto."""
    col = _get_collection()
    doc = col.find_one({"_id": column_name, "flgactive": {"$ne": False}})
    if not doc:
        return None
    doc["column_name"] = doc.pop("_id")
    return doc


def upsert_entry(entry: dict[str, Any]) -> str:
    """Crea o actualiza una entrada en el catálogo. Retorna 'created' o 'updated'."""
    col = _get_collection()
    name = entry["column_name"]
    existing = col.find_one({"_id": name})

    if existing:
        # Actualizar `used_in_tables` y `is_new` si la tabla es nueva
        tables = existing.get("used_in_tables") or []
        new_tables = entry.get("used_in_tables") or []
        merged = list(set(tables) | set(new_tables))
        col.update_one(
            {"_id": name},
            {"$set": {
                "used_in_tables": merged,
                "functional_definition": entry.get("functional_definition", existing.get("functional_definition", "")),
                "data_type": entry.get("data_type", existing.get("data_type", "")),
                "is_new": entry.get("is_new", existing.get("is_new", False)),
                "flgactive": True,
            }},
        )
        return "updated"

    col.insert_one({
        "_id": name,
        "column_name": name,
        "functional_definition": entry.get("functional_definition", ""),
        "data_type": entry.get("data_type", ""),
        "used_in_tables": entry.get("used_in_tables") or [],
        "is_new": entry.get("is_new", True),
        "flgactive": True,
    })
    return "created"


def add_table_to_entry(column_name: str, table_name: str) -> bool:
    """Agrega una tabla al `used_in_tables` de una columna existente.
    Retorna True si la columna existía, False si no."""
    col = _get_collection()
    result = col.update_one(
        {"_id": column_name},
        {"$addToSet": {"used_in_tables": table_name}},
    )
    return result.matched_count > 0


def ensure_index() -> None:
    """Crea el índice básico si no existe (idempotente)."""
    try:
        col = _get_collection()
        col.create_index([("functional_definition", "text")], background=True)
    except Exception:
        pass  # Cosmos DB puede lanzar NamespaceExists; ignorar
