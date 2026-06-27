"""Negocio de `relationships` (thin)."""
from __future__ import annotations

from . import repository
from .schemas import RelationshipBody


async def list_all() -> list[dict]:
    return await repository.list_all()


async def create(body: RelationshipBody) -> dict:
    return await repository.create(body.model_dump())


async def update(rid: str, body: RelationshipBody) -> dict | None:
    return await repository.update(rid, body.model_dump())


async def delete(rid: str) -> bool:
    return await repository.delete(rid)
