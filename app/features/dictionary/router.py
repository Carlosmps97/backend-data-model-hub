"""Endpoints del diccionario de abreviaturas + conversión lógico↔físico."""
from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.core.api.envelope import ok

from . import service
from .schemas import (
    AbbreviationBody,
    LogicalizeBody,
    PhysicalizeBody,
    RephysicalizeBody,
)

router = APIRouter(prefix="/api/dictionary", tags=["dictionary"])


@router.get("")
async def list_entries(scope: str | None = Query(default=None)):
    """Lista términos. Sin `scope` = todos (compat); con `scope` = sólo ese
    ('column' | 'table')."""
    return ok(await service.list_entries(scope))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_entry(body: AbbreviationBody):
    return ok(await service.create_entry(body))


@router.put("/{entry_id}")
async def update_entry(entry_id: str, body: AbbreviationBody):
    return ok(await service.update_entry(entry_id, body))


@router.delete("/{entry_id}")
async def delete_entry(entry_id: str):
    return ok(await service.delete_entry(entry_id))


@router.post("/physicalize")
async def physicalize_name(body: PhysicalizeBody):
    physical = await service.physicalize_name(
        body.logical, scope=body.scope, separator=body.separator
    )
    return ok({"physical": physical})


@router.post("/logicalize")
async def logicalize_name(body: LogicalizeBody):
    return ok({"logical": await service.logicalize_name(body.physical)})


@router.post("/rephysicalize")
async def rephysicalize(body: RephysicalizeBody):
    """Re-physicalize retroactivo (R5): recomputa el `physicalName` de TODAS las
    entidades del scope desde su `logicalName`. Sin scope ⇒ tablas y columnas.
    Update directo (fuera de publish). Devuelve `{updated: {tables, columns}}`."""
    return ok(await service.rephysicalize(body.scope))
