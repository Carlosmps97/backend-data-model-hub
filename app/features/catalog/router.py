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
    schema: str | None = Query(default=None, description="acota a UN esquema (filtro del modal Import existing)"),
):
    """Pool canónico. `q`+`limit` = búsqueda server-side (modales de catálogo);
    `schema` acota a un esquema (server-side, combinable con `q`); sin parámetros,
    la lista completa (compat)."""
    return ok(await service.list_tables(q, limit, schema))


@router.post("/tables", status_code=status.HTTP_201_CREATED)
async def create_table(body: CanonicalTableBody):
    return ok(await service.create_table(body))


@router.get("/columns")
async def search_columns(
    q: str = Query(min_length=1, description="búsqueda por nombre de columna (contains, case-insensitive)"),
    limit: int = Query(default=50, ge=1, le=500),
):
    """Búsqueda global por COLUMNA (Database Explorer): hits con su tabla
    resuelta (`table` + `schema`) para mostrarse como esquema.tabla."""
    return ok(await service.search_columns(q, limit))


@router.get("/tables/{table_id}/columns")
async def list_columns(table_id: str):
    return ok(await service.list_columns(table_id))


@router.post("/tables/{table_id}/columns", status_code=status.HTTP_201_CREATED)
async def create_column(table_id: str, body: CanonicalColumnBody):
    return ok(await service.create_column(table_id, body))


@router.get("/tables/{table_id}/usage")
async def table_usage(table_id: str, changesetId: str | None = Query(default=None)):
    """Dónde se usa la tabla (V3, doc 19 §12b): canvases que la referencian con
    carpeta y proyecto; con `changesetId` aplica el overlay del draft."""
    return ok(await service.table_usage(table_id, changesetId))
