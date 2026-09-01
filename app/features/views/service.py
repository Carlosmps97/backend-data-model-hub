"""Negocio de `views` (thin)."""
from __future__ import annotations

from . import repository
from .custom_sql import parse_custom_sql
from .models import normalize_source_tables
from .schemas import ViewBody


def _with_custom_sql(doc: dict) -> dict:
    """Doc 61: normaliza el modo Personalizada. customSql vacío → None +
    customColumns limpias (modo Regular); con script → RE-deriva customColumns
    del parse (no se confía en el cliente). Levanta CustomSqlError si el
    script no parsea (el router lo convierte en 422). Pura."""
    sql = (doc.get("customSql") or "").strip()
    if not sql:
        return {**doc, "customSql": None, "customColumns": []}
    return {**doc, "customSql": sql,
            "customColumns": parse_custom_sql(sql)["columns"]}


async def list_all(table_id: str | None = None, table_ids: list[str] | None = None) -> list[dict]:
    return await repository.list_all(table_id, table_ids)


async def create(body: ViewBody) -> dict:
    # by_alias: `schema` llega como `schema` al repo (no `sql_schema`).
    return await repository.create(_with_custom_sql(body.model_dump(by_alias=True)))


async def update(vid: str, body: ViewBody) -> dict | None:
    # Normaliza ANTES del $set: repository.update escribe el dict tal cual a
    # Mongo (solo la RESPUESTA se re-valida vía ViewDoc); sin esto el doc
    # persistido quedaría con tableId/sourceTableIds inconsistentes.
    return await repository.update(
        vid, _with_custom_sql(normalize_source_tables(body.model_dump(by_alias=True))))


async def delete(vid: str) -> bool:
    return await repository.delete(vid)
