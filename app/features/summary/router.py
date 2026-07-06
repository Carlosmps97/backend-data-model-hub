"""Endpoint de `summary` (5 cards del Home, p0)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.api.envelope import ok

from . import service

router = APIRouter(prefix="/api/summary", tags=["summary"])


@router.get("")
async def get_summary(projectId: list[str] | None = Query(default=None)):
    """Contadores del Home. Sin `projectId` = totales globales activos; con uno o
    varios `projectId` (repetible) = unión de su alcance (ver `service`)."""
    return ok(await service.summary(projectId))
