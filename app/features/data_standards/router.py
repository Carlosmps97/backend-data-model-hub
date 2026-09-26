"""Endpoints del módulo Data Standards (versionado independiente de UDP +
Parent Domains + glosario + naming + reglas DDL), POR PROYECTO (doc 75 D3/D4).
Lectura abierta a quien ve el módulo; apply exige `standards.edit`, rollback
exige `rollback`.

  GET  /api/projects/{pid}/standards/snapshot  → estado actual de estándares
  GET  /api/projects/{pid}/standards/versions  → historial, más reciente primero
  GET  /api/projects/{pid}/standards/summary   → conteos por bloque (asistente New project)
  POST /api/projects/{pid}/standards/apply     → aplica un batch + registra versión
  POST /api/projects/{pid}/standards/rollback  → restaura una versión + registra versión
  GET  /api/projects/{pid}/standards/rollback-preview?targetSeq=N → qué re-tipa/re-deriva (doc 95)
  GET  /api/projects/{pid}/standards/domains/{id}/history          → historial de UN dominio (doc 95)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.api.envelope import ok
from app.features.auth.deps import require_permission
from app.features.projects.deps import alive_project

from . import service
from .schemas import ApplyBody, RollbackBody

router = APIRouter(prefix="/api/projects/{project_id}/standards", tags=["data-standards"],
                   dependencies=[Depends(alive_project)])


@router.get("/snapshot")
async def snapshot(project_id: str):
    return ok(await service.current_snapshot(project_id))


@router.get("/versions")
async def versions(project_id: str):
    return ok(await service.history(project_id))


@router.get("/summary")
async def summary(project_id: str):
    return ok(await service.summary(project_id))


@router.get("/rollback-preview")
async def rollback_preview(project_id: str, targetSeq: int = Query(ge=1)):
    """Doc 95 D9: qué re-tipa y qué re-deriva el rollback a `targetSeq`, sin mutar."""
    out = await service.rollback_preview(project_id, targetSeq)
    if out is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="That standards version doesn't exist.")
    return ok(out)


@router.get("/domains/{domain_id}/history")
async def domain_history(project_id: str, domain_id: str):
    """Doc 95 D8: versiones donde cambió UN dominio, con su estado en cada una."""
    return ok(await service.domain_history_of(project_id, domain_id))


@router.post("/apply")
async def apply(project_id: str, body: ApplyBody,
                user: dict = Depends(require_permission("standards.edit"))):
    return ok(await service.apply(user["username"], project_id, body))


@router.post("/rollback")
async def rollback(project_id: str, body: RollbackBody,
                   user: dict = Depends(require_permission("rollback"))):
    """Rollback de Data Standards del proyecto a cualquier versión (por `targetSeq`).
    Requiere el permiso `rollback`."""
    version = await service.rollback(user["username"], project_id, body.targetSeq)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="That standards version doesn't exist.")
    return ok(version)
