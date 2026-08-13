"""Negocio de auth: autenticación (login) y resolución de usuario+permisos.

`effective_permissions` es PURO (testeable sin DB): a partir del rol resuelve el
mapa de permisos efectivo. El resto orquesta repository + security + audit.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.core.audit import audit
from app.core.security import create_access_token, hash_password, verify_password

from . import repository, sso
from .models import PERMISSIONS

# Hash real (bcrypt) para verificar SIEMPRE aunque el usuario no exista: evita el
# oráculo de timing (usuario inexistente respondía más rápido → enumeración).
_DUMMY_HASH = hash_password("__no_such_user__")

# Lockout: tras N fallos consecutivos la cuenta se bloquea M minutos (complementa
# el rate limiting por IP contra ataques distribuidos / botnets).
_LOCK_THRESHOLD = 8
_LOCK_MINUTES = 15


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
    Audita `login` / `login_failed` / `login_locked`. Verifica el hash SIEMPRE
    (con un dummy si el usuario no existe) para no filtrar por timing qué usuarios
    existen. Aplica lockout tras `_LOCK_THRESHOLD` fallos consecutivos."""
    record = await repository.get_login_record(username)
    hashed = (record or {}).get("passwordHash") or _DUMMY_HASH
    # Cuenta bloqueada por intentos fallidos → rechazar sin más (comparación de
    # ISO-8601 UTC, lexicográficamente correcta con el mismo formato).
    locked_until = (record or {}).get("lockedUntil")
    if record and locked_until and locked_until > datetime.now(timezone.utc).isoformat():
        await audit(username, "login_locked")
        return None
    ok = verify_password(password, hashed)
    if not record or record.get("status") == "disabled" or not ok:
        # Sólo contamos fallos de cuentas EXISTENTES y activas (no se crea doc
        # para usuarios inexistentes → sin oráculo de existencia por lockout).
        if record and record.get("status") != "disabled":
            await repository.register_failed_login(username, _LOCK_THRESHOLD, _LOCK_MINUTES)
        await audit(username, "login_failed")
        return None
    await repository.clear_failed_login(username)
    user = await resolve_session_user(username)
    token = create_access_token(
        username, extra={"email": user.get("email"), "name": user.get("name"), "role": user.get("role")}
    )
    await audit(username, "login")
    return {"token": token, "user": user}


async def login_sso(identity: sso.SsoIdentity) -> dict | None:
    """Login por identidad SSO ya atestada (doc 38). El correo ES el username:
    entra solo si está en la whitelist (colección `users`) con estado activo —
    es decir, si un admin le asignó una matriz de rol. Devuelve `{token, user}`
    (misma shape que `login`) o None si no tiene acceso.

    El nombre se resuelve best-effort (SCIM → hint → local-part del correo) y
    se persiste SOLO si el doc no traía uno (el admin puede pisarlo después).
    Audita `login_sso` / `login_sso_denied` (nunca `login_failed`: acá no hay
    credenciales que fallen, hay correos sin asignación)."""
    email = identity.email
    user = await repository.get_user(email)
    if not user or user.get("status") == "disabled":
        await audit(email, "login_sso_denied",
                    meta={"reason": "disabled" if user else "not_whitelisted"})
        return None
    fixes: dict = {}
    if not (user.get("email") or "").strip():
        fixes["email"] = email
    if not (user.get("name") or "").strip():
        name = await sso.resolve_display_name(email, identity.username_hint)
        # Misma derivación de iniciales que usa el módulo Admin.
        from app.features.admin.service import initials
        fixes["name"] = name
        fixes["initials"] = initials(name)
    if fixes:
        await repository.upsert_user(email, fixes)
    su = await resolve_session_user(email)
    if su is None:
        await audit(email, "login_sso_denied", meta={"reason": "disabled"})
        return None
    token = create_access_token(
        email, extra={"email": su.get("email") or email, "name": su.get("name"),
                      "role": su.get("role")}
    )
    await audit(email, "login_sso",
                meta={"userId": identity.user_id} if identity.user_id else None)
    return {"token": token, "user": su}


async def logout(username: str) -> None:
    await audit(username, "logout")
