"""Lógica de negocio de `projects` (acceso por rol/permiso + CRUD)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.features.auth import check_project_access, is_admin, visible_project_ids

from .models import ProjectDoc
from .repository import (
    create_project,
    delete_project,
    generate_project_id,
    get_project,
    get_projects,
    update_project,
)
from .schemas import CreateProjectRequest, UpdateProjectRequest


async def list_for_user(user) -> list[dict]:
    projects = await get_projects()
    if is_admin(user):
        return [p.model_dump() for p in projects]
    visible = visible_project_ids(user)
    return [p.model_dump() for p in projects if p.id in visible]


async def create(body: CreateProjectRequest) -> dict:
    if not body.name or not body.name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project name is required.",
        )
    now = datetime.now(timezone.utc).isoformat()
    project = ProjectDoc.model_validate(
        {
            "id": await generate_project_id(body.name.strip()),
            "name": body.name.strip(),
            "description": body.description.strip() if body.description else None,
            "engines": body.engines or [],
            "layers": body.layers or [],
            "domains": body.domains or [],
            "createdAt": now,
            "updatedAt": now,
        }
    )
    created = await create_project(project)
    return created.model_dump()


async def get_one(project_id: str, user) -> dict:
    await check_project_access(user, project_id, "view")
    project = await get_project(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return project.model_dump()


async def update(project_id: str, body: UpdateProjectRequest, user) -> dict:
    await check_project_access(user, project_id, "edit")
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update.",
        )
    updated = await update_project(project_id, updates)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return updated.model_dump()


async def delete(project_id: str) -> dict:
    deleted = await delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return {"deleted": True}
