"""Endpoints de Projects + Subject Areas (estructura estilo Erwin)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.identity import Principal, current_principal
from app.features.auth.deps import write_guard
from app.features.changesets.access import ensure_changeset_visible

from .deps import alive_project
from .service import DuplicateProjectError


def _found(x, what: str = "Resource"):
    """404 si la entidad no existe / soft-deleted (el servicio devuelve None o
    False) — antes se devolvía 200 con data:null/false, indistinguible de éxito."""
    if x is None or x is False:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found.")
    return x

from app.core.api.envelope import ok

from . import service
from .schemas import (
    DrawingsBody,
    LayoutBody,
    ProjectCreateBody,
    SubjectAreaBody,
    TablesBody,
    ViewsBody,
)

router = APIRouter(prefix="/api", tags=["projects"],
                   dependencies=[Depends(write_guard("model.edit"))])


@router.get("/projects")
async def list_projects():
    return ok(await service.list_projects())


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreateBody, principal: Principal = Depends(current_principal)):
    """Doc 75 D5/D15: la ÚNICA operación directa sobre proyectos. Renombrar,
    describir y borrar van por un draft del propio proyecto."""
    try:
        return ok(await service.create_project(principal.username, body))
    except DuplicateProjectError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/projects/{project_id}/counts")
async def project_counts(project_id: str = Depends(alive_project)):
    """Conteos de activos del proyecto (diálogo «Delete project…», doc 75 D5)."""
    return ok(await service.scope_counts(project_id))


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


@router.put("/subject-areas/{sa_id}/views")
async def set_views(sa_id: str, body: ViewsBody):
    """Doc 70: membresía de vistas del canvas (lista completa; camino DIRECTO
    sin changeset — el front en edición registra el doc completo al draft)."""
    return ok(_found(await service.set_views(sa_id, body.viewIds), "Canvas"))


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
async def diagram(sa_id: str, changesetId: str | None = Query(default=None),
                  principal: Principal = Depends(current_principal)):
    await ensure_changeset_visible(changesetId, principal)   # doc 70 §12
    """Diagrama del canvas en una request. Con `changesetId`, el overlay del
    working copy se computa server-side (modo edición/visor)."""
    return ok(_found(await service.diagram(sa_id, changeset_id=changesetId), "Canvas"))
