"""Endpoints de Projects + Subject Areas (estructura estilo Erwin)."""
from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.core.api.envelope import ok

from . import service
from .schemas import DrawingsBody, LayoutBody, ProjectBody, SubjectAreaBody, TablesBody

router = APIRouter(prefix="/api", tags=["projects"])


@router.get("/projects")
async def list_projects():
    return ok(await service.list_projects())


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectBody):
    return ok(await service.create_project(body))


@router.put("/projects/{pid}")
async def update_project(pid: str, body: ProjectBody):
    return ok(await service.update_project(pid, body))


@router.delete("/projects/{pid}")
async def delete_project(pid: str):
    return ok(await service.delete_project(pid))


@router.get("/projects/{pid}/subject-areas")
async def list_subject_areas(pid: str, folderId: str | None = Query(default=None)):
    return ok(await service.list_subject_areas(pid, folderId))


@router.post("/subject-areas", status_code=status.HTTP_201_CREATED)
async def create_subject_area(body: SubjectAreaBody):
    return ok(await service.create_subject_area(body))


@router.put("/subject-areas/{sa_id}")
async def update_subject_area(sa_id: str, body: SubjectAreaBody):
    return ok(await service.update_subject_area(sa_id, body))


@router.put("/subject-areas/{sa_id}/tables")
async def set_tables(sa_id: str, body: TablesBody):
    return ok(await service.set_tables(sa_id, body.tableIds))


@router.put("/subject-areas/{sa_id}/layout")
async def set_layout(sa_id: str, body: LayoutBody):
    return ok(await service.set_layout(sa_id, body.layout))


@router.put("/subject-areas/{sa_id}/drawings")
async def set_drawings(sa_id: str, body: DrawingsBody):
    return ok(await service.set_drawings(sa_id, body.drawings))


@router.delete("/subject-areas/{sa_id}")
async def delete_subject_area(sa_id: str):
    return ok(await service.delete_subject_area(sa_id))


@router.get("/subject-areas/{sa_id}")
async def get_subject_area(sa_id: str):
    return ok(await service.get_subject_area(sa_id))


@router.get("/subject-areas/{sa_id}/diagram")
async def diagram(sa_id: str):
    return ok(await service.diagram(sa_id))
