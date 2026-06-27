"""Endpoints del catálogo canónico (tablas + columnas universales)."""
from __future__ import annotations

from fastapi import APIRouter, status

from app.core.api.envelope import ok

from . import service
from .schemas import CanonicalColumnBody, CanonicalTableBody

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/tables")
async def list_tables():
    return ok(await service.list_tables())


@router.post("/tables", status_code=status.HTTP_201_CREATED)
async def create_table(body: CanonicalTableBody):
    return ok(await service.create_table(body))


@router.get("/tables/{table_id}/columns")
async def list_columns(table_id: str):
    return ok(await service.list_columns(table_id))


@router.post("/tables/{table_id}/columns", status_code=status.HTTP_201_CREATED)
async def create_column(table_id: str, body: CanonicalColumnBody):
    return ok(await service.create_column(table_id, body))
