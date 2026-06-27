"""UDP / Semantic Type catalog endpoints (transversal — cross-project).

GET    /api/metadata/semantic-types        — list (any auth user)
POST   /api/metadata/semantic-types        — create (editor/admin)
PUT    /api/metadata/semantic-types/{id}   — update (editor/admin)
DELETE /api/metadata/semantic-types/{id}   — delete (editor/admin)
GET    /api/metadata/udps                   — list (any auth user)
POST   /api/metadata/udps                   — create / PUT update / DELETE (editor/admin)

These power the Model Center "Metadata" admin section (Feature 3). Reads are
open to any authenticated user; writes require a non-viewer role.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from src.api.dependencies import AuthUserDep, EditorDep
from src.api.response_builder import ok
from src.db import metadata_db

router = APIRouter(prefix="/api/metadata", tags=["metadata"])


class SemanticTagBody(BaseModel):
    key: str
    allowedValues: list[str] = []


class SemanticTypeBody(BaseModel):
    name: str
    dataType: str
    description: str | None = None
    tags: list[SemanticTagBody] = []


class UdpBody(BaseModel):
    name: str
    description: str | None = None
    appliesTo: list[str] = []
    valueType: str = "text"
    allowedValues: list[str] | None = None


# ── Semantic Types ───────────────────────────────────────────────────────────

@router.get("/semantic-types")
async def list_semantic_types(user: AuthUserDep):
    return ok(await metadata_db.list_semantic_types())


@router.post("/semantic-types", status_code=status.HTTP_201_CREATED)
async def create_semantic_type(body: SemanticTypeBody, user: EditorDep):
    if not body.name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name is required.")
    return ok(await metadata_db.create_semantic_type(body.model_dump()))


@router.put("/semantic-types/{st_id}")
async def update_semantic_type(st_id: str, body: SemanticTypeBody, user: EditorDep):
    updated = await metadata_db.update_semantic_type(st_id, body.model_dump())
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Semantic Type not found.")
    return ok(updated)


@router.delete("/semantic-types/{st_id}")
async def delete_semantic_type(st_id: str, user: EditorDep):
    deleted = await metadata_db.delete_semantic_type(st_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Semantic Type not found.")
    return ok({"deleted": True})


# ── UDPs ─────────────────────────────────────────────────────────────────────

@router.get("/udps")
async def list_udps(user: AuthUserDep):
    return ok(await metadata_db.list_udps())


@router.post("/udps", status_code=status.HTTP_201_CREATED)
async def create_udp(body: UdpBody, user: EditorDep):
    if not body.name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name is required.")
    return ok(await metadata_db.create_udp(body.model_dump()))


@router.put("/udps/{udp_id}")
async def update_udp(udp_id: str, body: UdpBody, user: EditorDep):
    updated = await metadata_db.update_udp(udp_id, body.model_dump())
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="UDP not found.")
    return ok(updated)


@router.delete("/udps/{udp_id}")
async def delete_udp(udp_id: str, user: EditorDep):
    deleted = await metadata_db.delete_udp(udp_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="UDP not found.")
    return ok({"deleted": True})
