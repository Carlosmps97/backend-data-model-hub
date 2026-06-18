"""Endpoints CRUD de proyectos.

GET    /api/projects          — lista (filtrada por rol)
POST   /api/projects          — crea (admin)
GET    /api/projects/{id}     — obtiene (acceso view)
PUT    /api/projects/{id}     — actualiza (acceso edit)
DELETE /api/projects/{id}     — borra + cascada (admin)
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core.api.envelope import ok
from app.features.auth import AdminDep, AuthUserDep

from . import service
from .schemas import CreateProjectRequest, UpdateProjectRequest

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
async def list_projects(user: AuthUserDep):
    return ok(await service.list_for_user(user))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_new_project(body: CreateProjectRequest, user: AdminDep):
    return ok(await service.create(body))


@router.get("/{project_id}")
async def get_one_project(project_id: str, user: AuthUserDep):
    return ok(await service.get_one(project_id, user))


@router.put("/{project_id}")
async def update_one_project(project_id: str, body: UpdateProjectRequest, user: AuthUserDep):
    return ok(await service.update(project_id, body, user))


@router.delete("/{project_id}")
async def delete_one_project(project_id: str, user: AdminDep):
    return ok(await service.delete(project_id))
