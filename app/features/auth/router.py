"""Endpoints de auth.

POST /api/auth/login   — intercambia credenciales por una cookie de sesión
POST /api/auth/logout  — limpia la cookie de sesión
GET  /api/auth/me      — devuelve el usuario autenticado actual
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse

from app.core.api.envelope import ok
from app.core.security.jwt import AUTH_COOKIE_NAME, TOKEN_LIFETIME_SECONDS

from .dependencies import CurrentUserDep
from .schemas import LoginRequest
from .service import AuthError, login as login_user, public_user_payload

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
async def login(body: LoginRequest, response: Response):
    try:
        user, token = await login_user(body.username, body.password)
    except AuthError as exc:
        return JSONResponse(
            {"success": False, "error": str(exc)},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=TOKEN_LIFETIME_SECONDS,
    )
    return ok(public_user_payload(user))


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(response: Response):
    response.delete_cookie(key=AUTH_COOKIE_NAME, path="/")
    return ok(None)


@router.get("/me")
async def me(user: CurrentUserDep):
    if user is None:
        return JSONResponse(
            {"success": False, "error": "Not authenticated.", "code": "unauthenticated"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if not user.isActive:
        return JSONResponse(
            {"success": False, "error": "This user is disabled.", "code": "inactive"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return ok(public_user_payload(user))
