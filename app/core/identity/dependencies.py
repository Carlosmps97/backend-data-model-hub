"""Selección del provider de identidad y dependencia FastAPI.

Auth PROPIA (2026-07-04): la identidad sale del **token de sesión** (JWT HS256)
cuando viene en `Authorization: Bearer …`. Si no hay token:
- `REQUIRE_AUTH=True` (producción) → 401 (login obligatorio);
- si no (local/tests) → fallback al seam de identidad (`AUTH_MODE`: X-Dev-User /
  headers OBO / usuario fake), para no exigir login al desarrollar ni en los
  tests (que no montan DB).

Un token PRESENTE pero inválido/expirado siempre es 401 (el cliente mandó una
sesión: no se cae al fallback, que sería un agujero de seguridad).
"""
from __future__ import annotations

from fastapi import HTTPException, status
from starlette.requests import Request

from app.core.config import settings
from app.core.security import decode_access_token

from .models import Principal
from .provider import (
    DatabricksIdentityProvider,
    IdentityProvider,
    LocalIdentityProvider,
)


def get_identity_provider() -> IdentityProvider:
    if settings.AUTH_MODE == "databricks":
        return DatabricksIdentityProvider()
    return LocalIdentityProvider(
        email=settings.LOCAL_DEV_USER,
        username=settings.LOCAL_DEV_USERNAME or None,
        display_name=settings.LOCAL_DEV_DISPLAY_NAME or None,
    )


def bearer_token(request: Request) -> str | None:
    """Extrae el token de sesión del request. Fuentes, en orden:

    1. `Authorization: Bearer <token>` — dev local, curl, tests.
    2. `X-Session-Token: <token>` — producción en Databricks Apps: el proxy
       SSO de la plataforma CONSUME el header `Authorization` (es su propio
       carril de auth programática) y el backend jamás lo recibe — verificado
       2026-07-20: el navegador enviaba el Bearer y FastAPI lo veía ausente
       (401 "Authentication required" con token válido en vuelo). Por eso el
       front manda el token de sesión en un header propio.
    """
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return token
    return (request.headers.get("X-Session-Token") or "").strip() or None


def _principal_from_claims(claims: dict) -> Principal:
    sub = claims["sub"]
    return Principal(
        email=claims.get("email", ""),
        username=sub,
        display_name=claims.get("name") or sub,
        source="session",
    )


def current_principal(request: Request) -> Principal:
    """Dependencia FastAPI: el usuario en sesión.

    Token-first; si no hay token, fallback al seam salvo `REQUIRE_AUTH`."""
    token = bearer_token(request)
    if token is not None:
        claims = decode_access_token(token)
        if claims and claims.get("sub"):
            return _principal_from_claims(claims)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session. Sign in again.",
        )
    if settings.REQUIRE_AUTH:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Sign in.",
        )
    return get_identity_provider().principal_from_request(request)
