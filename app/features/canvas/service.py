"""Lógica de negocio de `canvas` (acceso por proyecto + reemplazo/posiciones)."""

from __future__ import annotations

from fastapi import HTTPException, status

from app.features.auth import check_project_access
from app.features.projects import get_project

from .repository import bulk_update_positions, get_canvas, replace_canvas
from .schemas import CanvasReplaceRequest, PatchPositionsRequest


async def read(project_id: str, user) -> dict:
    await check_project_access(user, project_id, "view")
    project = await get_project(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return await get_canvas(project_id)


async def replace(project_id: str, body: CanvasReplaceRequest, user) -> dict:
    await check_project_access(user, project_id, "edit")
    if body.tables is None and body.relationships is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one of `tables` or `relationships`.",
        )
    updated = await replace_canvas(project_id, tables=body.tables, relationships=body.relationships)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return updated


async def patch_positions(project_id: str, body: PatchPositionsRequest, user) -> dict:
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
    return await bulk_update_positions(project_id, table_positions)
