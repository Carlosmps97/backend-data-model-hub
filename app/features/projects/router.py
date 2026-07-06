"""Endpoints de Projects + Subject Areas (estructura estilo Erwin)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.features.auth.deps import write_guard


def _found(x, what: str = "Recurso"):
    """404 si la entidad no existe / soft-deleted (el servicio devuelve None o
    False) — antes se devolvía 200 con data:null/false, indistinguible de éxito."""
    if x is None or x is False:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} no encontrado.")
    return x

from app.core.api.envelope import ok

from . import service
from .schemas import DrawingsBody, LayoutBody, ProjectBody, SubjectAreaBody, TablesBody

router = APIRouter(prefix="/api", tags=["projects"],
                   dependencies=[Depends(write_guard("model.edit"))])


@router.get("/projects")
async def list_projects():
    return ok(await service.list_projects())


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectBody):
    return ok(await service.create_project(body))


@router.put("/projects/{pid}")
async def update_project(pid: str, body: ProjectBody):
    return ok(_found(await service.update_project(pid, body), "Proyecto"))


@router.delete("/projects/{pid}")
async def delete_project(pid: str):
    return ok(_found(await service.delete_project(pid), "Proyecto"))


@router.get("/projects/{pid}/subject-areas")
async def list_subject_areas(pid: str, folderId: str | None = Query(default=None)):
    return ok(await service.list_subject_areas(pid, folderId))


@router.post("/subject-areas", status_code=status.HTTP_201_CREATED)
async def create_subject_area(body: SubjectAreaBody):
    return ok(await service.create_subject_area(body))


@router.put("/subject-areas/{sa_id}")
async def update_subject_area(sa_id: str, body: SubjectAreaBody):
    return ok(_found(await service.update_subject_area(sa_id, body), "Canvas"))


@router.put("/subject-areas/{sa_id}/tables")
async def set_tables(sa_id: str, body: TablesBody):
    return ok(_found(await service.set_tables(sa_id, body.tableIds), "Canvas"))


@router.put("/subject-areas/{sa_id}/layout")
async def set_layout(sa_id: str, body: LayoutBody):
    return ok(_found(await service.set_layout(sa_id, body.layout), "Canvas"))


@router.put("/subject-areas/{sa_id}/drawings")
async def set_drawings(sa_id: str, body: DrawingsBody):
    return ok(_found(await service.set_drawings(sa_id, body.drawings), "Canvas"))


@router.delete("/subject-areas/{sa_id}")
async def delete_subject_area(sa_id: str):
    return ok(_found(await service.delete_subject_area(sa_id), "Canvas"))


@router.get("/subject-areas/{sa_id}")
async def get_subject_area(sa_id: str):
    return ok(_found(await service.get_subject_area(sa_id), "Canvas"))


@router.get("/subject-areas/{sa_id}/diagram")
async def diagram(sa_id: str, changesetId: str | None = Query(default=None)):
    """Diagrama del canvas en una request. Con `changesetId`, el overlay del
    working copy se computa server-side (modo edición/visor)."""
    return ok(_found(await service.diagram(sa_id, changeset_id=changesetId), "Canvas"))
