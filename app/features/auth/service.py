"""Negocio de auth: autenticación (login) y resolución de usuario+permisos.

`effective_permissions` es PURO (testeable sin DB): a partir del rol resuelve el
mapa de permisos efectivo. El resto orquesta repository + security + audit.
"""
from __future__ import annotations

from app.core.audit import audit
from app.core.security import create_access_token, hash_password, verify_password

from . import repository
from .models import PERMISSIONS

# Hash real (bcrypt) para verificar SIEMPRE aunque el usuario no exista: evita el
# oráculo de timing (usuario inexistente respondía más rápido → enumeración).
_DUMMY_HASH = hash_password("__no_such_user__")


def effective_permissions(role: dict | None) -> dict[str, bool]:
    """Mapa `permiso → bool` para TODOS los permisos del catálogo (los que el rol
    no declara quedan en False). Filtra keys desconocidas del rol. Puro."""
    granted = (role or {}).get("permissions") or {}
    return {p: bool(granted.get(p, False)) for p in PERMISSIONS}


def access_level(perms: dict[str, bool]) -> str:
    """Etiqueta legible (columna 'Nivel de acceso' del mock). Derivada. Pura."""
    if perms.get("admin.manage"):
        return "full"
    if perms.get("model.edit") or perms.get("standards.edit") or perms.get("publish"):
        return "edit"
    return "read"


async def resolve_session_user(username: str) -> dict | None:
    """Usuario en sesión enriquecido con rol + permisos efectivos (para /me y el
    gating del frontend). None si el usuario no existe o está **deshabilitado**
    (deshabilitar en Admin revoca la sesión activa, no solo el login futuro).
    Nunca incluye `passwordHash`."""
    user = await repository.get_user(username)
    if not user or user.get("status") == "disabled":
        return None
    role = await repository.get_role(user.get("role") or "")
    perms = effective_permissions(role)
    return {
        "username": user["id"],
        "email": user.get("email"),
        "name": user.get("name"),
        "role": user.get("role"),
        "roleName": (role or {}).get("name"),
        "projectIds": user.get("projectIds", []),
        "status": user.get("status", "active"),
        "initials": user.get("initials"),
        "permissions": perms,
        "accessLevel": access_level(perms),
    }


async def has_permission(username: str, perm: str) -> bool:
    """True si el usuario tiene el permiso (rol → matriz). Usado por
    `require_permission`."""
    su = await resolve_session_user(username)
    return bool(su and su["permissions"].get(perm))


async def login(username: str, password: str) -> dict | None:
    """Verifica credenciales; devuelve `{token, user}` o None si fallan.
    Audita `login` / `login_failed`. Verifica el hash SIEMPRE (con un dummy si el
    usuario no existe) para no filtrar por timing qué usuarios existen."""
    record = await repository.get_user(username, with_hash=True)
    hashed = (record or {}).get("passwordHash") or _DUMMY_HASH
    ok = verify_password(password, hashed)
    if not record or record.get("status") == "disabled" or not ok:
        await audit(username, "login_failed")
        return None
    user = await resolve_session_user(username)
    token = create_access_token(
        username, extra={"email": user.get("email"), "name": user.get("name"), "role": user.get("role")}
    )
    await audit(username, "login")
    return {"token": token, "user": user}


async def logout(username: str) -> None:
    await audit(username, "logout")
