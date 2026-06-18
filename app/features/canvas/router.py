"""Endpoints del canvas de un proyecto (tablas + relaciones).

GET   /api/projects/{id}/canvas      — canvas hidratado (acceso view)
PUT   /api/projects/{id}/canvas      — reemplaza tablas/relaciones (acceso edit)
PATCH /api/projects/{id}/positions   — persistencia rápida del layout (acceso edit)
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.api.envelope import ok
from app.features.auth import AuthUserDep

from . import service
from .schemas import CanvasReplaceRequest, PatchPositionsRequest

router = APIRouter(prefix="/api/projects", tags=["canvas"])


@router.get("/{project_id}/canvas")
async def get_project_canvas(project_id: str, user: AuthUserDep):
    return ok(await service.read(project_id, user))


@router.put("/{project_id}/canvas")
async def replace_project_canvas(project_id: str, body: CanvasReplaceRequest, user: AuthUserDep):
    return ok(await service.replace(project_id, body, user))


@router.patch("/{project_id}/positions")
async def patch_positions(project_id: str, body: PatchPositionsRequest, user: AuthUserDep):
    return ok(await service.patch_positions(project_id, body, user))
