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
):
    """Filas del reporte por tabla. Filtros opcionales por `schema` y
    `projectId` (hay lugar para más sin romper el contrato)."""
    filters = {"schema": schema, "projectId": projectId}
    filters = {k: v for k, v in filters.items() if v is not None}
    return ok(await service.list_table_rows(filters))


@router.get("/columns")
async def report_columns(tableId: str | None = Query(default=None)):
    """Detalle a nivel columna para el export por niveles. Sin `tableId`
    devuelve todas (Export to Excel completo)."""
    return ok(await service.list_column_rows(tableId))
