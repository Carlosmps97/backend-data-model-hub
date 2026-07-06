"""Endpoints de Views (vistas SQL)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.features.auth.deps import write_guard

from app.core.api.envelope import ok

from . import service
from .schemas import ViewBody

router = APIRouter(prefix="/api/views", tags=["views"],
                   dependencies=[Depends(write_guard("model.edit"))])


@router.get("")
async def list_all(tableId: str | None = None):
    return ok(await service.list_all(tableId))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(body: ViewBody):
    return ok(await service.create(body))


@router.put("/{vid}")
async def update(vid: str, body: ViewBody):
    res = await service.update(vid, body)
    if res is None:
        raise HTTPException(status_code=404, detail="Vista no encontrada.")
    return ok(res)


@router.delete("/{vid}")
async def delete(vid: str):
    if not await service.delete(vid):
        raise HTTPException(status_code=404, detail="Vista no encontrada.")
    return ok({"id": vid})
