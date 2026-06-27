"""Endpoints de Relationships (ER)."""
from __future__ import annotations

from fastapi import APIRouter, status

from app.core.api.envelope import ok

from . import service
from .schemas import RelationshipBody

router = APIRouter(prefix="/api/relationships", tags=["relationships"])


@router.get("")
async def list_all():
    return ok(await service.list_all())


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(body: RelationshipBody):
    return ok(await service.create(body))


@router.put("/{rid}")
async def update(rid: str, body: RelationshipBody):
    return ok(await service.update(rid, body))


@router.delete("/{rid}")
async def delete(rid: str):
    return ok(await service.delete(rid))
