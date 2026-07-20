"""Endpoints de configuración de naming (separador/case) por scope."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.features.auth.deps import write_guard

from app.core.api.envelope import ok

from . import service
from .schemas import NamingConfigBody

router = APIRouter(prefix="/api/settings", tags=["settings"],
                   dependencies=[Depends(write_guard("standards.edit"))])


@router.get("/naming")
async def get_naming():
    """Ambos scopes (column/table) con defaults sembrados si no existen."""
    return ok(await service.get_naming())


@router.put("/naming/{scope}")
async def update_naming(scope: str, body: NamingConfigBody):
    """Upsert de {separator, case} para `scope` ('column' | 'table')."""
    try:
        return ok(await service.update_naming(scope, body))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
