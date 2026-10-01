"""Dependencias de autorización reutilizables por otras features.

`require_permission("<perm>")` = dependency FastAPI que exige que el usuario en
sesión tenga el permiso (rol → matriz). Devuelve el usuario en sesión
enriquecido (para que el endpoint lo use / audite). 403 si falta el permiso;
401 lo levanta antes `current_principal` si no hay sesión válida.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.core.audit import audit
from app.core.identity import Principal, current_principal

from . import service

# Sufijos de escrituras de ALTA FRECUENCIA (guardados del canvas al arrastrar):
# no se auditan para no inundar el log. Desde el doc 105 esas rutas directas
# responden 409 antes de llegar a la auditoría (`versioned=True`); el filtro
# queda por si un router no versionado vuelve a exponerlas.
_NO_AUDIT = ("/layout", "/drawings", "/tables")

# Doc 105: el modelo se edita SIEMPRE por una versión (changesets → revisión →
# publish). Escribirlo directo a producción quedó cerrado.
VERSION_REQUIRED = "This change requires a version in edit mode."


def write_guard(perm: str, *, versioned: bool = False, direct_ok: frozenset[tuple[str, str]] = frozenset()):
    """Dependency de ROUTER: gatea por método. Lecturas (GET/HEAD/OPTIONS) sólo
    exigen sesión válida (`current_principal` → 401 en prod sin token); escrituras
    (POST/PUT/PATCH/DELETE) exigen `perm` (403 si falta o el usuario está
    deshabilitado). Se monta con `dependencies=[Depends(write_guard(...))]` para
    cerrar de una TODOS los endpoints directos de un router — que antes no tenían
    ninguna dependencia de auth (aceptaban requests anónimos).

    Doc 105 — `versioned=True`: el router escribe colecciones DEL MODELO; con el
    permiso, una escritura directa a producción responde 409 (VERSION_REQUIRED)
    — sin revisión, sin la unicidad del changeset, cruzando proyectos y sin
    auditoría eran su costo. Va después del permiso (sin él sigue siendo 403) y
    antes de la auditoría (un intento rechazado no es una acción). `direct_ok`:
    rutas (método, plantilla) que siguen directas por diseño."""
    async def _dep(request: Request, principal: Principal = Depends(current_principal)) -> dict | None:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return None
        user = await service.resolve_session_user(principal.username)
        if user is None or not user["permissions"].get(perm):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"You don't have permission for this action ({perm}).",
            )
        route = getattr(request.scope.get("route"), "path", request.url.path)
        if versioned and (request.method, route) not in direct_ok:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=VERSION_REQUIRED)
        # Auditoría de la acción (quién hizo qué): actor + método + ruta, para las
        # métricas de adopción. Best-effort; se saltan los guardados ruidosos.
        path = request.url.path
        if not any(path.endswith(sfx) for sfx in _NO_AUDIT):
            await audit(user["username"], f"{request.method.lower()} {path}", target_type="api")
        return user

    return _dep


def require_permission(perm: str):
    async def _dep(principal: Principal = Depends(current_principal)) -> dict:
        user = await service.resolve_session_user(principal.username)
        if user is None or not user["permissions"].get(perm):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"You don't have permission for this action ({perm}).",
            )
        return user

    return _dep


async def optional_session_user(principal: Principal = Depends(current_principal)) -> dict | None:
    """Usuario en sesión enriquecido, o None si no está registrado (p.ej. actor
    del seam en dev). No exige permiso — para endpoints que sólo lo usan si existe."""
    return await service.resolve_session_user(principal.username)
