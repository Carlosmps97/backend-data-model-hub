"""Endpoints del módulo Data Standards (versionado independiente de UDP +
Parent Domains). Lectura abierta a quien ve el módulo; apply/rollback exigen el
permiso `standards.edit`.

  GET  /api/standards/snapshot          → estado actual de estándares
  GET  /api/standards/versions          → historial (15f), más reciente primero
  POST /api/standards/apply             → aplica un batch + registra versión (15d/15e)
  POST /api/standards/rollback          → restaura una versión + registra versión (15g)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.api.envelope import ok
from app.features.auth.deps import require_permission

from . import service
from .schemas import ApplyBody, RollbackBody

router = APIRouter(prefix="/api/standards", tags=["data-standards"])


@router.get("/snapshot")
async def snapshot():
    return ok(await service.current_snapshot())


@router.get("/versions")
async def versions():
    return ok(await service.history())


@router.post("/apply")
async def apply(body: ApplyBody, user: dict = Depends(require_permission("standards.edit"))):
    return ok(await service.apply(user["username"], body))


@router.post("/rollback")
async def rollback(body: RollbackBody, user: dict = Depends(require_permission("standards.edit"))):
    version = await service.rollback(user["username"], body.targetSeq)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="That standards version doesn't exist.")
    return ok(version)
