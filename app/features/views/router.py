"""Endpoints de Views (vistas SQL)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.identity import Principal, current_principal
from app.features.auth.deps import write_guard
from app.features.changesets.access import ensure_changeset_visible

from app.core.api.envelope import ok

from . import service
from .custom_sql import CustomSqlError, parse_custom_sql
from .schemas import SqlParseBody, ViewBody


def _custom_sql_422(e: CustomSqlError) -> HTTPException:
    # detail estructurado: el sandbox pinta el caret con line/col.
    return HTTPException(status_code=422, detail={
        "message": e.message, "line": e.line, "col": e.col})

router = APIRouter(prefix="/api/views", tags=["views"],
                   dependencies=[Depends(write_guard("model.edit"))])


@router.get("")
async def list_all(tableId: str | None = None, tableIds: str | None = None,
                   projectId: str | None = None):
    # `tableIds` (CSV) trae en UNA request las vistas de todas las tablas de un
    # canvas (p.ej. para el Export DDL), sin N llamadas por tabla. Sin tabla,
    # `projectId` es obligatorio (doc 75 D6: nunca la colección completa).
    ids = [i for i in (tableIds.split(",") if tableIds else []) if i]
    if not tableId and not ids and not projectId:
        raise HTTPException(status_code=422, detail="projectId is required when no table is given.")
    return ok(await service.list_all(tableId, ids or None, projectId))


@router.get("/for-table")
async def for_table(tableId: str = Query(...), changesetId: str | None = Query(default=None),
                    principal: Principal = Depends(current_principal)):
    """Doc 70: vistas GLOBALES de la tabla con los canvases donde cada una es
    miembro (tab Views del panel: «This canvas» / «Other canvases»). Publicadas
    + overlay del changeset, igual que `GET /api/relationships/links`."""
    await ensure_changeset_visible(changesetId, principal)   # doc 70 §12
    return ok(await service.table_views(tableId, changesetId))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(body: ViewBody):
    if not (body.sourceTableIds or body.tableId):
        # F3: una vista sin tabla fuente no puede derivar nada (ni DDL ni
        # canvas). Con 2+ fuentes SIN joinOverride SÍ se permite guardar:
        # el JOIN se infiere de las relaciones al generar el DDL en el front.
        raise HTTPException(status_code=409,
                            detail="The view needs at least one source table.")
    try:
        return ok(await service.create(body))
    except CustomSqlError as e:
        raise _custom_sql_422(e) from e


@router.post("/sql/parse")
async def parse_sql(body: SqlParseBody):
    """Doc 61: valida e interpreta el SQL custom SIN guardar (botón Validate
    del sandbox de Query SQL). Devuelve columnas de salida y tablas referidas."""
    try:
        return ok(parse_custom_sql(body.sql))
    except CustomSqlError as e:
        raise _custom_sql_422(e) from e


@router.put("/{vid}")
async def update(vid: str, body: ViewBody):
    try:
        res = await service.update(vid, body)
    except CustomSqlError as e:
        raise _custom_sql_422(e) from e
    if res is None:
        raise HTTPException(status_code=404, detail="View not found.")
    return ok(res)


@router.delete("/{vid}")
async def delete(vid: str):
    if not await service.delete(vid):
        raise HTTPException(status_code=404, detail="View not found.")
    return ok({"id": vid})
