"""Endpoints del catálogo transversal UDP / Semantic Type.

GET  lectura (cualquier usuario auth) · POST/PUT/DELETE escritura (editor/admin).
Potencian la sección "Metadata" del Model Center (Feature 3).
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core.api.envelope import ok
from app.features.auth import AuthUserDep, EditorDep

from . import service
from .schemas import SemanticTypeBody, UdpBody

router = APIRouter(prefix="/api/metadata", tags=["metadata"])


# ── Semantic Types ───────────────────────────────────────────────────────────

@router.get("/semantic-types")
async def list_semantic_types(user: AuthUserDep):
    return ok(await service.list_semantic_types())


@router.post("/semantic-types", status_code=status.HTTP_201_CREATED)
async def create_semantic_type(body: SemanticTypeBody, user: EditorDep):
    return ok(await service.create_semantic_type(body))


@router.put("/semantic-types/{st_id}")
async def update_semantic_type(st_id: str, body: SemanticTypeBody, user: EditorDep):
    return ok(await service.update_semantic_type(st_id, body))


@router.delete("/semantic-types/{st_id}")
async def delete_semantic_type(st_id: str, user: EditorDep):
    return ok(await service.delete_semantic_type(st_id))


# ── UDPs ─────────────────────────────────────────────────────────────────────

@router.get("/udps")
async def list_udps(user: AuthUserDep):
    return ok(await service.list_udps())


@router.post("/udps", status_code=status.HTTP_201_CREATED)
async def create_udp(body: UdpBody, user: EditorDep):
    return ok(await service.create_udp(body))


@router.put("/udps/{udp_id}")
async def update_udp(udp_id: str, body: UdpBody, user: EditorDep):
    return ok(await service.update_udp(udp_id, body))


@router.delete("/udps/{udp_id}")
async def delete_udp(udp_id: str, user: EditorDep):
    return ok(await service.delete_udp(udp_id))
