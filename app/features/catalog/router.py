"""Endpoints del catálogo canónico (tablas + columnas universales)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.features.auth.deps import write_guard

from app.core.api.envelope import ok

from . import service
from .schemas import CanonicalColumnBody, CanonicalTableBody

router = APIRouter(prefix="/api/catalog", tags=["catalog"],
                   dependencies=[Depends(write_guard("model.edit"))])


@router.get("/tables")
async def list_tables(
    q: str | None = Query(default=None, description="búsqueda por nombre (contains, case-insensitive)"),
    limit: int | None = Query(default=None, ge=1, le=500),
):
    """Pool canónico. `q`+`limit` = búsqueda server-side (modales de catálogo);
    sin parámetros, la lista completa (compat)."""
    return ok(await service.list_tables(q, limit))


@router.post("/tables", status_code=status.HTTP_201_CREATED)
async def create_table(body: CanonicalTableBody):
    return ok(await service.create_table(body))


@router.get("/tables/{table_id}/columns")
async def list_columns(table_id: str):
    return ok(await service.list_columns(table_id))


@router.post("/tables/{table_id}/columns", status_code=status.HTTP_201_CREATED)
async def create_column(table_id: str, body: CanonicalColumnBody):
    return ok(await service.create_column(table_id, body))
