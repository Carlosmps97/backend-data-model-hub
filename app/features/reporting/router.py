"""Endpoints de `reporting` (tabla de metadata + detalle por columna, pr).
Doc 75: el reporte es de UN proyecto — `projectId` obligatorio en todas las
lecturas."""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.api.envelope import ok

from . import service

router = APIRouter(prefix="/api/reporting", tags=["reporting"])


@router.get("/tables")
async def report_tables(
    projectId: str = Query(min_length=1),
    schema: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=0),
    offset: int = Query(default=0, ge=0),
):
    """Filas del reporte por tabla del proyecto. Filtro opcional por `schema`;
    `limit` acota la cantidad (carga inicial liviana del front) y `offset`
    (doc 92 D3) pagina el scroll infinito — sólo aplica en el fast path sin
    filtros (orden `physicalName`)."""
    filters = {"schema": schema} if schema is not None else {}
    return ok(await service.list_table_rows(projectId, filters, limit, offset))


@router.get("/tables/count")
async def report_tables_count(projectId: str = Query(min_length=1)):
    """Doc 92 D4: total de tablas activas del proyecto (conteo real de la barra
    del Reporting y del Select all, aunque la grilla tenga sólo una página)."""
    return ok({"total": await service.count_tables(projectId)})


@router.get("/filters")
async def report_filters(projectId: str = Query(min_length=1)):
    """Doc 70 §4.1: universo completo de los filtros del reporte tabular
    (esquemas de tablas activas y canvases del proyecto) — sin tope."""
    return ok(await service.filter_options(projectId))


@router.get("/columns")
async def report_columns(
    projectId: str = Query(min_length=1),
    tableId: str | None = Query(default=None),
    tableIds: str | None = Query(default=None, description="ids separados por coma (export acotado)"),
    limit: int | None = Query(default=None, ge=1, le=100000),
):
    """Detalle a nivel columna para el export por niveles. `tableIds` acota el
    export a las tablas seleccionadas; sin filtros se aplica un tope de seguridad
    (`limit` o el cap por defecto) para no volcar cientos de miles de columnas."""
    id_list = [s for s in (tableIds.split(",") if tableIds else []) if s] or None
    return ok(await service.list_column_rows(projectId, tableId, id_list, limit))


@router.get("/views")
async def report_views(
    projectId: str = Query(min_length=1),
    tableIds: str | None = Query(default=None, description="ids separados por coma (export acotado)"),
    schema: str | None = Query(default=None, description="solo las vistas de este esquema (Database Explorer)"),
):
    """Vistas para el export por niveles: fuentes resueltas (`schema.tabla`),
    filtro, join override, SQL y detalle columna a columna (alias de salida,
    origen, casteo, expresión). `tableIds` acota a las vistas derivadas de las
    tablas seleccionadas; `schema` a las de un esquema (Database Explorer);
    sin filtros aplica un tope de seguridad."""
    id_list = [s for s in (tableIds.split(",") if tableIds else []) if s] or None
    return ok(await service.list_view_rows(projectId, id_list, schema=schema))
