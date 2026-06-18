"""Endpoints de gestión de usuarios (admin-only).

GET    /api/admin/users          — lista todos los usuarios
POST   /api/admin/users          — crea usuario
PUT    /api/admin/users/{id}     — actualiza usuario
DELETE /api/admin/users/{id}     — hard-delete de usuario
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core.api.envelope import ok
from app.features.auth import AdminDep

from . import service
from .schemas import CreateUserRequest, UpdateUserRequest

router = APIRouter(prefix="/api/admin/users", tags=["admin"])


@router.get("")
async def list_all_users(_admin: AdminDep):
    return ok(await service.list_all())


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_new_user(body: CreateUserRequest, _admin: AdminDep):
    return ok(await service.create(body))


@router.put("/{user_id}")
async def update_existing_user(user_id: str, body: UpdateUserRequest, admin: AdminDep):
    return ok(await service.update(user_id, body, admin))


@router.delete("/{user_id}")
async def delete_existing_user(user_id: str, admin: AdminDep):
    return ok(await service.delete(user_id, admin))
