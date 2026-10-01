"""Endpoints de identidad.

GET /api/users → usuarios activos (`{id, name, initials}`) para asignar
                 revisores (fallback a la lista simulada en dev sin seed);
                 con `includeDisabled=true` también los deshabilitados
                 (`disabled: true`), SÓLO para resolver nombres (doc 105).
(`GET /api/me` se retiró en el doc 75: duplicaba `GET /api/auth/me`.)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.api.envelope import ok
from app.core.identity import Principal, current_principal
from app.features.auth import repository as auth_repo

from .users import _initials, list_users

router = APIRouter(prefix="/api", tags=["identity"])


@router.get("/users", summary="Usuarios reales (id/name/initials) para asignar revisores.")
async def users(can: str | None = Query(
        default=None, description="filtra por permiso del ROL (p.ej. review.decide)"),
        includeDisabled: bool = Query(
            default=False, description="suma los deshabilitados (disabled: true) para resolver nombres; "
                                       "se ignora con `can`"),
        principal: Principal = Depends(current_principal)):
    """Usuarios ACTIVOS de la plataforma (colección `users`), no la lista fija
    simulada — así el selector de revisores ve a todos los usuarios existentes.
    Con `can`, sólo usuarios cuyo rol otorga ese permiso — el selector de
    revisores usa `can=review.decide`: asignar a alguien que nunca podría
    votar dejaba el request trabado (la unanimidad jamás se cumplía).
    Fallback a la lista simulada si aún no hay usuarios (dev sin seed).
    Requiere sesión (doc 38): la whitelist es un directorio de correos y no
    debe ser enumerable de forma anónima.

    Doc 105: `includeDisabled=true` es para RESOLVER NOMBRES (quien dejó la
    empresa sigue siendo el dueño o autor de su versión y debe verse con su
    nombre, no con su correo). Con `can` se ignora: a quien está deshabilitado
    nunca se le ofrece revisar ni recibir una versión."""
    try:
        real = await auth_repo.list_users()
    except Exception:  # noqa: BLE001 — sin DB (p.ej. tests) → fallback simulados
        real = []
    if can and real:
        perms_by_role = {r["id"]: (r.get("permissions") or {})
                         for r in await auth_repo.list_roles()}
        real = [u for u in real
                if perms_by_role.get(u.get("role") or "", {}).get(can)]
    with_disabled = includeDisabled and not can
    out = [
        {"id": u["id"], "name": u.get("name") or u["id"],
         "initials": u.get("initials") or _initials(u.get("name") or u["id"]),
         **({"disabled": True} if u.get("status") == "disabled" else {})}
        for u in real if with_disabled or u.get("status") != "disabled"
    ]
    # Con filtro `can`, una lista vacía es una respuesta VÁLIDA (no caer a los
    # simulados: ninguno podría votar de verdad).
    return ok(out if (can or out) else list_users())
