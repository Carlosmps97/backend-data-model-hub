"""Project CRUD endpoints.

GET    /api/projects          — list projects (filtered by role)
POST   /api/projects          — create project (admin-only)
GET    /api/projects/{id}     — get project (requires view access)
PUT    /api/projects/{id}     — update project (requires edit access)
DELETE /api/projects/{id}     — delete project + cascade (admin-only)

Mirrors web-data-model-hub/src/app/api/projects/route.ts and [id]/route.ts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from src.api.dependencies import (
    AdminDep,
    AuthUserDep,
    check_project_access,
    is_admin,
    visible_project_ids,
)
from src.api.response_builder import ok
from src.db.db_models import ProjectDoc
from src.db.projects_db import (
    create_project,
    delete_project,
    get_project,
    get_projects,
    update_project,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])


class CreateProjectRequest(BaseModel):
    name: str
    description: str | None = None


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    description: str | None = None


@router.get("")
async def list_projects(user: AuthUserDep):
    projects = await get_projects()
    if is_admin(user):
        return ok([p.model_dump() for p in projects])
    visible = visible_project_ids(user)
    filtered = [p for p in projects if p.id in visible]
    return ok([p.model_dump() for p in filtered])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_new_project(body: CreateProjectRequest, user: AdminDep):
    if not body.name or not body.name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project name is required.",
        )
    now = datetime.now(timezone.utc).isoformat()
    project = ProjectDoc(
        id=str(uuid.uuid4()),
        name=body.name.strip(),
        description=body.description.strip() if body.description else None,
        createdAt=now,
        updatedAt=now,
    )
    created = await create_project(project)
    return ok(created.model_dump())


@router.get("/{project_id}")
async def get_one_project(project_id: str, user: AuthUserDep):
    await check_project_access(user, project_id, "view")
    project = await get_project(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return ok(project.model_dump())


@router.put("/{project_id}")
async def update_one_project(project_id: str, body: UpdateProjectRequest, user: AuthUserDep):
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
    return ok(updated.model_dump())


@router.delete("/{project_id}")
async def delete_one_project(project_id: str, user: AdminDep):
    deleted = await delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return ok({"deleted": True})
