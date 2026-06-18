"""Hashing/verificación de contraseñas con bcrypt.

Espeja `web-data-model-hub/src/server/auth/password.ts`.
"""

from __future__ import annotations

import bcrypt

_BCRYPT_ROUNDS = 10


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())
