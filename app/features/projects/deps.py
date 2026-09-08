"""Dependencia de TODA ruta `/api/projects/{project_id}/…` (doc 75 D4/D5): el
proyecto existe y está activo; si fue borrado responde 404 uniforme."""
from __future__ import annotations

from fastapi import HTTPException, status

from . import repository


async def alive_project(project_id: str) -> str:
    if not await repository.get_project(project_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return project_id
