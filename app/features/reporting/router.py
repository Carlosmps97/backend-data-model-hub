"""Endpoints de `reporting` (tabla de metadata + detalle por columna, pr)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.api.envelope import ok

from . import service

router = APIRouter(prefix="/api/reporting", tags=["reporting"])


@router.get("/tables")
async def report_tables(
    schema: str | None = Query(default=None),
    projectId: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=0),
):
    """Filas del reporte por tabla. Filtros opcionales por `schema` y
    `projectId`; `limit` acota la cantidad (carga inicial liviana del front)."""
    filters = {"schema": schema, "projectId": projectId}
    filters = {k: v for k, v in filters.items() if v is not None}
    return ok(await service.list_table_rows(filters, limit))


@router.get("/columns")
async def report_columns(
    tableId: str | None = Query(default=None),
    tableIds: str | None = Query(default=None, description="ids separados por coma (export acotado)"),
    limit: int | None = Query(default=None, ge=1, le=100000),
):
    """Detalle a nivel columna para el export por niveles. `tableIds` acota el
    export a las tablas seleccionadas; sin filtros se aplica un tope de seguridad
    (`limit` o el cap por defecto) para no volcar cientos de miles de columnas."""
    id_list = [s for s in (tableIds.split(",") if tableIds else []) if s] or None
    return ok(await service.list_column_rows(tableId, id_list, limit))


@router.get("/views")
async def report_views(
    tableIds: str | None = Query(default=None, description="ids separados por coma (export acotado)"),
    schema: str | None = Query(default=None, description="solo las vistas de este esquema (Database Explorer)"),
):
    """Vistas para el export por niveles: fuentes resueltas (`schema.tabla`),
    filtro, join override, SQL y detalle columna a columna (alias de salida,
    origen, casteo, expresión). `tableIds` acota a las vistas derivadas de las
    tablas seleccionadas; `schema` a las de un esquema (Database Explorer);
    sin filtros aplica un tope de seguridad."""
    id_list = [s for s in (tableIds.split(",") if tableIds else []) if s] or None
    return ok(await service.list_view_rows(id_list, schema=schema))
