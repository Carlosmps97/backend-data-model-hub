"""Endpoints de Relationships (ER)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.features.auth.deps import write_guard

from app.core.api.envelope import ok

from . import service
from .schemas import RelationshipBody

router = APIRouter(prefix="/api/relationships", tags=["relationships"],
                   dependencies=[Depends(write_guard("model.edit"))])


@router.get("")
async def list_all(tableId: str | None = Query(default=None)):
    """Con `tableId`, sólo las relaciones que tocan esa tabla (slice `$in`)."""
    return ok(await service.list_for_table(tableId) if tableId else await service.list_all())


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(body: RelationshipBody):
    return ok(await service.create(body))


@router.put("/{rid}")
async def update(rid: str, body: RelationshipBody):
    res = await service.update(rid, body)
    if res is None:
        raise HTTPException(status_code=404, detail="Relación no encontrada.")
    return ok(res)


@router.delete("/{rid}")
async def delete(rid: str):
    if not await service.delete(rid):
        raise HTTPException(status_code=404, detail="Relación no encontrada.")
    return ok({"id": rid})
