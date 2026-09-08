"""Endpoints de Relationships (ER)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.identity import Principal, current_principal
from app.features.auth.deps import write_guard
from app.features.changesets.access import ensure_changeset_visible

from app.core.api.envelope import ok

from . import service
from .schemas import RelationshipBody

router = APIRouter(prefix="/api/relationships", tags=["relationships"],
                   dependencies=[Depends(write_guard("model.edit"))])


@router.get("")
async def list_all(tableId: str | None = Query(default=None)):
    """Con `tableId`, sólo las relaciones que tocan esa tabla (slice `$in`)."""
    return ok(await service.list_for_table(tableId) if tableId else await service.list_all())


@router.get("/impact")
async def impact(columnId: str = Query(...), changesetId: str | None = Query(default=None)):
    """Impacto GLOBAL de eliminar una columna (spec 10 §8): relaciones activas
    (publicadas + overlay del changeset) donde es extremo, enriquecidas con el
    otro extremo (tabla.columna) y los canvases donde la relación es visible."""
    return ok(await service.column_impact(columnId, changesetId))


@router.get("/links")
async def links(tableId: str = Query(...), changesetId: str | None = Query(default=None),
                principal: Principal = Depends(current_principal)):
    await ensure_changeset_visible(changesetId, principal)   # doc 70 §12
    """Relaciones de una tabla en TODOS los canvases (la tabla es canónica),
    con nombres resueltos y los canvases donde cada relación es visible —
    alimenta Properties · Links (bloques del canvas actual + de otros)."""
    return ok(await service.table_links(tableId, changesetId))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(body: RelationshipBody):
    return ok(await service.create(body))


@router.put("/{rid}")
async def update(rid: str, body: RelationshipBody):
    res = await service.update(rid, body)
    if res is None:
        raise HTTPException(status_code=404, detail="Relationship not found.")
    return ok(res)


@router.delete("/{rid}")
async def delete(rid: str):
    if not await service.delete(rid):
        raise HTTPException(status_code=404, detail="Relationship not found.")
    return ok({"id": rid})
