"""Endpoints de Views (vistas SQL)."""
from __future__ import annotations

from fastapi import APIRouter, status

from app.core.api.envelope import ok

from . import service
from .schemas import ViewBody

router = APIRouter(prefix="/api/views", tags=["views"])


@router.get("")
async def list_all(tableId: str | None = None):
    return ok(await service.list_all(tableId))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(body: ViewBody):
    return ok(await service.create(body))


@router.put("/{vid}")
async def update(vid: str, body: ViewBody):
    return ok(await service.update(vid, body))


@router.delete("/{vid}")
async def delete(vid: str):
    return ok(await service.delete(vid))
