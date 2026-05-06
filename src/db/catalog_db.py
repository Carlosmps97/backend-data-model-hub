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
        "flgactive":            true,               # soft-delete flag
        "embedding":            [0.12, ...]         # vector 1536-dim (text-embedding-3-small)
    }

Las operaciones CRUD son síncronas (pymongo) para compatibilidad con los
tools @tool del Agent Framework. Las capacidades vectoriales (embed_texts,
search_similar_columns) son asíncronas y usan motor + AsyncAzureOpenAI.
"""

from __future__ import annotations

import threading
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from openai import AsyncAzureOpenAI, AzureOpenAI
from pymongo import MongoClient
from pymongo.collection import Collection

from src.config import settings

# ─── Pymongo singleton (sync, thread-safe) ──────────────────────────────────

_client_lock = threading.Lock()
_client: MongoClient | None = None


def _get_collection() -> Collection:
    """Devuelve la colección `column_catalog` (lazy singleton pymongo)."""
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


# ─── Motor singleton (async, single-threaded asyncio) ───────────────────────

_motor_client: AsyncIOMotorClient | None = None


def _get_motor_collection() -> AsyncIOMotorCollection:
    """Devuelve la colección `column_catalog` vía motor (lazy singleton)."""
    global _motor_client
    if _motor_client is None:
        if not settings.COSMOS_CONNECTION_STRING:
            raise RuntimeError(
                "COSMOS_CONNECTION_STRING no está configurado en .env."
            )
        _motor_client = AsyncIOMotorClient(
            settings.COSMOS_CONNECTION_STRING,
            serverSelectionTimeoutMS=10_000,
        )
    return _motor_client[settings.COSMOS_DATABASE]["column_catalog"]


# ─── Embedding clients (lazy singletons) ────────────────────────────────────

_async_embed_client: AsyncAzureOpenAI | None = None
_sync_embed_client: AzureOpenAI | None = None
_embed_lock = threading.Lock()


def _embedding_configured() -> bool:
    return bool(
        settings.AZURE_OPENAI_ENDPOINT
        and settings.FOUNDRY_API_KEY
        and settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
    )


def _get_sync_embed_client() -> AzureOpenAI | None:
    global _sync_embed_client
    if not _embedding_configured():
        return None
    if _sync_embed_client is None:
        with _embed_lock:
            if _sync_embed_client is None:
                _sync_embed_client = AzureOpenAI(
                    api_key=settings.FOUNDRY_API_KEY,
                    api_version=settings.AZURE_OPENAI_API_VERSION,
                    azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                )
    return _sync_embed_client


def _embed_text_sync(text: str) -> list[float] | None:
    """Vectoriza un texto con el cliente sync de Azure OpenAI (para upsert)."""
    client = _get_sync_embed_client()
    if not client:
        return None
    try:
        resp = client.embeddings.create(
            input=[text],
            model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
        )
        return resp.data[0].embedding
    except Exception:
        return None


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Vectoriza una lista de textos con Azure OpenAI Embeddings (async, batch).

    Retorna lista vacía si la configuración de embeddings no está disponible
    o si la API falla — el flujo nunca se bloquea por esto.
    """
    global _async_embed_client
    if not _embedding_configured() or not texts:
        return []
    try:
        if _async_embed_client is None:
            _async_embed_client = AsyncAzureOpenAI(
                api_key=settings.FOUNDRY_API_KEY,
                api_version=settings.AZURE_OPENAI_API_VERSION,
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            )
        resp = await _async_embed_client.embeddings.create(
            input=texts,
            model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
        )
        return [item.embedding for item in resp.data]
    except Exception:
        return []


# ─── Búsqueda vectorial (async, motor) ──────────────────────────────────────

