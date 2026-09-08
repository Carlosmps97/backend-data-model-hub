"""Endpoints de `schemas` (entidad esquema de BD, doc 18) — POR PROYECTO (doc 75 D6).

Listar/crear cuelgan del proyecto (`/api/projects/{project_id}/schemas`); editar/
borrar van por id (`/api/schemas/{sid}`, el esquema ya sabe su proyecto). El GET
es lectura para cualquier sesión válida (`write_guard` gatea por método): lo
consumen los dropdowns de esquema y el Explorer, incluso con rol lector. Las
escrituras exigen `model.edit` y son el camino SIN changeset — en sesión de
edición el front escribe SIEMPRE vía changeset (recordChange / endpoints de
rename-delete del changeset)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.api.envelope import ok
from app.features.auth.deps import write_guard
from app.features.projects.deps import alive_project

from . import service
from .schemas import SchemaBody
from .service import DuplicateSchemaError, InvalidSchemaNameError

router = APIRouter(prefix="/api/projects/{project_id}/schemas", tags=["schemas"],
                   dependencies=[Depends(write_guard("model.edit")), Depends(alive_project)])
router_by_id = APIRouter(prefix="/api/schemas", tags=["schemas"],
                         dependencies=[Depends(write_guard("model.edit"))])


@router.get("")
async def list_schemas(project_id: str):
    return ok(await service.list_schemas(project_id))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_schema(project_id: str, body: SchemaBody):
    try:
        return ok(await service.create_schema(project_id, body))
    except InvalidSchemaNameError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DuplicateSchemaError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router_by_id.patch("/{sid}")
async def update_schema(sid: str, body: SchemaBody):
    try:
        res = await service.update_schema(sid, body)
    except InvalidSchemaNameError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DuplicateSchemaError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if res is None:
        raise HTTPException(status_code=404, detail="Schema not found.")
    return ok(res)


@router_by_id.delete("/{sid}")
async def delete_schema(sid: str):
    res = await service.delete_schema(sid)
    if res == "in-use":
        raise HTTPException(status_code=409,
                            detail="The schema has tables or views: it can't be deleted.")
    if res is False:
        raise HTTPException(status_code=404, detail="Schema not found.")
    return ok({"deleted": True})
