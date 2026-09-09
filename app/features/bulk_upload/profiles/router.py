"""Endpoints de perfiles de carga (doc 78 §7.1) — `model.edit` + proyecto vivo:

  GET    /api/projects/{pid}/upload-profiles              lista (default primero)
  POST   /api/projects/{pid}/upload-profiles              201 · 422 {problems} · 409 nombre
  GET    …/upload-profiles/catalog                        campos por hoja, tipos de regla, políticas
  POST   …/upload-profiles/validate                       {problems} sin guardar
  POST   …/upload-profiles/suggest                        mapeo sugerido por nombre
  POST   …/upload-profiles/default                        201 {profile, warnings} · 409 si ya existe
  GET/PUT/DELETE …/upload-profiles/{id}                   404 si no existe en el proyecto
  POST   …/upload-profiles/{id}/default                   marca default

Las rutas fijas van ANTES de `/{profile_id}` para que FastAPI no las tome
como ids."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.api.envelope import ok
from app.features.auth.deps import require_permission
from app.features.projects.deps import alive_project

from . import service
from .models import SHEET_ROLES, UploadProfileBody, catalog

_can_edit = require_permission("model.edit")
router = APIRouter(prefix="/api/projects/{project_id}/upload-profiles", tags=["upload-profiles"],
                   dependencies=[Depends(alive_project)])


class SuggestBody(BaseModel):
    sheet: str
    headers: list[str] = []


def _not_found():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload profile not found.")


async def _write(coro):
    """Traduce las excepciones del service a HTTP (422 con la lista de problemas / 409)."""
    try:
        return await coro
    except service.ProfileInvalid as exc:
        raise HTTPException(status_code=422,
                            detail={"message": "The profile has problems.", "problems": exc.problems}) from exc
    except service.ProfileNameTaken as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="There is already a profile with that name in this project.") from exc


@router.get("")
async def list_profiles(project_id: str, user: dict = Depends(_can_edit)):
    return ok(await service.list_profiles(project_id))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_profile(project_id: str, body: UploadProfileBody, user: dict = Depends(_can_edit)):
    return ok(await _write(service.create_profile(user["username"], project_id, body)))


@router.get("/catalog")
async def get_catalog(project_id: str, user: dict = Depends(_can_edit)):
    return ok(catalog())


@router.post("/validate")
async def validate(project_id: str, body: UploadProfileBody, user: dict = Depends(_can_edit)):
    return ok({"problems": await service.validate_body(project_id, body)})


@router.post("/suggest")
async def suggest(project_id: str, body: SuggestBody, user: dict = Depends(_can_edit)):
    if body.sheet not in SHEET_ROLES:
        raise HTTPException(status_code=422, detail="sheet must be 'tables' or 'columns'.")
    return ok(await service.suggest_headers(project_id, body.sheet, body.headers))


@router.post("/default", status_code=status.HTTP_201_CREATED)
async def create_default(project_id: str, user: dict = Depends(_can_edit)):
    res = await _write(service.create_default(user["username"], project_id))
    if res is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This project already has the default profile.")
    profile, warnings = res
    return ok({"profile": profile, "warnings": warnings})


@router.get("/{profile_id}")
async def get_profile(project_id: str, profile_id: str, user: dict = Depends(_can_edit)):
    return ok(await service.get_profile(project_id, profile_id) or _not_found())


@router.put("/{profile_id}")
async def update_profile(project_id: str, profile_id: str, body: UploadProfileBody, user: dict = Depends(_can_edit)):
    return ok(await _write(service.update_profile(user["username"], project_id, profile_id, body)) or _not_found())


@router.delete("/{profile_id}")
async def delete_profile(project_id: str, profile_id: str, user: dict = Depends(_can_edit)):
    if not await service.delete_profile(project_id, profile_id):
        _not_found()
    return ok({"deleted": True})


@router.post("/{profile_id}/default")
async def set_default(project_id: str, profile_id: str, user: dict = Depends(_can_edit)):
    return ok(await service.set_default(project_id, profile_id) or _not_found())
