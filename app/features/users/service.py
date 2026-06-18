"""Lógica de negocio de `users` (gestión de usuarios por admin).

El hash de contraseña nunca se devuelve al cliente. Las mutaciones rechazan
operar sobre la propia cuenta del que llama cuando lo dejaría sin acceso
(borrar / degradar / deshabilitar).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.core.security.passwords import hash_password

from .models import PermissionDoc, UserDoc
from .repository import (
    create_user,
    delete_user,
    get_user_by_id,
    get_user_by_username,
    list_users,
    update_user,
)
from .schemas import CreateUserRequest, PermissionPayload, UpdateUserRequest

_VALID_ROLES = {"admin", "editor", "viewer"}
_VALID_LEVELS = {"view", "edit"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_user(user: UserDoc) -> dict:
    """Payload saneado — sin hash de contraseña."""
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


def _validate_role(role: str) -> None:
    if role not in _VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role '{role}'. Must be one of: {sorted(_VALID_ROLES)}.",
        )


def _validate_permissions(perms: list[PermissionPayload]) -> list[PermissionDoc]:
    out: list[PermissionDoc] = []
    for p in perms:
        if p.level not in _VALID_LEVELS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid permission level '{p.level}'.",
            )
        out.append(PermissionDoc(projectId=p.projectId, level=p.level))  # type: ignore[arg-type]
    return out


# ── Operaciones ────────────────────────────────────────────────────────────

async def list_all() -> list[dict]:
    return [public_user(u) for u in await list_users()]


async def create(body: CreateUserRequest) -> dict:
    username = body.username.strip()
    if not username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username is required.",
        )
    if not body.password or len(body.password) < 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 4 characters.",
        )
    _validate_role(body.role)
    permissions = _validate_permissions(body.permissions)

    existing = await get_user_by_username(username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{username}' is already taken.",
        )

    now = _now()
    user = UserDoc(
        id=str(uuid.uuid4()),
        username=username,
        passwordHash=hash_password(body.password),
        role=body.role,  # type: ignore[arg-type]
        isActive=body.isActive,
        permissions=permissions,
        createdAt=now,
        statusUpdatedAt=now,
    )
    created = await create_user(user)
    return public_user(created)


async def update(user_id: str, body: UpdateUserRequest, admin: UserDoc) -> dict:
    target = await get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    updates: dict = {}

    # Username — exige unicidad si cambió.
    if body.username is not None:
        new_username = body.username.strip()
        if not new_username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username cannot be empty.",
            )
        if new_username != target.username:
            clash = await get_user_by_username(new_username)
            if clash and clash.id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Username '{new_username}' is already taken.",
                )
            updates["username"] = new_username

    # Password — solo persiste si vino un valor no vacío.
    if body.password:
        if len(body.password) < 4:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 4 characters.",
            )
        updates["passwordHash"] = hash_password(body.password)

    # Role — evita que el llamante se degrade a sí mismo.
    if body.role is not None and body.role != target.role:
        _validate_role(body.role)
        if user_id == admin.id and body.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot change your own role.",
            )
        updates["role"] = body.role

    # isActive — evita que el llamante se deshabilite a sí mismo.
    if body.isActive is not None and body.isActive != target.isActive:
        if user_id == admin.id and not body.isActive:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot disable your own account.",
            )
        updates["isActive"] = body.isActive
        updates["statusUpdatedAt"] = _now()

    # Permissions — reemplazo total cuando se provee.
    if body.permissions is not None:
        permissions = _validate_permissions(body.permissions)
        updates["permissions"] = [p.model_dump() for p in permissions]

    if not updates:
        return public_user(target)

    updated = await update_user(user_id, updates)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return public_user(updated)


async def delete(user_id: str, admin: UserDoc) -> dict:
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account.",
        )
    deleted = await delete_user(user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return {"deleted": True}
