"""Endpoints de identidad.

GET /api/me    → el `Principal` actual, resuelto por el seam
                 (`app.core.identity`) según `AUTH_MODE`.
GET /api/users → lista fija de usuarios simulados (`{id, name, initials}`) para
                 el switch de actor y la asignación de revisores (modo local).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.api.envelope import ok
from app.core.identity import Principal, current_principal

from .users import list_users

router = APIRouter(prefix="/api", tags=["identity"])


@router.get("/me", summary="Identidad del usuario en sesión.")
async def me(principal: Principal = Depends(current_principal)):
    return ok(principal.model_dump())


@router.get("/users", summary="Usuarios simulados (id/name/initials).")
async def users():
    return ok(list_users())
