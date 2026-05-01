"""Auth endpoints.

POST /api/auth/login   — exchange credentials for a session cookie
POST /api/auth/logout  — clear the session cookie
GET  /api/auth/me      — return current authenticated user

Mirrors web-data-model-hub/src/app/api/auth/*/route.ts.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.api.auth import (
    AUTH_COOKIE_NAME,
    TOKEN_LIFETIME_SECONDS,
    ensure_bootstrap_admin,
    sign_token,
    verify_password,
)
from src.api.dependencies import CurrentUserDep
from src.db.db_models import UserDoc
from src.db.users_db import get_user_by_username, mark_login_success

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


def _user_payload(user: UserDoc) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "isActive": user.isActive,
        "permissions": [p.model_dump() for p in user.permissions],
        "createdAt": user.createdAt,
        "statusUpdatedAt": user.statusUpdatedAt,
        "lastLoginAt": user.lastLoginAt,
    }


@router.post("/login")
async def login(body: LoginRequest, response: Response):
    # Bootstrap default admin on first login so no seed script is needed.
    await ensure_bootstrap_admin()

    record = await get_user_by_username(body.username.strip())
    if not record:
        return JSONResponse(
            {"success": False, "error": "Invalid credentials."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if not record.isActive:
        return JSONResponse(
            {"success": False, "error": "This user is disabled. Contact an administrator."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if not verify_password(body.password, record.passwordHash):
        return JSONResponse(
            {"success": False, "error": "Invalid credentials."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    token = sign_token(record.id, record.username, record.role)

    # Fire-and-forget audit write — a DB error here must not fail the login.
    await mark_login_success(record.id)

    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=TOKEN_LIFETIME_SECONDS,
    )
    return {"success": True, "data": _user_payload(record)}


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(response: Response):
    response.delete_cookie(key=AUTH_COOKIE_NAME, path="/")
    return {"success": True}


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
    return {"success": True, "data": _user_payload(user)}
