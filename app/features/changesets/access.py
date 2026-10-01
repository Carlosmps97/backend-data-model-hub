"""Visibilidad de versiones en el VISOR del canvas (doc 70 §12) y quién las
ADMINISTRA (doc 104).

Regla del owner: en el canvas se pueden abrir las versiones PUBLICADAS (pasadas
y actual) y las PROPIAS (draft / en revisión / rechazada); las de otros usuarios
sólo con `versions.view_all` (administrador). Un revisor ASIGNADO también puede
abrir el request que le toca revisar (es el flujo del Review). La metadata de
una versión (owner, aprobaciones, fechas, comparación) NO se restringe: sólo
el CONTENIDO del modelo (diagrama, effective, links, búsqueda, usage).

El rollback no necesita restricción extra: crea un draft que debe aprobarse.

Doc 104: transferir o eliminar un draft lo hace su owner (si su rol todavía
edita modelos) o un administrador (`admin.manage`) — administrar no es editar:
el administrador sigue sin poder escribir en un draft ajeno, salvo que se lo
transfiera.
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


def can_manage(cs: dict | None, username: str | None, perms: dict | None) -> bool:
    """Doc 104: ¿`username` puede transferir o eliminar la versión `cs`? Puro.
    Su owner mientras su rol edite modelos, o un administrador. El ESTADO
    (sólo drafts) lo decide el service; esto es sólo el quién."""
    if cs is None or not username:
        return False
    perms = perms or {}
    if perms.get("admin.manage"):
        return True
    return username == cs.get("owner") and bool(perms.get("model.edit"))


GONE_DETAIL = "This version no longer exists — it may have been deleted. Open another version."


async def ensure_changeset_exists(cs_id: str | None) -> dict | None:
    """Doc 104: 404 si `cs_id` (real, no `asof:`) ya no existe — un draft
    ELIMINADO. Antes la lectura caía en silencio a producción (o a `null`) y el
    canvas que lo tuviera abierto mostraba otra cosa sin avisar. Devuelve la
    cabecera (None sin changeset o con `asof:`)."""
    if not cs_id or asof.asof_version_id(cs_id):
        return None
    cs = await repository.get(cs_id)
    if cs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=GONE_DETAIL)
    return cs


async def ensure_changeset_visible(cs_id: str | None, principal: Principal) -> None:
    """Dependencia de los endpoints changeset-aware de LECTURA: 403 si el
    changeset es una versión no publicada de OTRO usuario y el actor no tiene
    `versions.view_all`. Sin changeset o snapshot `asof:` (publicada) → pasa;
    inexistente → 404 (`ensure_changeset_exists`)."""
    cs = await ensure_changeset_exists(cs_id)
    if cs is None or is_published(cs):
        return
    user = await auth_service.resolve_session_user(principal.username)
    if not can_view(cs, principal.username, (user or {}).get("permissions")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=FORBIDDEN_DETAIL)
