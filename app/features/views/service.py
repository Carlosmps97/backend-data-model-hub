"""Negocio de `views` (thin)."""
from __future__ import annotations

from . import repository
from .schemas import ViewBody


async def list_all(table_id: str | None = None, table_ids: list[str] | None = None) -> list[dict]:
    return await repository.list_all(table_id, table_ids)


async def create(body: ViewBody) -> dict:
    # by_alias: `schema` llega como `schema` al repo (no `sql_schema`).
    return await repository.create(body.model_dump(by_alias=True))


async def update(vid: str, body: ViewBody) -> dict | None:
    return await repository.update(vid, body.model_dump(by_alias=True))


async def delete(vid: str) -> bool:
    return await repository.delete(vid)
