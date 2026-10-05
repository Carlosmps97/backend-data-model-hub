"""Doc 109 — lectura de los themes de color del proyecto. Las mutaciones van
versionadas por `POST /api/projects/{pid}/standards/apply` (themesUpsert /
themesDelete), como el resto de Data Standards."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.api.envelope import ok
from app.features.projects.deps import alive_project

from . import repository

router = APIRouter(prefix="/api/projects/{project_id}/themes", tags=["themes"])


@router.get("")
async def list_themes(project_id: str = Depends(alive_project)):
    """Themes activos del proyecto, en su orden: [{id, name, color, order}]."""
    return ok(await repository.list_themes(project_id))
