"""Visibilidad de versiones en el VISOR del canvas (doc 70 §12).

Regla del owner: en el canvas se pueden abrir las versiones PUBLICADAS (pasadas
y actual) y las PROPIAS (draft / en revisión / rechazada); las de otros usuarios
sólo con `versions.view_all` (administrador). Un revisor ASIGNADO también puede
abrir el request que le toca revisar (es el flujo del Review). La metadata de
una versión (owner, aprobaciones, fechas, comparación) NO se restringe: sólo
el CONTENIDO del modelo (diagrama, effective, links, búsqueda, usage).

El rollback no necesita restricción extra: crea un draft que debe aprobarse.
"""
from __future__ import annotations

from fastapi import HTTPException, status

from app.core.identity import Principal
from app.features.auth import service as auth_service

from . import asof, repository

VIEW_ALL = "versions.view_all"
FORBIDDEN_DETAIL = ("Only published versions and your own drafts can be opened in the canvas. "
                    "Administrators can open any version.")


def is_published(cs: dict) -> bool:
    """Publicada = aprobada Y aplicada (misma regla que `current_production`)."""
    return cs.get("status") == "approved" and bool(cs.get("appliedAt"))


def can_view(cs: dict | None, username: str | None, perms: dict | None) -> bool:
    """¿`username` puede abrir el contenido de la versión `cs`? Puro.
    Inexistente → True (que el endpoint responda 404/vacío como siempre)."""
    if cs is None or is_published(cs):
        return True
    if username and (username == cs.get("owner") or username in (cs.get("reviewers") or [])):
        return True
    perms = perms or {}
    # admin.manage implica view_all: las matrices sembradas antes del doc 70 no
    # traen la key nueva y el administrador no debe perder visibilidad.
    return bool(perms.get(VIEW_ALL) or perms.get("admin.manage"))


async def ensure_changeset_visible(cs_id: str | None, principal: Principal) -> None:
    """Dependencia de los endpoints changeset-aware de LECTURA: 403 si el
    changeset es una versión no publicada de OTRO usuario y el actor no tiene
    `versions.view_all`. Sin changeset o snapshot `asof:` (publicada) → pasa."""
    if not cs_id or asof.asof_version_id(cs_id):
        return
    cs = await repository.get(cs_id)
    if cs is None or is_published(cs):
        return
    user = await auth_service.resolve_session_user(principal.username)
    if not can_view(cs, principal.username, (user or {}).get("permissions")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=FORBIDDEN_DETAIL)
