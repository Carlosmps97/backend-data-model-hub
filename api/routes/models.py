"""DataModel CRUD endpoints.

GET    /api/models                 — list (filter by ?projectId=… and ?full=true)
POST   /api/models                 — create (requires edit access on target project)
GET    /api/models/{id}            — fetch hydrated model (view access)
PUT    /api/models/{id}            — partial update (edit access)
DELETE /api/models/{id}            — soft-delete model + cascade children (edit access)

Mirrors the removed Next.js routes under
`web-data-model-hub/src/app/api/models/*`. Permission logic matches
`@/lib/auth/permissions.ts` on the frontend.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from src.api.dependencies import (
    AuthUserDep,
    can_edit_model,
    can_edit_project,
    can_view_model,
    is_admin,
    visible_project_ids,
)
from src.api.response_builder import ok
from src.db.db_models import DataModelDoc
from src.db.models_db import (
    create_model,
    delete_model,
    get_model,
    get_models,
    update_model,
)

router = APIRouter(prefix="/api/models", tags=["models"])


class CreateModelRequest(BaseModel):
    projectId: str
    name: str
    description: str | None = None
    engine: str | None = None
    tables: list[dict[str, Any]] | None = None
    relationships: list[dict[str, Any]] | None = None
    views: list[dict[str, Any]] | None = None
    domainCatalog: list[dict[str, Any]] | None = None


class UpdateModelRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    engine: str | None = None
    tables: list[dict[str, Any]] | None = None
    relationships: list[dict[str, Any]] | None = None
    views: list[dict[str, Any]] | None = None
    domainCatalog: list[dict[str, Any]] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── GET /api/models ────────────────────────────────────────────────────────

@router.get("")
async def list_models(
    user: AuthUserDep,
    projectId: str | None = Query(default=None),  # noqa: N803 — matches TS query key
    full: bool = Query(default=False),
):
    """List models. Non-admins only see models in projects they can access."""
    models = await get_models(project_id=projectId, full=full)
    if is_admin(user):
        return ok([m.model_dump(by_alias=True) for m in models])

    visible = visible_project_ids(user)
    filtered = [m for m in models if m.projectId in visible]
    return ok([m.model_dump(by_alias=True) for m in filtered])


# ── POST /api/models ───────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_new_model(body: CreateModelRequest, user: AuthUserDep):
    if not body.name or not body.name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Model name is required.",
        )
    if not body.projectId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="projectId is required.",
        )
    if not can_edit_project(user, body.projectId):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No edit access to project {body.projectId}.",
        )

    now = _now()
    model = DataModelDoc.model_validate(
        {
            "id": str(uuid.uuid4()),
            "projectId": body.projectId,
            "name": body.name.strip(),
            "description": body.description.strip() if body.description else None,
            "engine": body.engine,
            "tables": body.tables or [],
            "relationships": body.relationships or [],
            "views": body.views or [],
            "domainCatalog": body.domainCatalog or [],
            "createdAt": now,
            "updatedAt": now,
        }
    )
    created = await create_model(model)
    return ok(created.model_dump(by_alias=True))


# ── GET /api/models/{id} ───────────────────────────────────────────────────

@router.get("/{model_id}")
async def get_one_model(model_id: str, user: AuthUserDep):
    model = await get_model(model_id)
    if not model:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found.")
    if not can_view_model(user, model_id, model.projectId):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No view access to model {model_id}.",
        )
    return ok(model.model_dump(by_alias=True))


# ── PUT /api/models/{id} ───────────────────────────────────────────────────

@router.put("/{model_id}")
async def update_one_model(
    model_id: str,
    body: UpdateModelRequest,
    user: AuthUserDep,
):
    existing = await get_model(model_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found.")
    if not can_edit_model(user, model_id, existing.projectId):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No edit access to model {model_id}.",
        )

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update.",
        )

    updated = await update_model(model_id, updates)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found.")
    return ok(updated.model_dump(by_alias=True))


# ── DELETE /api/models/{id} ────────────────────────────────────────────────

@router.delete("/{model_id}")
async def delete_one_model(model_id: str, user: AuthUserDep):
    existing = await get_model(model_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found.")
    if not can_edit_model(user, model_id, existing.projectId):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No edit access to model {model_id}.",
        )
    deleted = await delete_model(model_id)
    return ok({"deleted": bool(deleted)})
