"""Endpoints de identidad.

GET /api/me    → el `Principal` actual, resuelto por el seam
                 (`app.core.identity`) según `AUTH_MODE`.
GET /api/users → lista fija de usuarios simulados (`{id, name, initials}`) para
                 el switch de actor y la asignación de revisores (modo local).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.api.envelope import ok
from app.core.identity import Principal, current_principal
from app.features.auth import repository as auth_repo

from .users import _initials, list_users

router = APIRouter(prefix="/api", tags=["identity"])


@router.get("/me", summary="Identidad del usuario en sesión.")
async def me(principal: Principal = Depends(current_principal)):
    return ok(principal.model_dump())


@router.get("/users", summary="Usuarios reales (id/name/initials) para asignar revisores.")
async def users(can: str | None = Query(
        default=None, description="filtra por permiso del ROL (p.ej. review.decide)")):
    """Usuarios ACTIVOS de la plataforma (colección `users`), no la lista fija
    simulada — así el selector de revisores ve a todos los usuarios existentes.
    Con `can`, sólo usuarios cuyo rol otorga ese permiso — el selector de
    revisores usa `can=review.decide`: asignar a alguien que nunca podría
    votar dejaba el request trabado (la unanimidad jamás se cumplía).
    Fallback a la lista simulada si aún no hay usuarios (dev sin seed)."""
    try:
        real = await auth_repo.list_users()
    except Exception:  # noqa: BLE001 — sin DB (p.ej. tests) → fallback simulados
        real = []
    if can and real:
        perms_by_role = {r["id"]: (r.get("permissions") or {})
                         for r in await auth_repo.list_roles()}
        real = [u for u in real
                if perms_by_role.get(u.get("role") or "", {}).get(can)]
    out = [
        {"id": u["id"], "name": u.get("name") or u["id"],
         "initials": u.get("initials") or _initials(u.get("name") or u["id"])}
        for u in real if u.get("status") != "disabled"
    ]
    # Con filtro `can`, una lista vacía es una respuesta VÁLIDA (no caer a los
    # simulados: ninguno podría votar de verdad).
    return ok(out if (can or out) else list_users())