async def search_similar_columns(
    query_embedding: list[float],
    limit: int = 3,
    threshold: float | None = None,
) -> list[dict[str, Any]]:
    """Busca columnas históricas semánticamente similares al query_embedding.

    Prueba primero la sintaxis nativa de Cosmos DB vCore (`cosmosSearch`) y
    hace fallback al estándar de MongoDB Atlas (`$vectorSearch`).
    Filtra resultados con score < threshold antes de retornar.
    Retorna lista vacía ante cualquier error para no bloquear el flujo.
    """
    if threshold is None:
        threshold = settings.VECTOR_SIMILARITY_THRESHOLD

    if not query_embedding:
        return []

    col = _get_motor_collection()

    # Cosmos DB vCore: $search con cosmosSearch
    cosmos_pipeline = [
        {
            "$search": {
                "cosmosSearch": {
                    "vector": query_embedding,
                    "path": "embedding",
                    "k": limit,
                }
            }
        },
        {
            "$project": {
                "_id": 1,
                "column_name": 1,
                "functional_definition": 1,
                "data_type": 1,
                "flgactive": 1,
                "score": {"$meta": "searchScore"},
            }
        },
        {"$match": {"flgactive": {"$ne": False}}},
    ]

    try:
        cursor = col.aggregate(cosmos_pipeline)
        results = await cursor.to_list(length=limit)
        filtered = [r for r in results if r.get("score", 0) >= threshold]
        if filtered:
            return filtered
    except Exception:
        pass

    # Fallback: MongoDB Atlas $vectorSearch
    atlas_pipeline = [
        {
            "$vectorSearch": {
                "queryVector": query_embedding,
                "path": "embedding",
                "numCandidates": limit * 10,
                "limit": limit,
                "index": "VectorSearchIndex",
            }
        },
        {
            "$project": {
                "_id": 1,
                "column_name": 1,
                "functional_definition": 1,
                "data_type": 1,
                "flgactive": 1,
                "score": {"$meta": "vectorSearchScore"},
            }
        },
        {"$match": {"flgactive": {"$ne": False}}},
    ]

    try:
        cursor = col.aggregate(atlas_pipeline)
        results = await cursor.to_list(length=limit)
        return [r for r in results if r.get("score", 0) >= threshold]
    except Exception:
        return []


# ─── CRUD (sync, pymongo) ────────────────────────────────────────────────────

def load_all_entries() -> list[dict[str, Any]]:
    """Devuelve todas las entradas activas del catálogo."""
    col = _get_collection()
    docs = col.find(
        {"flgactive": {"$ne": False}},
        {
            "_id": 0,
            "column_name": 1,
            "functional_definition": 1,
            "data_type": 1,
            "used_in_tables": 1,
            "is_new": 1,
        },
    )
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
    """Crea o actualiza una entrada en el catálogo. Retorna 'created' o 'updated'.

    Genera el embedding de la definición funcional antes de persistir,
    almacenándolo en el campo `embedding` para búsqueda vectorial futura.
    Si la API de embeddings no está disponible o falla, el upsert continúa
    sin el campo embedding (degradación graceful).
    """
    col = _get_collection()
    name = entry["column_name"]
    fdef = entry.get("functional_definition") or ""

    embedding = _embed_text_sync(fdef) if fdef else None

    existing = col.find_one({"_id": name})

    if existing:
        tables = existing.get("used_in_tables") or []
        new_tables = entry.get("used_in_tables") or []
        merged = list(set(tables) | set(new_tables))
        update_fields: dict[str, Any] = {
            "used_in_tables": merged,
            "functional_definition": entry.get(
                "functional_definition", existing.get("functional_definition", "")
            ),
            "data_type": entry.get("data_type", existing.get("data_type", "")),
            "is_new": entry.get("is_new", existing.get("is_new", False)),
            "flgactive": True,
        }
        if embedding:
            update_fields["embedding"] = embedding
        col.update_one({"_id": name}, {"$set": update_fields})
        return "updated"

    doc: dict[str, Any] = {
        "_id": name,
        "column_name": name,
        "functional_definition": fdef,
        "data_type": entry.get("data_type", ""),
        "used_in_tables": entry.get("used_in_tables") or [],
        "is_new": entry.get("is_new", True),
        "flgactive": True,
    }
    if embedding:
        doc["embedding"] = embedding
    col.insert_one(doc)
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
    """Crea los índices necesarios si no existen (idempotente)."""
    try:
        col = _get_collection()
        col.create_index([("functional_definition", "text")], background=True)
    except Exception:
        pass

    # Índice de búsqueda vectorial para el Semantic Data Dictionary
    try:
        _get_collection()  # asegura que _client esté inicializado
        db = _client[settings.COSMOS_DATABASE]
        db.command({
            "createIndexes": "column_catalog",
            "indexes": [
                {
                    "name": "VectorSearchIndex",
                    "key": {"embedding": "cosmosSearch"},
                    "cosmosSearchOptions": {
                        "kind": "vector-ivf",
                        "numLists": 1,
                        "similarity": "COS",
                        "dimensions": 1536,
                    },
                }
            ],
        })
    except Exception:
        pass  # El índice puede ya existir o el motor no soportarlo aún
