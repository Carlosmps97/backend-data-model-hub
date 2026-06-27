"""Project canvas endpoints (tables + relationships).

GET   /api/projects/{id}/canvas      — hydrated canvas (view access)
PUT   /api/projects/{id}/canvas      — replace tables/relationships (edit access)
PATCH /api/projects/{id}/positions   — fast layout persistence (edit access)

The project's `engines` / `layers` (ModelLevels) / `domains` are small arrays
embedded in the project document and are managed through `PUT /api/projects/{id}`
(see `api/routes/projects.py`). This router owns only the heavy child data.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from src.api.dependencies import AuthUserDep, check_project_access
from src.api.response_builder import ok
from src.db.canvas_db import bulk_update_positions, get_canvas, replace_canvas
from src.db.projects_db import get_project

router = APIRouter(prefix="/api/projects", tags=["canvas"])


class CanvasReplaceRequest(BaseModel):
    tables: list[dict[str, Any]] | None = None
    relationships: list[dict[str, Any]] | None = None


class PositionPayload(BaseModel):
    x: float
    y: float


class PatchPositionsRequest(BaseModel):
    tables: dict[str, PositionPayload] | None = None


# ── GET /api/projects/{id}/canvas ──────────────────────────────────────────

@router.get("/{project_id}/canvas")
async def get_project_canvas(project_id: str, user: AuthUserDep):
    await check_project_access(user, project_id, "view")
    project = await get_project(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    canvas = await get_canvas(project_id)
    return ok(canvas)


# ── PUT /api/projects/{id}/canvas ──────────────────────────────────────────

@router.put("/{project_id}/canvas")
async def replace_project_canvas(
    project_id: str,
    body: CanvasReplaceRequest,
    user: AuthUserDep,
):
    await check_project_access(user, project_id, "edit")
    if body.tables is None and body.relationships is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one of `tables` or `relationships`.",
        )
    updated = await replace_canvas(project_id, tables=body.tables, relationships=body.relationships)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return ok(updated)


# ── PATCH /api/projects/{id}/positions ─────────────────────────────────────

@router.patch("/{project_id}/positions")
async def patch_positions(
    project_id: str,
    body: PatchPositionsRequest,
    user: AuthUserDep,
):
    """Lightweight endpoint dedicated to canvas layout persistence. Separate
    from PUT /canvas so a debounced drag handler doesn't round-trip the full
    (possibly MB-sized) canvas payload."""
    await check_project_access(user, project_id, "edit")
    table_positions = (
        {tid: pos.model_dump() for tid, pos in body.tables.items()}
        if body.tables
        else None
    )
    if not table_positions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide `tables` positions to update.",
        )
    counts = await bulk_update_positions(project_id, table_positions)
    return ok(counts)
