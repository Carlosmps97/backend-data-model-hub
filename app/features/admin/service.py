"""Negocio del módulo Admin: usuarios + roles (RBAC data-driven).

Reutiliza el repositorio de `auth` (users/roles). `initials` y
`sanitize_permissions` son puros. Cada mutación se audita.
"""
from __future__ import annotations

from app.core.audit import audit
from app.core.security import hash_password
from app.features.auth import repository
from app.features.auth.models import PERMISSIONS


def initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def sanitize_permissions(perms: dict) -> dict[str, bool]:
    """Solo permisos conocidos, coaccionados a bool (no se persisten keys
    arbitrarias en la matriz). Puro."""
    return {p: bool((perms or {}).get(p, False)) for p in PERMISSIONS}


class AdminGuardError(ValueError):
    """Operación bloqueada para no dejar el sistema sin administrador, o por una
    referencia colgada (rol con usuarios). El router la mapea a 400."""


async def _survives_admin(hypo) -> bool:
    """¿Queda ALGÚN administrador activo si se aplica el cambio hipotético?
    `hypo(user, roles)` devuelve el (role, status, permsOverride|None) efectivo
    de ese usuario tras el cambio. Invariante: nunca quedar sin admin."""
    users = await repository.list_users()
    roles = {r["id"]: r for r in await repository.list_roles()}
    for u in users:
        role_key, status, override = hypo(u, roles)
        if status == "disabled":
            continue
        perms = override if override is not None else (roles.get(role_key or "") or {}).get("permissions", {})
        if perms.get("admin.manage"):
            return True
    return False


# ── Users ─────────────────────────────────────────────────────────────────


async def list_users() -> list[dict]:
    return await repository.list_users()


async def create_user(actor: str, body) -> dict:
    username = body.username.strip()
    fields = {
        "email": body.email, "name": body.name, "role": body.role,
        "projectIds": body.projectIds, "status": body.status,
        "initials": initials(body.name), "passwordHash": hash_password(body.password),
    }
    user = await repository.upsert_user(username, fields)
    await audit(actor, "admin.user.create", target=username, target_type="user")
    return user


async def update_user(actor: str, username: str, body) -> dict | None:
    before = await repository.get_user(username)
    if before is None:
        return None
    # Guard: cambiar el rol o deshabilitar NO debe dejar sin administrador.
    if body.role is not None or body.status is not None:
        def hypo(u, roles):
            if u["id"] != username:
                return u.get("role"), u.get("status"), None
            return (body.role if body.role is not None else u.get("role"),
                    body.status if body.status is not None else u.get("status"), None)
        if not await _survives_admin(hypo):
            raise AdminGuardError("Ese cambio dejaría el sistema sin ningún administrador.")
    fields: dict = {}
    if body.email is not None:
        fields["email"] = body.email
    if body.name is not None:
        fields["name"] = body.name
        fields["initials"] = initials(body.name)
    if body.role is not None:
        fields["role"] = body.role
    if body.projectIds is not None:
        fields["projectIds"] = body.projectIds
    if body.status is not None:
        fields["status"] = body.status
    if body.password:
        fields["passwordHash"] = hash_password(body.password)
    user = await repository.upsert_user(username, fields)
    # Auditoría con VALORES (old→new) de los campos sensibles: una escalada de
    # rol o un disable quedan reconstruibles (antes sólo guardaba los nombres).
    changes = {}
    for key in ("role", "status"):
        if key in fields:
            changes[key] = {"from": before.get(key), "to": fields[key]}
    await audit(actor, "admin.user.update", target=username, target_type="user",
                meta={"changes": changes,
                      "fields": [k for k in fields if k not in ("passwordHash", "role", "status")],
                      "passwordChanged": "passwordHash" in fields})
    return user


async def delete_user(actor: str, username: str) -> bool:
    # Guard: no eliminar al último administrador.
    if not await _survives_admin(lambda u, roles: (
        u.get("role"), ("disabled" if u["id"] == username else u.get("status")), None)):
        raise AdminGuardError("No podés eliminar al último administrador del sistema.")
    ok = await repository.delete_user(username)
    if ok:
        await audit(actor, "admin.user.delete", target=username, target_type="user")
    return ok


# ── Roles ─────────────────────────────────────────────────────────────────


async def list_roles() -> list[dict]:
    return await repository.list_roles()


async def upsert_role(actor: str, key: str, body) -> dict:
    perms = sanitize_permissions(body.permissions)
    # Guard: quitarle admin.manage a un rol NO debe dejar sin administrador
    # (p.ej. destildar la celda administrador × admin.manage en la matriz).
    if not perms.get("admin.manage"):
        override_for_key = perms  # los usuarios de `key` usan estos permisos nuevos
        if not await _survives_admin(lambda u, roles: (
            u.get("role"), u.get("status"), override_for_key if u.get("role") == key else None)):
            raise AdminGuardError("Ese cambio en la matriz dejaría el sistema sin administrador.")
    fields = {"name": body.name, "description": body.description, "permissions": perms}
    role = await repository.upsert_role(key, fields)
    await audit(actor, "admin.role.update", target=key, target_type="role")
    return role


async def delete_role(actor: str, key: str) -> bool:
    # Guard: no borrar un rol que tiene usuarios asignados (quedarían sin
    # permisos, con un role= inexistente). Reasignalos primero.
    users = await repository.list_users()
    assigned = [u["id"] for u in users if u.get("role") == key]
    if assigned:
        raise AdminGuardError(
            f"El rol tiene {len(assigned)} usuario(s) asignado(s). Reasignalos antes de eliminarlo."
        )
    ok = await repository.delete_role(key)
    if ok:
        await audit(actor, "admin.role.delete", target=key, target_type="role")
    return ok


def permissions_catalog() -> list[str]:
    """Keys de permiso (columnas/filas de la matriz). Puro."""
    return list(PERMISSIONS)
