"""Admin user-management endpoints.

GET    /api/admin/users          — list all users (admin-only)
POST   /api/admin/users          — create user (admin-only)
PUT    /api/admin/users/{id}     — update user (admin-only)
DELETE /api/admin/users/{id}     — hard-delete user (admin-only)

Mirrors the contract consumed by the frontend `adminService` in
`web-data-model-hub/src/services/admin.service.ts` and the
`AuthUser` type in `web-data-model-hub/src/types/auth.ts`.

The password hash is never echoed back to clients. All mutating
endpoints refuse to operate on the caller's own account when the
change would lock them out (delete, demote, disable).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.api.auth import hash_password
from src.api.dependencies import AdminDep
from src.api.response_builder import ok
from src.db.db_models import PermissionDoc, UserDoc
from src.db.users_db import (
    create_user,
    delete_user,
    get_user_by_id,
    get_user_by_username,
    list_users,
    update_user,
)

router = APIRouter(prefix="/api/admin/users", tags=["admin"])


# ── Request / response schemas ────────────────────────────────────────────


class PermissionPayload(BaseModel):
    scope: str  # "project" | "model"
    projectId: str
    modelId: str | None = None
    level: str  # "view" | "edit"


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str  # "admin" | "editor" | "viewer"
    isActive: bool = True
    permissions: list[PermissionPayload] = Field(default_factory=list)


class UpdateUserRequest(BaseModel):
    username: str | None = None
    password: str | None = None
    role: str | None = None
    isActive: bool | None = None
    permissions: list[PermissionPayload] | None = None


# ── Helpers ───────────────────────────────────────────────────────────────


_VALID_ROLES = {"admin", "editor", "viewer"}
_VALID_LEVELS = {"view", "edit"}
_VALID_SCOPES = {"project", "model"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_user(user: UserDoc) -> dict:
    """Sanitized payload — no password hash."""
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
        if p.scope not in _VALID_SCOPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid permission scope '{p.scope}'.",
            )
        if p.level not in _VALID_LEVELS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid permission level '{p.level}'.",
            )
        if p.scope == "model" and not p.modelId:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="modelId is required for model-scope permissions.",
            )
        out.append(
            PermissionDoc(
                scope=p.scope,  # type: ignore[arg-type]
                projectId=p.projectId,
                modelId=p.modelId if p.scope == "model" else None,
                level=p.level,  # type: ignore[arg-type]
            )
        )
    return out


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.get("")
async def list_all_users(_admin: AdminDep):
    users = await list_users()
    return ok([_public_user(u) for u in users])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_new_user(body: CreateUserRequest, _admin: AdminDep):
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
    return ok(_public_user(created))


@router.put("/{user_id}")
async def update_existing_user(
    user_id: str,
    body: UpdateUserRequest,
    admin: AdminDep,
):
    target = await get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    updates: dict = {}

    # Username — enforce uniqueness if it changed.
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

    # Password — only persist if a non-empty value was sent.
    if body.password:
        if len(body.password) < 4:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 4 characters.",
            )
        updates["passwordHash"] = hash_password(body.password)

    # Role — guard against the caller demoting themselves.
    if body.role is not None and body.role != target.role:
        _validate_role(body.role)
        if user_id == admin.id and body.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot change your own role.",
            )
        updates["role"] = body.role

    # isActive — guard against the caller disabling themselves.
    if body.isActive is not None and body.isActive != target.isActive:
        if user_id == admin.id and not body.isActive:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot disable your own account.",
            )
        updates["isActive"] = body.isActive
        updates["statusUpdatedAt"] = _now()

    # Permissions — replace wholesale when provided.
    if body.permissions is not None:
        permissions = _validate_permissions(body.permissions)
        updates["permissions"] = [p.model_dump() for p in permissions]

    if not updates:
        return ok(_public_user(target))

    updated = await update_user(user_id, updates)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return ok(_public_user(updated))


@router.delete("/{user_id}")
async def delete_existing_user(user_id: str, admin: AdminDep):
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account.",
        )
    deleted = await delete_user(user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return ok({"deleted": True})
