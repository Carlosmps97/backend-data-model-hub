"""Plantillas de hoja Excel del Reporting (doc 95 D11) — permisos de los saved
reports (spec D11): cualquiera en sesión ve las suyas + las compartidas y crea
las suyas; edita y borra el dueño, y un admin (`admin.manage`) también las
compartidas. Proyecto vivo:

  GET    /api/projects/{pid}/sheet-templates              lista (por nombre)
  POST   /api/projects/{pid}/sheet-templates              201 · 409 nombre · 422 forma
  POST   …/sheet-templates/default                        201 · 409 si ya tiene «QA_MODELO»
  PUT    …/sheet-templates/{template_id}                  404 (no existe o no es tuya) · 409 · 422
  DELETE …/sheet-templates/{template_id}                  404 (no existe o no es tuya)

La ruta fija `/default` va ANTES de `/{template_id}`."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.api.envelope import ok
from app.core.identity import Principal, current_principal
from app.features.auth.deps import optional_session_user
from app.features.projects.deps import alive_project

from . import service
from .models import SheetTemplateBody

router = APIRouter(prefix="/api/projects/{project_id}/sheet-templates", tags=["sheet-templates"],
                   dependencies=[Depends(alive_project)])


async def _actor(principal: Principal = Depends(current_principal),
                 user: dict | None = Depends(optional_session_user)) -> tuple[str, bool]:
    """Quién escribe y si es admin (gobierna las plantillas compartidas)."""
    return principal.username, bool(user and user["permissions"].get("admin.manage"))


def _not_found():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                        detail="Sheet template not found (or it isn't yours).")


async def _write(coro):
    """Nombre repetido en el proyecto → 409."""
    try:
        return await coro
    except service.TemplateNameTaken as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="There is already a sheet template with that name in this project.") from exc


@router.get("")
async def list_templates(project_id: str, principal: Principal = Depends(current_principal)):
    return ok(await service.list_templates(project_id, principal.username))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_template(project_id: str, body: SheetTemplateBody, actor: tuple[str, bool] = Depends(_actor)):
    return ok(await _write(service.create_template(actor[0], project_id, body)))


@router.post("/default", status_code=status.HTTP_201_CREATED)
async def create_default(project_id: str, actor: tuple[str, bool] = Depends(_actor)):
    doc = await _write(service.create_default(actor[0], project_id))
    if doc is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This project already has the QA_MODELO template.")
    return ok(doc)


@router.put("/{template_id}")
async def update_template(project_id: str, template_id: str, body: SheetTemplateBody,
                          actor: tuple[str, bool] = Depends(_actor)):
    return ok(await _write(service.update_template(actor[0], actor[1], project_id, template_id, body))
              or _not_found())


@router.delete("/{template_id}")
async def delete_template(project_id: str, template_id: str, actor: tuple[str, bool] = Depends(_actor)):
    if not await service.delete_template(actor[0], actor[1], project_id, template_id):
        _not_found()
    return ok({"deleted": True})
