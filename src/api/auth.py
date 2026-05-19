"""JWT sign/verify, password hashing, and bootstrap admin.

Mirrors:
- web-data-model-hub/src/server/auth/jwt.ts
- web-data-model-hub/src/server/auth/password.ts
- web-data-model-hub/src/server/auth/bootstrap.ts

HS256, 10-hour lifetime, AUTH_SECRET env var with dev fallback.
Cookie name: modeler-auth.
Bootstrap admin: admin / dogadmin2019 (idempotent, first-startup only).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from src.db.db_models import UserDoc
from src.db.users_db import create_user, get_user_by_username

TOKEN_LIFETIME_SECONDS = 10 * 60 * 60  # 10 hours
JWT_ALG = "HS256"
AUTH_COOKIE_NAME = "modeler-auth"
_BCRYPT_ROUNDS = 10

_bootstrap_done = False


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


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


async def ensure_bootstrap_admin() -> None:
    """Creates admin/dogadmin2019 if no admin exists. Idempotent, swallows errors."""
    global _bootstrap_done
    if _bootstrap_done:
        return
    try:
        existing = await get_user_by_username("admin")
        if not existing:
            now = datetime.now(timezone.utc).isoformat()
            admin = UserDoc(
                id=str(uuid.uuid4()),
                username="admin",
                passwordHash=hash_password("dogadmin2019"),
                role="admin",
                isActive=True,
                permissions=[],
                createdAt=now,
                statusUpdatedAt=now,
            )
            await create_user(admin)
        _bootstrap_done = True
    except Exception:  # noqa: BLE001
        pass
