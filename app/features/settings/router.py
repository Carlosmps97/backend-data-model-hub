"""Endpoints de configuración de naming (separador/case/maxLength) por
(proyecto, scope) — doc 75 D3/D4: prefijo `/api/projects/{project_id}/settings`."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.api.envelope import ok
from app.features.data_standards.deps import standards_read_only
from app.features.projects.deps import alive_project

from . import service
from .schemas import NamingConfigBody

# Doc 105 (D1b): el router sólo LEE. El PUT sigue declarado pero responde 409 en
# `standards_read_only`: el naming se cambia por Data Standards (`standards/apply`).
router = APIRouter(prefix="/api/projects/{project_id}/settings", tags=["settings"],
                   dependencies=[Depends(standards_read_only)])


@router.get("/naming")
async def get_naming(project_id: str = Depends(alive_project)):
    """Ambos scopes (column/table) del proyecto, con defaults sembrados si no existen."""
    return ok(await service.get_naming(project_id))


@router.put("/naming/{scope}")
async def update_naming(scope: str, body: NamingConfigBody, project_id: str = Depends(alive_project)):
    """Upsert de {separator, case, maxLength} para `scope` ('column' | 'table').
    Cerrada (doc 105 D1b): 409 antes de cualquier efecto."""
    try:
        return ok(await service.update_naming(project_id, scope, body))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
