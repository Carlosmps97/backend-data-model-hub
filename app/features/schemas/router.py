"""Endpoints de `schemas` (entidad esquema de BD, doc 18).

El GET es lectura para cualquier sesión válida (`write_guard` gatea por
método): lo consumen los dropdowns de esquema y el Database Explorer, incluso
con rol lector. Las escrituras exigen `model.edit` y son el camino SIN
changeset — en sesión de edición el front escribe SIEMPRE vía changeset
(recordChange / endpoints de rename-delete del changeset)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.api.envelope import ok
from app.features.auth.deps import write_guard

from . import service
from .schemas import SchemaBody
from .service import DuplicateSchemaError, InvalidSchemaNameError

router = APIRouter(prefix="/api/schemas", tags=["schemas"],
                   dependencies=[Depends(write_guard("model.edit"))])


@router.get("")
async def list_schemas():
    return ok(await service.list_schemas())


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_schema(body: SchemaBody):
    try:
        return ok(await service.create_schema(body))
    except InvalidSchemaNameError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DuplicateSchemaError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/{sid}")
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


@router.delete("/{sid}")
async def delete_schema(sid: str):
    res = await service.delete_schema(sid)
    if res == "in-use":
        raise HTTPException(status_code=409,
                            detail="The schema has tables or views: it can't be deleted.")
    if res is False:
        raise HTTPException(status_code=404, detail="Schema not found.")
    return ok({"deleted": True})
