"""Endpoints de auth propia (sesión por token; dos formas de nacer la sesión).

  POST /api/auth/sso/login                     → {token, user}   (doc 38: SSO
                                                 heredado de Databricks Apps;
                                                 403 si el correo no está
                                                 whitelisteado en un rol)
  POST /api/auth/login   {username, password} → {token, user}   (401 si falla;
                                                 carril del administrador)
  POST /api/auth/logout                        → {ok}            (audita)
  GET  /api/auth/me                            → usuario en sesión enriquecido
  GET  /api/auth/warmup/{next_b64}             → 302 a `next`    (doc 36)
"""
from __future__ import annotations

import base64
import binascii
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from app.core.api.envelope import ok
from app.core.config import settings
from app.core.identity import Principal, current_principal
from app.core.ratelimit import limiter

from . import service, sso
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


@router.post("/sso/login")
@limiter.limit("10/minute")  # el secreto del relay no debe ser brute-forceable
async def sso_login(request: Request, response: Response):
    """Login con la identidad heredada del SSO de Databricks Apps (doc 38).

    Sin body: la identidad viene en los headers de relay `x-dmh-sso-*` que el
    server del front adjunta (autenticados con `x-dmh-proxy-secret`). El correo
    debe estar asignado a una matriz de rol (whitelist del módulo Admin); si no
    lo está, 403 con mensaje accionable. `response` es obligatoria por slowapi
    (mismo motivo que /login)."""
    identity = sso.resolve_sso_identity(request)  # 401/503 si no hay identidad
    result = await service.login_sso(identity)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has no access. Ask an administrator to "
                   "assign your email to a role.",
        )
    return ok(result)


def warmup_next_allowed(next_url: str) -> bool:
    """¿`next` apunta a un origen del FRONT permitido? Mismo allowlist que el
    CORS (lista exacta + regex, `fullmatch` igual que Starlette): anti
    open-redirect. Puro, para testear sin app."""
    try:
        parts = urlsplit(next_url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin in settings.CORS_ORIGINS:
        return True
    if settings.CORS_ORIGIN_REGEX:
        try:
            return re.fullmatch(settings.CORS_ORIGIN_REGEX, origin) is not None
        except re.error:
            return False
    return False


@router.get("/warmup/{next_b64}")
async def warmup(next_b64: str):
    """Warm-up de sesión para Databricks Apps (doc 36). Cada app vive detrás
    de su PROPIO muro SSO por subdominio; un fetch() del front no puede
    completar ese SSO (redirect cross-origin a Microsoft = imposible en XHR).
    El front navega ACÁ top-level: el proxy de Databricks hace el SSO de este
    dominio, la cookie queda puesta y este endpoint solo rebota de vuelta al
    front. Público a propósito (como /login); `next` validado contra el
    allowlist CORS para no ser un open redirect.

    `next` viaja EN EL PATH (base64url), NO como query string: el replay
    post-SSO del proxy de Databricks se atora con query strings (el usuario
    quedaba varado en un `{}` del proxy — visto 2026-07-30 en corporativo;
    con URL sin query, p.ej. /api/health, el replay siempre llega a la app)."""
    try:
        pad = "=" * (-len(next_b64) % 4)
        next_url = base64.urlsafe_b64decode(next_b64 + pad).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid next URL.")
    if not warmup_next_allowed(next_url):
        raise HTTPException(status_code=400, detail="Invalid next URL.")
    return RedirectResponse(next_url, status_code=302,
                            headers={"Cache-Control": "no-store"})


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
