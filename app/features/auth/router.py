"""Endpoints de auth propia (login usuario/contraseña + sesión por token).

  POST /api/auth/login   {username, password} → {token, user}   (401 si falla)
  POST /api/auth/logout                        → {ok}            (audita)
  GET  /api/auth/me                            → usuario en sesión enriquecido
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core.api.envelope import ok
from app.core.identity import Principal, current_principal
from app.core.ratelimit import limiter

from . import service
from .schemas import LoginBody

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
@limiter.limit("5/minute")  # anti fuerza bruta / credential stuffing (por IP)
async def login(request: Request, response: Response, body: LoginBody):
    # `response` es OBLIGATORIA con el limiter ACTIVO (producción): slowapi
    # inyecta ahí los headers X-RateLimit-* cuando el endpoint devuelve un
    # dict; sin ella revienta con "parameter `response` must be an instance
    # of starlette.responses.Response" → 500 en el primer login real (visto
    # en Apps 2026-07-20; en dev el limiter está OFF y nunca se manifestó).
    result = await service.login(body.username.strip(), body.password)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
        )
    return ok(result)


@router.post("/logout")
async def logout(principal: Principal = Depends(current_principal)):
    await service.logout(principal.username)
    return ok({"ok": True})


@router.get("/me")
async def me(principal: Principal = Depends(current_principal)):
    """Usuario en sesión + rol + permisos efectivos (para el gating del front).
    Si el actor no está registrado (seam de dev), degrada al Principal básico."""
    user = await service.resolve_session_user(principal.username)
    return ok(user if user is not None else principal.model_dump())
