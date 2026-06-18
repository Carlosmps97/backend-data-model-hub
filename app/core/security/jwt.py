"""Firma/verificación de JWT y nombre de cookie de sesión.

Espeja `web-data-model-hub/src/server/auth/jwt.ts`. HS256, vida de 10 horas,
`AUTH_SECRET` desde env con fallback de dev. Cookie: `modeler-auth`.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import jwt

TOKEN_LIFETIME_SECONDS = 10 * 60 * 60  # 10 hours
JWT_ALG = "HS256"
AUTH_COOKIE_NAME = "modeler-auth"


def _get_secret() -> str:
    return os.getenv(
        "AUTH_SECRET",
        "dev-only-secret-please-override-in-production-via-AUTH_SECRET-env",
    )


def sign_token(user_id: str, username: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(seconds=TOKEN_LIFETIME_SECONDS),
    }
    return jwt.encode(payload, _get_secret(), algorithm=JWT_ALG)


def verify_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, _get_secret(), algorithms=[JWT_ALG])
        if not payload.get("sub"):
            return None
        return payload
    except jwt.PyJWTError:
        return None
