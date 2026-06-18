"""Lógica de negocio de `auth`: login (bootstrap + credenciales + token).

El router solo traduce a HTTP (cookie + shaping de la respuesta).
"""

from __future__ import annotations

from app.core.security.jwt import sign_token
from app.core.security.passwords import verify_password
from app.features.users import UserDoc, get_user_by_username, mark_login_success

from .bootstrap import ensure_bootstrap_admin


class AuthError(Exception):
    """Credenciales inválidas o usuario deshabilitado (el router → 401)."""


def public_user_payload(user: UserDoc) -> dict:
    """Payload saneado del usuario — nunca incluye el hash de contraseña."""
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


async def login(username: str, password: str) -> tuple[UserDoc, str]:
    """Autentica y devuelve (usuario, token). Levanta `AuthError` si falla.

    Hace bootstrap del admin por defecto en el primer login para no requerir
    un script de seed.
    """
    await ensure_bootstrap_admin()

    record = await get_user_by_username(username.strip())
    if not record:
        raise AuthError("Invalid credentials.")
    if not record.isActive:
        raise AuthError("This user is disabled. Contact an administrator.")
    if not verify_password(password, record.passwordHash):
        raise AuthError("Invalid credentials.")

    token = sign_token(record.id, record.username, record.role)
    # Auditoría best-effort — un error de DB acá no debe romper el login.
    await mark_login_success(record.id)
    return record, token
