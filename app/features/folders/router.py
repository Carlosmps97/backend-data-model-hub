"""Endpoints de Folders (jerarquía anidada del Model Explorer, estilo Erwin).

Listar admite dos formas equivalentes (ambas por requerimiento):
- `GET /api/projects/{projectId}/folders` (anidado, espeja subject-areas)
- `GET /api/folders?projectId=...` (query)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.features.auth.deps import write_guard

from app.core.api.envelope import ok

from . import service
from .schemas import FolderCreateBody, FolderRenameBody

router = APIRouter(prefix="/api", tags=["folders"],
                   dependencies=[Depends(write_guard("model.edit", versioned=True))])   # doc 105


@router.get("/projects/{project_id}/folders")
async def list_folders_by_project(project_id: str):
    return ok(await service.list_folders(project_id))


@router.get("/folders")
async def list_folders(projectId: str = Query(...)):
    return ok(await service.list_folders(projectId))


def _found(x, what: str = "Folder"):
    """404 si la carpeta no existe / está borrada (doc 82): antes se devolvía
    200 con `data: null`/`false`, indistinguible de un éxito (mismo criterio
    que projects/subject-areas)."""
    if x is None or x is False:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found.")
    return x


@router.get("/folders/{folder_id}")
async def get_folder(folder_id: str):
    return ok(_found(await service.get_folder(folder_id)))


@router.post("/folders", status_code=status.HTTP_201_CREATED)
async def create_folder(body: FolderCreateBody):
    return ok(await service.create_folder(body))


@router.patch("/folders/{folder_id}")
async def update_folder(folder_id: str, body: FolderRenameBody):
    return ok(_found(await service.update_folder(folder_id, body)))


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str):
    return ok(_found(await service.delete_folder(folder_id)))
