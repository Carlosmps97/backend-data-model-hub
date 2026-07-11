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
async def list_all(tableId: str | None = None, tableIds: str | None = None):
    # `tableIds` (CSV) trae en UNA request las vistas de todas las tablas de un
    # canvas (p.ej. para el Export DDL), sin N llamadas por tabla.
    ids = [i for i in (tableIds.split(",") if tableIds else []) if i]
    return ok(await service.list_all(tableId, ids or None))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(body: ViewBody):
    if not (body.sourceTableIds or body.tableId):
        # F3: una vista sin tabla fuente no puede derivar nada (ni DDL ni
        # canvas). Con 2+ fuentes SIN joinOverride SÍ se permite guardar:
        # el JOIN se infiere de las relaciones al generar el DDL en el front.
        raise HTTPException(status_code=409,
                            detail="La vista necesita al menos una tabla fuente.")
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
