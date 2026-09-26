"""Negocio de las plantillas de hoja Excel (doc 95 D11) con los permisos de los
saved reports (spec D11, final review #1): cada uno ve las suyas + las
compartidas del proyecto; edita y borra el dueño; un admin (`admin.manage`)
gobierna además las compartidas — así la «QA_MODELO» sembrada por el one-shot
(dueño `system`) sigue siendo dato editable. Nombre único entre las que ve
quien escribe; la built-in se siembra una sola vez por proyecto."""
from __future__ import annotations

from . import repository
from .builtin import BUILTIN_ORIGIN, QA_MODELO
from .models import SheetTemplateBody


class TemplateNameTaken(Exception):
    """Ya hay una plantilla con ese nombre entre las que ve quien escribe (409)."""


def can_edit(template: dict, username: str, is_admin: bool) -> bool:
    """El dueño edita y borra la suya; un admin, además, las compartidas. Puro
    (espejo de `canEditTemplate` del front)."""
    return template.get("owner") == username or (is_admin and template.get("shared") is True)


async def _check_name(project_id: str, username: str, name: str, exclude_id: str | None = None) -> None:
    for t in await repository.list_templates(project_id, username):
        if t["id"] != exclude_id and (t.get("name") or "").strip().lower() == name.strip().lower():
            raise TemplateNameTaken(name)


async def list_templates(project_id: str, username: str) -> list[dict]:
    return await repository.list_templates(project_id, username)


async def create_template(actor: str, project_id: str, body: SheetTemplateBody, *, origin: str = "user") -> dict:
    await _check_name(project_id, actor, body.name)
    return await repository.create_template(project_id, {**body.model_dump(), "origin": origin, "owner": actor,
                                                         "createdBy": actor, "updatedBy": actor})


async def update_template(actor: str, is_admin: bool, project_id: str, template_id: str,
                          body: SheetTemplateBody) -> dict | None:
    """None si no existe o `actor` no puede editarla. El admin que edita una
    compartida ajena no la vuelve privada (dejaría de verla todo el proyecto)."""
    current = await repository.get_template(project_id, template_id)
    if current is None or not can_edit(current, actor, is_admin):
        return None
    await _check_name(project_id, actor, body.name, exclude_id=template_id)
    data = {**body.model_dump(), "updatedBy": actor}
    if current.get("owner") != actor:
        data["shared"] = True
    return await repository.update_template(project_id, template_id, data)


async def delete_template(actor: str, is_admin: bool, project_id: str, template_id: str) -> bool:
    current = await repository.get_template(project_id, template_id)
    if current is None or not can_edit(current, actor, is_admin):
        return False
    return await repository.delete_template(project_id, template_id)


async def create_default(actor: str, project_id: str) -> dict | None:
    """Siembra «QA_MODELO» COMPARTIDA en el proyecto; None si el proyecto ya
    tiene la built-in (editada o no, la vea o no `actor`)."""
    if await repository.has_origin(project_id, BUILTIN_ORIGIN):
        return None
    body = SheetTemplateBody.model_validate({**QA_MODELO, "shared": True})
    return await create_template(actor, project_id, body, origin=BUILTIN_ORIGIN)
